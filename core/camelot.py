"""Roda Camelot: converte tons para o código Camelot e diz o que mixa com o quê.

Camelot numera os 24 tons como um relógio (1-12) com A = menor e B = maior.
Vizinhos na roda compartilham quase todas as notas, então a transição soa
"natural": mesmo código, ±1 no número (mesma letra) ou a troca A<->B do mesmo
número (relativo maior/menor).
"""

import re
from dataclasses import dataclass
from typing import Optional

NOTE_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
PC_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]

CAMELOT_RE = re.compile(r"^\s*(1[0-2]|0?[1-9])\s*([AB])\s*$", re.I)
OPEN_KEY_RE = re.compile(r"^\s*(1[0-2]|0?[1-9])\s*([MD])\s*$", re.I)  # Traktor
KEY_RE = re.compile(
    r"^\s*([A-G])\s*([#♯b♭]|sharp|flat)?\s*"
    r"(m|min|minor|maj|major|moll|dur)?\s*$", re.I)


def camelot_from_pitch(pc: int, minor: bool) -> str:
    major_pc = (pc + 3) % 12 if minor else pc  # menor -> relativo maior
    number = ((7 * major_pc) % 12 + 7) % 12 + 1
    return f"{number}{'A' if minor else 'B'}"


def to_camelot(text: Optional[str]) -> Optional[str]:
    """'Am', 'A minor', 'F#', 'Dbm', '8A', '8m' (Open Key) ... -> '8A'."""
    if not text:
        return None
    text = text.strip().replace("♯", "#").replace("♭", "b")
    m = CAMELOT_RE.match(text)
    if m:
        return f"{int(m.group(1))}{m.group(2).upper()}"
    m = OPEN_KEY_RE.match(text)
    if m:  # Open Key: 1d = C maior = 8B; 1m = Am = 8A
        number = (int(m.group(1)) + 6) % 12 + 1
        return f"{number}{'A' if m.group(2).lower() == 'm' else 'B'}"
    # Notação "A / Am" com espaço ou vírgula (ex. "Am/C") -> usa a primeira.
    text = re.split(r"[/,]", text)[0]
    m = KEY_RE.match(text)
    if not m:
        return None
    pc = NOTE_PC[m.group(1).upper()]
    accidental = (m.group(2) or "").lower()
    if accidental in ("#", "sharp"):
        pc += 1
    elif accidental in ("b", "flat"):
        pc -= 1
    quality = m.group(3) or ""
    # "m" sozinho é menor; "M" sozinho (maiúsculo) é convenção de maior.
    minor = quality.lower() in ("min", "minor", "moll") or quality == "m"
    return camelot_from_pitch(pc % 12, minor)


def camelot_label(code: str) -> str:
    """'8A' -> 'Am' (nome tradicional, pra quem não pensa em Camelot)."""
    number, letter = int(code[:-1]), code[-1]
    major_pc = (7 * (number - 8)) % 12  # inverso de camelot_from_pitch
    if letter == "A":
        return f"{PC_NAMES[(major_pc - 3) % 12]}m"
    return PC_NAMES[major_pc]


def _split(code: str) -> tuple[int, str]:
    return int(code[:-1]), code[-1]


@dataclass(frozen=True)
class Match:
    score: float          # 0..1, maior = mixa melhor
    key_reason: str       # "mesmo tom", "+1 (sobe energia)", ...
    bpm_reason: str       # "mesmo BPM", "+2.3%", "meio-tempo" ...
    harmonic: bool


def key_relation(a: Optional[str], b: Optional[str]) -> tuple[float, str]:
    """Quão bem o tom `b` vem depois do tom `a`."""
    if not a or not b:
        return 0.4, "tom desconhecido"
    (na, la), (nb, lb) = _split(a), _split(b)
    step = (nb - na) % 12
    if la == lb:
        if step == 0:
            return 1.0, "mesmo tom"
        if step == 1:
            return 0.9, "+1 (sobe energia)"
        if step == 11:
            return 0.9, "−1 (desce energia)"
        if step == 2:
            return 0.6, "+2 (energy boost)"
        if step == 7:
            return 0.55, "+7 (sobe 1 semitom)"
    elif step == 0:
        return 0.85, "relativo maior/menor"
    elif (la == "A" and step == 1) or (la == "B" and step == 11):
        return 0.5, "diagonal"
    return 0.0, "choque de tom"


def bpm_relation(a: Optional[float], b: Optional[float]) -> tuple[float, str]:
    if not a or not b:
        return 0.4, "BPM desconhecido"
    best = None
    # factor reescala b pra perto de a: b*0.5 ≈ a quer dizer que b é o dobro.
    for factor, label in ((1, ""), (0.5, "dobro"), (2, "meio-tempo")):
        diff = (b * factor - a) / a
        if best is None or abs(diff) < abs(best[0]):
            best = (diff, label)
    diff, label = best
    pct = abs(diff) * 100
    score = max(0.0, 1 - pct / 8)  # 0% = 1.0, 8% de pitch ou mais = 0
    if label:
        return score * 0.8, label
    if pct < 0.5:
        return score, "mesmo BPM"
    return score, f"{'+' if diff > 0 else '−'}{pct:.1f}%"


def match(a_key, a_bpm, b_key, b_bpm) -> Match:
    k_score, k_reason = key_relation(a_key, b_key)
    b_score, b_reason = bpm_relation(a_bpm, b_bpm)
    return Match(score=0.6 * k_score + 0.4 * b_score, key_reason=k_reason,
                 bpm_reason=b_reason, harmonic=k_score >= 0.85)


def harmonic_order(tracks: list, key=lambda t: t["camelot"], bpm=lambda t: t["bpm"]) -> list:
    """Reordena um set: começa pela primeira faixa e sempre escolhe, entre as
    que sobraram, a que melhor mixa com a atual (guloso, mas bom na prática)."""
    if len(tracks) < 3:
        return list(tracks)
    remaining = list(tracks[1:])
    ordered = [tracks[0]]
    while remaining:
        cur = ordered[-1]
        nxt = max(remaining, key=lambda t: match(key(cur), bpm(cur), key(t), bpm(t)).score)
        remaining.remove(nxt)
        ordered.append(nxt)
    return ordered


# Cores por posição na roda (como os softwares de DJ fazem): vizinhos têm
# cores vizinhas, então dá pra "ver" a compatibilidade na lista.
WHEEL_COLORS = ["#56C6A9", "#7ED957", "#B8E04A", "#F2D544", "#F6A93B", "#F47C4A",
                "#EF5A6F", "#E25AA8", "#B565D9", "#8A7CF0", "#5C9BF2", "#3DBFD9"]


def camelot_color(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    return WHEEL_COLORS[(_split(code)[0] - 1) % 12]
