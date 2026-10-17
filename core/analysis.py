"""BPM e tom de um arquivo de áudio.

1. Tags do arquivo primeiro: muita faixa do Soulseek já vem marcada pelo Mixed
   In Key / rekordbox (TBPM/TKEY, "bpm"/"initialkey"), e isso é mais confiável
   que qualquer estimativa.
2. Senão, análise do áudio só com numpy (sem librosa, que é pesado demais pra
   empacotar no app):
   - BPM: envelope de ataques (fluxo espectral) -> autocorrelação, refinada
     somando vários múltiplos do período pra chegar a décimos de BPM.
   - Tom: cromagrama (energia por nota) comparado com os perfis de
     Krumhansl-Kessler das 24 tonalidades.
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np

from core.camelot import camelot_from_pitch, to_camelot

SR = 11025                  # tudo é reamostrado pra cá: sobra pra ritmo e tom
SKIP_SECONDS = 20           # pula a intro (geralmente só bumbo ou silêncio)
MAX_SECONDS = 120
BPM_MIN, BPM_MAX = 70.0, 180.0

KK_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KK_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


@dataclass
class TrackAnalysis:
    bpm: Optional[float]
    camelot: Optional[str]
    source: str              # "tags", "áudio" ou "tags+áudio"


# ============================== TAGS ==============================

def _first(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if hasattr(value, "text"):          # frame ID3
        value = value.text[0] if value.text else None
    if isinstance(value, bytes):        # MP4 freeform
        value = value.decode("utf-8", "ignore")
    return str(value).strip() if value is not None else None


def read_tags(path: str) -> tuple[Optional[float], Optional[str]]:
    try:
        import mutagen
        audio = mutagen.File(path)
    except Exception:
        return None, None
    if audio is None or audio.tags is None:
        return None, None
    tags = audio.tags
    bpm_raw = key_raw = None
    for name in ("TBPM", "bpm", "BPM", "tmpo", "----:com.apple.iTunes:BPM"):
        try:
            bpm_raw = bpm_raw or _first(tags.get(name))
        except Exception:
            pass
    for name in ("TKEY", "initialkey", "INITIALKEY", "key", "KEY",
                 "----:com.apple.iTunes:initialkey"):
        try:
            key_raw = key_raw or _first(tags.get(name))
        except Exception:
            pass
    bpm = None
    try:
        value = float(str(bpm_raw).replace(",", "."))
        if 40 <= value <= 250:
            bpm = round(value, 1)
    except (TypeError, ValueError):
        pass
    return bpm, to_camelot(key_raw)


# ============================== ÁUDIO ==============================

def load_mono(path: str, skip: float = SKIP_SECONDS, seconds: float = MAX_SECONDS) -> np.ndarray:
    import soundfile as sf
    with sf.SoundFile(path) as f:
        native = f.samplerate
        total = f.frames / native if f.frames > 0 else 0
        start = skip if total > skip + 30 else 0  # faixa curta: usa do começo
        f.seek(int(start * native))
        data = f.read(int(seconds * native), dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    # Reamostragem simples por média de blocos (o filtro passa-baixa implícito
    # basta pra ritmo e tom). Taxas que não dividem exato caem pra interpolação.
    factor = native / SR
    if abs(factor - round(factor)) < 1e-6 and round(factor) >= 1:
        n = int(round(factor))
        mono = mono[: len(mono) // n * n].reshape(-1, n).mean(axis=1)
    else:
        positions = np.arange(0, len(mono) - 1, factor)
        mono = np.interp(positions, np.arange(len(mono)), mono).astype(np.float32)
    return mono


def _stft_mag(x: np.ndarray, n_fft: int, hop: int, batch: int = 2048) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    if len(x) < n_fft:
        x = np.pad(x, (0, n_fft - len(x)))
    frames = np.lib.stride_tricks.sliding_window_view(x, n_fft)[::hop]
    window = np.hanning(n_fft).astype(np.float32)
    out = np.empty((len(frames), n_fft // 2 + 1), dtype=np.float32)
    # Em lotes: a matriz inteira de janelas de uma faixa de 2 min passaria de 150 MB.
    for i in range(0, len(frames), batch):
        out[i:i + batch] = np.abs(np.fft.rfft(frames[i:i + batch] * window, axis=1))
    return out


def estimate_bpm(x: np.ndarray, sr: int = SR) -> Optional[float]:
    hop = 64
    fps = sr / hop
    mag = np.log1p(100 * _stft_mag(x, 1024, hop))
    flux = np.maximum(0, np.diff(mag, axis=0)).sum(axis=1)
    if len(flux) < fps * 8 or not flux.any():
        return None
    # Tira a tendência lenta (crescendos) pra sobrar só o "pulso".
    kernel = int(fps)
    flux = flux - np.convolve(flux, np.ones(kernel) / kernel, mode="same")
    flux = np.maximum(flux, 0)
    flux = (flux - flux.mean()) / (flux.std() + 1e-9)

    n = len(flux)
    spectrum = np.fft.rfft(flux, 2 * n)
    ac = np.fft.irfft(spectrum * np.conj(spectrum))[:n]
    ac /= ac[0] + 1e-9

    def ac_at(lag: float) -> float:
        i = int(lag)
        if i + 1 >= n:
            return 0.0
        frac = lag - i
        return (1 - frac) * ac[i] + frac * ac[i + 1]

    def score(bpm: float) -> float:
        period = 60 * fps / bpm
        hits = [ac_at(k * period) for k in range(1, 17) if k * period < n - 1]
        return float(np.mean(hits)) if hits else 0.0

    # Busca grossa (0.5 BPM), com leve preferência por 100-140 — onde a
    # maioria das pistas vive — pra desempatar dobro/metade.
    grid = np.arange(BPM_MIN, BPM_MAX, 0.5)
    prior = np.exp(-0.5 * (np.log2(grid / 124) / 0.9) ** 2)
    coarse = np.array([score(b) for b in grid]) * (0.85 + 0.15 * prior)
    best = float(grid[int(np.argmax(coarse))])
    # Busca fina (0.02 BPM) em volta do melhor.
    fine = np.arange(best - 0.6, best + 0.6, 0.02)
    best = float(fine[int(np.argmax([score(b) for b in fine]))])
    # Produção eletrônica quase sempre é BPM inteiro.
    return float(round(best)) if abs(best - round(best)) < 0.2 else round(best, 1)


def estimate_key(x: np.ndarray, sr: int = SR) -> Optional[str]:
    n_fft = 8192
    mag = _stft_mag(x, n_fft, n_fft // 2)
    if not mag.any():
        return None
    # Só picos tonais contam: bumbo e chimbal são ruído de banda larga e, sem
    # isso, espalham energia por todas as notas e achatam o cromagrama.
    width = 31
    local = np.apply_along_axis(
        lambda row: np.convolve(row, np.ones(width) / width, mode="same"), 1, mag)
    peaks = np.maximum(mag - 2 * local, 0)
    # Normaliza cada janela: trecho alto não manda sozinho no resultado.
    peaks = peaks / (peaks.sum(axis=1, keepdims=True) + 1e-9)
    spectrum = np.sqrt(peaks).sum(axis=0)
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    band = (freqs >= 55) & (freqs <= 1760)  # A1..A6: onde mora a harmonia
    midi = 69 + 12 * np.log2(freqs[band] / 440.0)
    pcs = np.round(midi).astype(int) % 12
    chroma = np.bincount(pcs, weights=spectrum[band], minlength=12)
    if not chroma.any():
        return None
    best_r, best = -2.0, None
    for pc in range(12):
        rolled = np.roll(chroma, -pc)
        for minor, profile in ((False, KK_MAJOR), (True, KK_MINOR)):
            r = float(np.corrcoef(rolled, profile)[0, 1])
            if r > best_r:
                best_r, best = r, camelot_from_pitch(pc, minor)
    return best


def analyze(path: str) -> TrackAnalysis:
    bpm, camelot = read_tags(path)
    if bpm and camelot:
        return TrackAnalysis(bpm, camelot, "tags")
    audio = load_mono(path)
    a_bpm = bpm or estimate_bpm(audio)
    a_key = camelot or estimate_key(audio)
    source = "tags+áudio" if (bpm or camelot) else "áudio"
    return TrackAnalysis(a_bpm, a_key, source)
