"""Busca e download de música no Soulseek, com foco em achar a melhor qualidade.

A rede Soulseek é P2P: cada resultado vem de um usuário real, que pode estar
lento, com fila cheia ou simplesmente offline no meio do download. Por isso este
módulo não é um "baixador" simples — ele é feito para insistir:

  1. Busca e coleta resultados por uma janela de tempo (eles chegam aos poucos).
  2. Pontua cada arquivo por qualidade real (formato, bitrate, sample rate) e por
     disponibilidade (slot livre, fila, velocidade do usuário).
  3. Baixa do melhor candidato e, se falhar/travar/enfileirar demais, cai
     automaticamente para o próximo da lista até conseguir.

Toda a rede roda em um loop asyncio próprio, numa thread daemon — a API pública
é síncrona e pode ser chamada direto da UI (Flet).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

# Numa conexão real, o aioslsk loga em WARNING toda desconexão de peer e todo
# resultado de busca que chega depois da janela de coleta fechar — isso é
# tráfego P2P normal, não erro, e sozinho já produz milhares de linhas por
# minuto. Silenciado aqui (não nos handlers, no logger em si) para que
# funcione não importa a ordem de import — inclusive antes do próprio
# aioslsk chamar `logging.basicConfig(DEBUG)` internamente (network/upnp.py).
logging.getLogger("aioslsk").setLevel(logging.ERROR)

try:
    from aioslsk.client import SoulSeekClient
    from aioslsk.settings import (
        Settings,
        CredentialsSettings,
        SharedDirectorySettingEntry,
    )
    from aioslsk.shares.model import DirectoryShareMode
    from aioslsk.events import (
        SearchResultEvent,
        SessionInitializedEvent,
        SessionDestroyedEvent,
    )
    from aioslsk.transfer.model import TransferState
    from aioslsk.protocol.primitives import AttributeKey
    from aioslsk.exceptions import AuthenticationError, ConnectionFailedError

    AIOSLSK_AVAILABLE = True
    IMPORT_ERROR = ""
except Exception as _e:  # pragma: no cover - só acontece sem a dependência
    AIOSLSK_AVAILABLE = False
    IMPORT_ERROR = str(_e)


# ============================== CONSTANTES ==============================

LOSSLESS_EXTS = {"flac", "wav", "aiff", "aif", "ape", "wv", "alac", "dsf", "shn"}
LOSSY_EXTS = {"mp3", "m4a", "aac", "ogg", "oga", "opus", "wma", "mp2", "mpc"}
AUDIO_EXTS = LOSSLESS_EXTS | LOSSY_EXTS

# Trechos de nome que quase sempre indicam arquivo inútil (prévia, vinheta).
JUNK_RE = re.compile(r"(preview|sample|snippet|teaser|jingle|\bdemo\b)", re.IGNORECASE)

# O Soulseek usa "\" como separador de pasta, mas nem todo cliente respeita.
PATH_SPLIT_RE = re.compile(r"[\\/]")

SERVER_DEFAULT = "server.slsknet.org"


def _data_dir() -> Path:
    """Pasta de dados do app, por sistema operacional (mesma do licensing)."""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home())
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    d = Path(base) / "MusicAggregator"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _default_download_dir() -> str:
    return str(Path.home() / "Music" / "Soulseek")


# ============================== CONFIGURAÇÃO ==============================


@dataclass
class SoulseekConfig:
    """Preferências de conexão, busca e download.

    Fica em <dados do app>/soulseek.json. A senha é gravada em texto puro nesse
    arquivo — é a conta do Soulseek, não use uma senha reaproveitada de e-mail.
    """

    username: str = ""
    password: str = ""
    download_dir: str = field(default_factory=_default_download_dir)
    # Compartilhar algo é praticamente obrigatório: muitos usuários banem
    # automaticamente quem não compartilha nada ("leechers").
    shared_dirs: list[str] = field(default_factory=list)
    share_download_dir: bool = True

    listening_port: int = 60000
    upload_slots: int = 3
    use_upnp: bool = True

    # Busca
    search_seconds: float = 12.0     # janela de coleta de resultados
    max_results: int = 400
    # Filtros de qualidade (aplicados na exibição, não na coleta)
    min_bitrate: int = 0
    only_free_slots: bool = False
    allowed_formats: list[str] = field(default_factory=list)  # vazio = todos
    prefer_lossless: bool = True

    # Download
    max_parallel: int = 2
    queue_timeout: float = 120.0     # tempo máximo esperando na fila do usuário
    stall_timeout: float = 45.0      # tempo máximo sem receber bytes novos
    max_attempts: int = 6            # candidatos diferentes tentados por faixa

    @classmethod
    def path(cls) -> Path:
        return _data_dir() / "soulseek.json"

    @classmethod
    def load(cls) -> "SoulseekConfig":
        cfg = cls()
        try:
            raw = json.loads(cls.path().read_text(encoding="utf-8"))
        except Exception:
            return cfg
        for key, value in raw.items():
            if hasattr(cfg, key) and value is not None:
                setattr(cfg, key, value)
        return cfg

    def save(self) -> None:
        data = {k: getattr(self, k) for k in self.__dataclass_fields__}
        try:
            self.path().write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def resolved_download_dir(self) -> Path:
        d = Path(self.download_dir or _default_download_dir())
        d.mkdir(parents=True, exist_ok=True)
        return d

    def effective_shared_dirs(self) -> list[str]:
        dirs = [d for d in self.shared_dirs if d and Path(d).exists()]
        if self.share_download_dir:
            dl = str(self.resolved_download_dir())
            if dl not in dirs:
                dirs.append(dl)
        return dirs


# ============================== MODELO ==============================


@dataclass
class Candidate:
    """Um arquivo específico, de um usuário específico, pronto para baixar."""

    username: str
    remote_path: str
    size: int = 0
    extension: str = ""
    bitrate: int = 0          # kbps informado pelo dono do arquivo
    duration: int = 0         # segundos
    sample_rate: int = 0      # Hz
    bit_depth: int = 0
    vbr: bool = False
    free_slots: bool = False
    upload_speed: int = 0     # bytes/s (média do usuário, informada pelo servidor)
    queue_size: int = 0       # quantas pessoas na frente
    locked: bool = False      # arquivo só liberado para amigos/privilegiados
    score: float = 0.0

    @property
    def filename(self) -> str:
        return PATH_SPLIT_RE.split(self.remote_path)[-1]

    @property
    def folder(self) -> str:
        parts = PATH_SPLIT_RE.split(self.remote_path)
        return "\\".join(parts[:-1]) if len(parts) > 1 else ""

    @property
    def folder_name(self) -> str:
        parts = PATH_SPLIT_RE.split(self.remote_path)
        return parts[-2] if len(parts) > 1 else ""

    @property
    def ext(self) -> str:
        if self.extension:
            return self.extension.lower().lstrip(".")
        _, _, tail = self.filename.rpartition(".")
        return tail.lower()

    @property
    def is_lossless(self) -> bool:
        return self.ext in LOSSLESS_EXTS

    @property
    def is_audio(self) -> bool:
        return self.ext in AUDIO_EXTS

    @property
    def effective_bitrate(self) -> int:
        """Bitrate informado ou, na falta dele, calculado por tamanho/duração."""
        if self.bitrate:
            return self.bitrate
        if self.duration > 0 and self.size > 0:
            return int((self.size * 8) / self.duration / 1000)
        return 0

    @property
    def size_mb(self) -> float:
        return self.size / (1024 * 1024)

    @property
    def duration_label(self) -> str:
        if not self.duration:
            return "--:--"
        m, s = divmod(int(self.duration), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    @property
    def quality_label(self) -> str:
        ext = self.ext.upper() or "?"
        if self.is_lossless:
            bits = []
            if self.sample_rate:
                bits.append(f"{self.sample_rate / 1000:g}kHz")
            if self.bit_depth:
                bits.append(f"{self.bit_depth}bit")
            return f"{ext} {' '.join(bits)}".strip()
        br = self.effective_bitrate
        if not br:
            return ext
        return f"{ext} {br}{'~' if self.vbr else ''}kbps"

    @property
    def speed_label(self) -> str:
        if not self.upload_speed:
            return "?"
        mbps = self.upload_speed / (1024 * 1024)
        if mbps >= 1:
            return f"{mbps:.1f} MB/s"
        return f"{self.upload_speed / 1024:.0f} KB/s"

    @property
    def key(self) -> tuple:
        return (self.username, self.remote_path)


@dataclass
class Release:
    """Vários arquivos do mesmo usuário na mesma pasta — um álbum/episódio."""

    username: str
    folder: str
    tracks: list[Candidate] = field(default_factory=list)

    @property
    def folder_name(self) -> str:
        return PATH_SPLIT_RE.split(self.folder)[-1] if self.folder else "(raiz)"

    @property
    def total_size(self) -> int:
        return sum(t.size for t in self.tracks)

    @property
    def score(self) -> float:
        return sum(t.score for t in self.tracks) / len(self.tracks) if self.tracks else 0.0

    @property
    def quality_label(self) -> str:
        return self.tracks[0].quality_label if self.tracks else "?"


# ============================== PONTUAÇÃO ==============================


def score_candidate(c: Candidate, cfg: Optional[SoulseekConfig] = None) -> float:
    """Nota de 0 a ~100 misturando qualidade do arquivo e chance real de baixar.

    Qualidade sozinha não serve: um FLAC de um usuário com 40 pessoas na fila e
    20 KB/s é pior, na prática, que um MP3 320 disponível na hora.
    """
    cfg = cfg or SoulseekConfig()
    score = 0.0

    # --- Qualidade do arquivo (0-60) ---
    if c.is_lossless:
        score += 52 if cfg.prefer_lossless else 42
        if c.sample_rate >= 88200 or c.bit_depth >= 24:
            score += 6          # alta resolução
        elif c.sample_rate >= 44100:
            score += 4          # CD quality
    else:
        br = c.effective_bitrate
        if br >= 320:
            score += 44
        elif br >= 256:
            score += 38
        elif br >= 192:
            score += 30
        elif br >= 128:
            score += 18
        elif br > 0:
            score += 6
        else:
            score += 20         # bitrate desconhecido: nota neutra
        if c.ext in ("opus", "m4a", "aac") and br >= 128:
            score += 3          # codecs modernos rendem mais por kbps

    # --- Disponibilidade (0-35) ---
    if c.free_slots:
        score += 20
    else:
        score -= min(c.queue_size, 40) * 0.4
    if c.upload_speed:
        score += min(c.upload_speed / (1024 * 1024), 5) * 2   # até +10

    # --- Penalidades ---
    if c.locked:
        score -= 40             # só sai se o dono liberar; deixa por último
    if JUNK_RE.search(c.filename):
        score -= 25
    if c.duration and c.duration < 45:
        score -= 20             # provavelmente um trecho, não a faixa inteira
    if c.size and c.size < 512 * 1024:
        score -= 15

    # --- Preferência explícita do usuário ---
    if cfg.allowed_formats and c.ext in [f.lower() for f in cfg.allowed_formats]:
        score += 5

    return round(score, 2)


def filter_candidates(
    candidates: Iterable[Candidate],
    cfg: SoulseekConfig,
    include_locked: bool = False,
) -> list[Candidate]:
    """Aplica os filtros do usuário e devolve a lista já ordenada pela nota."""
    allowed = {f.lower().lstrip(".") for f in cfg.allowed_formats}
    out = []
    for c in candidates:
        if not c.is_audio:
            continue
        if c.locked and not include_locked:
            continue
        if cfg.only_free_slots and not c.free_slots:
            continue
        if allowed and c.ext not in allowed:
            continue
        if cfg.min_bitrate and not c.is_lossless:
            if c.effective_bitrate and c.effective_bitrate < cfg.min_bitrate:
                continue
        out.append(c)
    out.sort(key=lambda x: x.score, reverse=True)
    return out


def group_releases(candidates: Iterable[Candidate]) -> list[Release]:
    """Agrupa por (usuário, pasta) — útil para baixar um álbum/episódio inteiro."""
    groups: dict[tuple, Release] = {}
    for c in candidates:
        k = (c.username, c.folder)
        if k not in groups:
            groups[k] = Release(username=c.username, folder=c.folder)
        groups[k].tracks.append(c)
    releases = list(groups.values())
    for r in releases:
        r.tracks.sort(key=lambda t: t.filename.lower())
    releases.sort(key=lambda r: (r.score, len(r.tracks)), reverse=True)
    return releases


def dedupe_by_file(candidates: Iterable[Candidate]) -> list[Candidate]:
    """Uma entrada por arquivo lógico (mesmo nome + tamanho parecido).

    Mantém o candidato de maior nota como principal — os outros continuam
    disponíveis como fallback via `alternatives_for`.
    """
    best: dict[tuple, Candidate] = {}
    for c in sorted(candidates, key=lambda x: x.score, reverse=True):
        k = (c.filename.lower(), round(c.size / (256 * 1024)))
        if k not in best:
            best[k] = c
    return sorted(best.values(), key=lambda x: x.score, reverse=True)


def parse_queries(raw) -> list[str]:
    """Aceita "a, b, c", várias linhas, ou uma lista — devolve nomes limpos.

    Vírgula e quebra de linha valem como separador, sem duplicatas e sem
    perder a ordem em que a pessoa digitou.
    """
    if isinstance(raw, str):
        pieces = re.split(r"[,\n;]+", raw)
    else:
        pieces = []
        for item in raw or []:
            pieces.extend(re.split(r"[,\n;]+", str(item)))

    out: list[str] = []
    seen = set()
    for piece in pieces:
        name = piece.strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def normalize_title(filename: str) -> str:
    """Nome comparável: sem extensão, número de faixa, tags de release nem pontuação."""
    stem = filename.rsplit(".", 1)[0].lower()
    stem = re.sub(r"^\s*\d{1,3}\s*[-._)\]]\s*", " ", stem)   # "01 - ", "03."
    stem = re.sub(r"[\[\(][^\]\)]*[\]\)]", " ", stem)         # "[FLAC]", "(2019)"
    stem = re.sub(r"[^a-z0-9]+", " ", stem)
    return " ".join(stem.split())


def _token_overlap(a: str, b: str) -> float:
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def alternatives_for(
    target: Candidate,
    pool: Iterable[Candidate],
    loose: bool = True,
) -> list[Candidate]:
    """Fontes alternativas para o mesmo conteúdo, em ordem de preferência.

    Em camadas, porque a chance de sucesso cai a cada degrau:
      1. Exatamente o mesmo arquivo em outro usuário (mesmo nome e tamanho).
      2. A mesma faixa em outro rip/formato (o FLAC morreu? cai pro MP3 320).
      3. Nome parecido — último recurso, só com `loose`.
    """
    pool = [c for c in pool if c.key != target.key and not c.locked]
    name = target.filename.lower()
    title = normalize_title(target.filename)

    def similar_size(c: Candidate) -> bool:
        if not target.size or not c.size:
            return True
        return abs(c.size - target.size) / max(target.size, 1) < 0.25

    tier1 = [c for c in pool if c.filename.lower() == name and similar_size(c)]
    seen = {c.key for c in tier1}

    tier2 = [
        c for c in pool
        if c.key not in seen and title and normalize_title(c.filename) == title
    ]
    seen |= {c.key for c in tier2}

    tier3 = []
    if loose and title:
        tier3 = [
            c for c in pool
            if c.key not in seen and _token_overlap(title, normalize_title(c.filename)) >= 0.6
        ]

    for tier in (tier1, tier2, tier3):
        tier.sort(key=lambda x: x.score, reverse=True)
    return tier1 + tier2 + tier3


# ============================== HISTÓRICO ==============================


class DownloadHistory:
    """Registro SQLite do que já foi baixado, para não repetir."""

    def __init__(self, db_path: Optional[Path] = None):
        self.path = db_path or (_data_dir() / "soulseek_history.db")
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        try:
            with sqlite3.connect(self.path) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS downloads (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        label       TEXT,
                        username    TEXT,
                        remote_path TEXT,
                        local_path  TEXT,
                        size        INTEGER,
                        quality     TEXT,
                        finished_at TEXT DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_remote ON downloads(username, remote_path)"
                )
        except Exception:
            pass

    def add(self, label: str, c: Candidate, local_path: str) -> None:
        with self._lock:
            try:
                with sqlite3.connect(self.path) as conn:
                    conn.execute(
                        "INSERT INTO downloads (label, username, remote_path, local_path, size, quality)"
                        " VALUES (?, ?, ?, ?, ?, ?)",
                        (label, c.username, c.remote_path, local_path, c.size, c.quality_label),
                    )
            except Exception:
                pass

    def has(self, c: Candidate) -> bool:
        with self._lock:
            try:
                with sqlite3.connect(self.path) as conn:
                    row = conn.execute(
                        "SELECT 1 FROM downloads WHERE username = ? AND remote_path = ? LIMIT 1",
                        (c.username, c.remote_path),
                    ).fetchone()
                    return row is not None
            except Exception:
                return False

    def recent(self, limit: int = 100) -> list[dict]:
        with self._lock:
            try:
                with sqlite3.connect(self.path) as conn:
                    conn.row_factory = sqlite3.Row
                    rows = conn.execute(
                        "SELECT * FROM downloads ORDER BY id DESC LIMIT ?", (limit,)
                    ).fetchall()
                    return [dict(r) for r in rows]
            except Exception:
                return []


# ============================== JOB DE DOWNLOAD ==============================


@dataclass
class DownloadJob:
    """Um alvo lógico (uma faixa) + a fila de candidatos usada como fallback."""

    label: str
    candidates: list[Candidate] = field(default_factory=list)
    state: str = "pending"      # pending|queued|downloading|complete|failed|cancelled
    detail: str = ""
    attempt: int = 0
    current: Optional[Candidate] = None
    bytes_done: int = 0
    size: int = 0
    speed: float = 0.0
    place_in_queue: int = 0
    local_path: str = ""
    error: str = ""
    cancelled: bool = False

    @property
    def progress(self) -> float:
        if not self.size:
            return 0.0
        return min(self.bytes_done / self.size, 1.0)

    @property
    def speed_label(self) -> str:
        if self.speed <= 0:
            return ""
        mbps = self.speed / (1024 * 1024)
        return f"{mbps:.1f} MB/s" if mbps >= 1 else f"{self.speed / 1024:.0f} KB/s"

    @property
    def is_done(self) -> bool:
        return self.state in ("complete", "failed", "cancelled")


# ============================== MOTOR ==============================


def _guard(callback: Optional[Callable]) -> Callable:
    """Envolve um callback externo para que ele nunca derrube quem o chamou."""
    if callback is None:
        return lambda *_args, **_kwargs: None

    def safe(*args, **kwargs):
        try:
            callback(*args, **kwargs)
        except Exception:
            pass

    return safe


class SoulseekEngine:
    """Fachada síncrona para o Soulseek. Toda a rede roda numa thread própria."""

    def __init__(
        self,
        config: Optional[SoulseekConfig] = None,
        log_callback: Optional[Callable[[str], None]] = None,
        on_status: Optional[Callable[[str, str], None]] = None,
    ):
        self.config = config or SoulseekConfig.load()
        # Callbacks vêm da UI e rodam dentro do loop de rede: se um deles
        # levantar exceção, ele derruba o download junto. Por isso, blindados.
        self.log = _guard(log_callback)
        self.on_status = _guard(on_status)
        self.history = DownloadHistory()

        self._client = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._connected = threading.Event()
        self._collectors: dict[int, "_ResultCollector"] = {}
        self._jobs: list[DownloadJob] = []
        self._jobs_lock = threading.Lock()
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._stopping = False

    # ---------- ciclo de vida ----------

    @property
    def available(self) -> bool:
        return AIOSLSK_AVAILABLE

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    def _ensure_loop(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        def runner():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._ready.set()
            try:
                self._loop.run_forever()
            finally:
                try:
                    self._loop.close()
                except Exception:
                    pass

        self._ready.clear()
        self._thread = threading.Thread(target=runner, name="soulseek-loop", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=10)

    def _run(self, coro, timeout: Optional[float] = None):
        """Executa uma corrotina no loop da rede e espera o resultado."""
        self._ensure_loop()
        if not self._loop:
            raise RuntimeError("Loop do Soulseek não iniciou")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def _spawn(self, coro):
        """Dispara uma corrotina no loop da rede sem esperar."""
        self._ensure_loop()
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def connect(self, timeout: float = 90.0) -> tuple[bool, str]:
        """Conecta e faz login. Devolve (ok, mensagem) — nunca levanta exceção."""
        if not AIOSLSK_AVAILABLE:
            return False, (
                "Biblioteca aioslsk não instalada. Rode: pip install aioslsk"
                + (f" ({IMPORT_ERROR})" if IMPORT_ERROR else "")
            )
        if not self.config.username or not self.config.password:
            return False, "Preencha usuário e senha do Soulseek."
        if self.is_connected:
            return True, "Já conectado."

        try:
            return self._run(self._connect_async(), timeout=timeout)
        except asyncio.TimeoutError:
            return False, "Tempo esgotado ao conectar no servidor do Soulseek."
        except Exception as e:
            return False, f"Falha ao conectar: {e}"

    def _build_settings(self):
        cfg = self.config
        settings = Settings(
            credentials=CredentialsSettings(
                username=cfg.username,
                password=cfg.password,
            )
        )
        # Reconexão automática: quedas de servidor são comuns numa sessão longa.
        settings.network.server.hostname = SERVER_DEFAULT
        settings.network.server.reconnect.auto = True
        settings.network.listening.port = int(cfg.listening_port)
        settings.network.listening.obfuscated_port = int(cfg.listening_port) + 1
        settings.network.upnp.enabled = bool(cfg.use_upnp)

        settings.shares.download = str(cfg.resolved_download_dir())
        settings.shares.scan_on_start = True
        settings.shares.directories = [
            SharedDirectorySettingEntry(path=d, share_mode=DirectoryShareMode.EVERYONE)
            for d in cfg.effective_shared_dirs()
        ]

        settings.searches.receive.max_results = int(cfg.max_results)
        settings.searches.receive.store_amount = max(int(cfg.max_results), 500)
        settings.transfers.limits.upload_slots = int(cfg.upload_slots)
        settings.transfers.report_interval = 0.5
        return settings

    async def _connect_async(self) -> tuple[bool, str]:
        settings = self._build_settings()
        self._client = SoulSeekClient(settings)
        self._semaphore = asyncio.Semaphore(max(1, int(self.config.max_parallel)))

        self._client.events.register(SearchResultEvent, self._on_search_result)
        self._client.events.register(SessionInitializedEvent, self._on_session_up)
        self._client.events.register(SessionDestroyedEvent, self._on_session_down)

        self.log("Conectando ao servidor do Soulseek...")
        try:
            await self._client.start()
            await self._client.login()
        except AuthenticationError:
            await self._safe_stop()
            return False, (
                "Usuário ou senha recusados. No Soulseek o nome é único: se já "
                "existir, a senha precisa ser a mesma. Tente outro nome de usuário."
            )
        except ConnectionFailedError as e:
            await self._safe_stop()
            return False, f"Não foi possível alcançar o servidor: {e}"
        except Exception as e:
            await self._safe_stop()
            return False, f"Erro no login: {e}"

        self._connected.set()
        shared = len(self.config.effective_shared_dirs())
        self.log(f"Conectado como '{self.config.username}' — {shared} pasta(s) compartilhada(s).")
        self.on_status("connected", f"Conectado como {self.config.username}")
        return True, "Conectado."

    async def _safe_stop(self) -> None:
        if self._client:
            try:
                await self._client.stop()
            except Exception:
                pass
        self._client = None
        self._connected.clear()

    def disconnect(self) -> None:
        if not self._client:
            return
        self._stopping = True
        try:
            self._run(self._safe_stop(), timeout=30)
        except Exception:
            pass
        finally:
            self._stopping = False
            self._connected.clear()
            self.on_status("disconnected", "Desconectado")
            self.log("Desconectado do Soulseek.")

    def shutdown(self) -> None:
        """Encerra tudo: cancela jobs, desconecta e para o loop."""
        for job in self.jobs():
            job.cancelled = True
        self.disconnect()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    # ---------- eventos ----------

    async def _on_session_up(self, event) -> None:
        self._connected.set()
        self.on_status("connected", f"Conectado como {self.config.username}")

    async def _on_session_down(self, event) -> None:
        self._connected.clear()
        if not self._stopping:
            self.log("Sessão caiu — o cliente vai tentar reconectar sozinho.")
            self.on_status("reconnecting", "Reconectando...")

    async def _on_search_result(self, event) -> None:
        collector = self._collectors.get(event.query.ticket)
        if collector is None:
            return
        try:
            collector.add(event.result)
        except Exception as e:
            self.log(f"Erro ao ler resultado de busca: {e}")

    # ---------- busca ----------

    def search(
        self,
        query: str,
        seconds: Optional[float] = None,
        on_partial: Optional[Callable[[list[Candidate]], None]] = None,
        stop_when: Optional[Callable[[list[Candidate]], bool]] = None,
    ) -> list[Candidate]:
        """Busca na rede e coleta resultados por uma janela de tempo.

        `on_partial` recebe a lista parcial (já pontuada e ordenada) a cada
        atualização, para a UI ir preenchendo enquanto os resultados chegam.
        `stop_when` permite encerrar antes do tempo (ex.: já achou 3 FLACs livres).
        """
        query = (query or "").strip()
        if not query:
            return []
        if not self.is_connected:
            ok, msg = self.connect()
            if not ok:
                self.log(f"Busca cancelada: {msg}")
                return []

        window = seconds if seconds is not None else self.config.search_seconds
        try:
            return self._run(
                self._search_async(query, window, on_partial, stop_when),
                timeout=window + 45,
            )
        except Exception as e:
            self.log(f"Erro na busca '{query}': {e}")
            return []

    async def _search_async(
        self,
        query: str,
        seconds: float,
        on_partial: Optional[Callable[[list[Candidate]], None]],
        stop_when: Optional[Callable[[list[Candidate]], bool]],
    ) -> list[Candidate]:
        request = await self._client.searches.search(query)
        collector = _ResultCollector(self.config)
        self._collectors[request.ticket] = collector
        self.log(f"Buscando '{query}' (coletando por {seconds:.0f}s)...")

        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds
        last_count = 0
        try:
            while loop.time() < deadline:
                await asyncio.sleep(0.4)
                if collector.count == last_count:
                    continue
                last_count = collector.count
                current = collector.ranked()
                if on_partial:
                    try:
                        on_partial(current)
                    except Exception:
                        pass
                if stop_when and stop_when(current):
                    self.log("Já há resultados bons o bastante — encerrando a busca.")
                    break
        finally:
            self._collectors.pop(request.ticket, None)
            try:
                self._client.searches.remove_request(request)
            except Exception:
                pass

        results = collector.ranked()
        self.log(
            f"'{query}': {len(results)} arquivo(s) de áudio de "
            f"{len({c.username for c in results})} usuário(s)."
        )
        return results

    def search_many(
        self,
        queries: Iterable[str],
        seconds: Optional[float] = None,
        on_partial: Optional[Callable[[str, list[Candidate]], None]] = None,
        stagger: float = 2.0,
    ) -> dict[str, list[Candidate]]:
        """Busca vários nomes de uma vez e devolve {nome: candidatos}.

        As buscas correm em paralelo, mas com um intervalo entre os disparos:
        o servidor do Soulseek limita quem manda busca em rajada, e ninguém
        quer tomar bloqueio temporário no meio da montagem do set.
        """
        wanted = parse_queries(queries)
        if not wanted:
            return {}
        if len(wanted) == 1:
            q = wanted[0]
            cb = (lambda results: on_partial(q, results)) if on_partial else None
            return {q: self.search(q, seconds=seconds, on_partial=cb)}

        if not self.is_connected:
            ok, msg = self.connect()
            if not ok:
                self.log(f"Busca cancelada: {msg}")
                return {q: [] for q in wanted}

        window = seconds if seconds is not None else self.config.search_seconds
        total = stagger * (len(wanted) - 1) + window
        self.log(f"Buscando {len(wanted)} nomes (~{total:.0f}s no total)...")
        try:
            return self._run(
                self._search_many_async(wanted, window, on_partial, stagger),
                timeout=total + 60,
            )
        except Exception as e:
            self.log(f"Erro na busca múltipla: {e}")
            return {q: [] for q in wanted}

    async def _search_many_async(
        self,
        queries: list[str],
        seconds: float,
        on_partial: Optional[Callable[[str, list[Candidate]], None]],
        stagger: float,
    ) -> dict[str, list[Candidate]]:
        results: dict[str, list[Candidate]] = {q: [] for q in queries}

        async def one(query: str, delay: float):
            if delay:
                await asyncio.sleep(delay)
            cb = (lambda found, q=query: on_partial(q, found)) if on_partial else None
            try:
                results[query] = await self._search_async(query, seconds, cb, None)
            except Exception as e:
                self.log(f"Erro ao buscar '{query}': {e}")

        await asyncio.gather(
            *(one(q, i * stagger) for i, q in enumerate(queries)),
            return_exceptions=True,
        )
        achou = sum(1 for v in results.values() if v)
        self.log(f"Busca múltipla concluída: {achou}/{len(queries)} nomes com resultado.")
        return results

    # ---------- downloads ----------

    def jobs(self) -> list[DownloadJob]:
        with self._jobs_lock:
            return list(self._jobs)

    def clear_finished_jobs(self) -> None:
        with self._jobs_lock:
            self._jobs = [j for j in self._jobs if not j.is_done]

    def enqueue(
        self,
        candidate: Candidate,
        pool: Optional[Iterable[Candidate]] = None,
        label: Optional[str] = None,
        on_update: Optional[Callable[[DownloadJob], None]] = None,
    ) -> DownloadJob:
        """Coloca um arquivo na fila, com fallback automático para outras fontes."""
        chain: list[Candidate] = [candidate]
        seen = {candidate.key}
        for alt in (alternatives_for(candidate, pool) if pool else []):
            if alt.key not in seen:
                seen.add(alt.key)
                chain.append(alt)
        job = DownloadJob(
            label=label or candidate.filename,
            candidates=chain[: max(1, self.config.max_attempts)],
            size=candidate.size,
        )
        with self._jobs_lock:
            self._jobs.append(job)
        self._spawn(self._process_job(job, on_update))
        return job

    def enqueue_release(
        self,
        release: Release,
        pool: Optional[Iterable[Candidate]] = None,
        on_update: Optional[Callable[[DownloadJob], None]] = None,
    ) -> list[DownloadJob]:
        """Baixa uma pasta inteira (álbum/episódio), faixa por faixa."""
        return [
            self.enqueue(track, pool=pool, label=f"{release.folder_name} / {track.filename}",
                         on_update=on_update)
            for track in release.tracks
        ]

    def search_and_download(
        self,
        query: str,
        on_update: Optional[Callable[[DownloadJob], None]] = None,
        seconds: Optional[float] = None,
        skip_known: bool = True,
    ) -> Optional[DownloadJob]:
        """Modo setlist: busca, escolhe o melhor e baixa — com fallback.

        É o caminho usado para uma lista de faixas de show: o usuário só passa
        "Artista - Música" e o motor resolve o resto.
        """
        results = self.search(query, seconds=seconds)
        pool = filter_candidates(results, self.config)
        if skip_known:
            pool = [c for c in pool if not self.history.has(c)]
        if not pool:
            self.log(f"Nada encontrado para '{query}' com os filtros atuais.")
            job = DownloadJob(label=query, state="failed", error="Sem resultados")
            with self._jobs_lock:
                self._jobs.append(job)
            if on_update:
                on_update(job)
            return job

        best = dedupe_by_file(pool)[0]
        return self.enqueue(best, pool=pool, label=query, on_update=on_update)

    def cancel(self, job: DownloadJob) -> None:
        """Pede o cancelamento.

        Só vira 'cancelled' de fato quando a transferência for abortada do outro
        lado — antes disso os bytes ainda podem estar chegando. A exceção é o
        job que nem começou: esse morre na hora.
        """
        job.cancelled = True
        if job.state == "pending":
            job.state = "cancelled"
            job.detail = "Cancelado antes de começar"
        else:
            job.detail = "Cancelando..."

    async def _process_job(
        self,
        job: DownloadJob,
        on_update: Optional[Callable[[DownloadJob], None]],
    ) -> None:
        """Rede como esta falha de formas criativas: nada aqui pode escapar sem
        marcar o job, senão ele fica preso em 'pendente' para sempre na tela."""
        try:
            await self._run_job(job, _guard(on_update))
        except asyncio.CancelledError:
            job.state = "cancelled"
            _guard(on_update)(job)
            raise
        except Exception as e:
            job.state = "failed"
            job.error = str(e)
            job.detail = f"Erro inesperado: {e}"
            self.log(f"[{job.label}] erro inesperado: {e}")
            _guard(on_update)(job)

    async def _run_job(
        self,
        job: DownloadJob,
        notify_cb: Callable[[DownloadJob], None],
    ) -> None:
        def notify():
            notify_cb(job)

        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(max(1, int(self.config.max_parallel)))

        async with self._semaphore:
            if job.cancelled:
                job.state = "cancelled"
                notify()
                return

            if self._client is None:
                job.state = "failed"
                job.detail = job.error = "Sem conexão com o Soulseek"
                notify()
                return

            for index, cand in enumerate(job.candidates, start=1):
                if job.cancelled:
                    job.state = "cancelled"
                    notify()
                    return

                job.attempt = index
                job.current = cand
                job.size = cand.size
                job.bytes_done = 0
                job.state = "queued"
                job.detail = f"Tentativa {index}/{len(job.candidates)} — {cand.username} ({cand.quality_label})"
                notify()
                self.log(f"[{job.label}] tentando {cand.username}: {cand.filename} ({cand.quality_label})")

                outcome, message = await self._try_candidate(job, cand, notify)
                if outcome == "complete":
                    job.state = "complete"
                    job.detail = f"{cand.quality_label} — de {cand.username}"
                    notify()
                    self.history.add(job.label, cand, job.local_path)
                    self.log(f"[{job.label}] ✅ concluído ({cand.quality_label}) → {job.local_path}")
                    return
                if outcome == "cancelled":
                    job.state = "cancelled"
                    notify()
                    return

                job.error = message
                self.log(f"[{job.label}] ⚠ {message} — indo para a próxima fonte.")

            job.state = "failed"
            job.detail = job.error or "Todas as fontes falharam"
            notify()
            self.log(f"[{job.label}] ❌ nenhuma fonte funcionou.")

    async def _try_candidate(self, job: DownloadJob, cand: Candidate, notify) -> tuple[str, str]:
        """Tenta uma fonte. Devolve (resultado, mensagem)."""
        transfer = None
        try:
            transfer = await self._client.transfers.download(cand.username, cand.remote_path)
        except Exception as e:
            return "failed", f"não foi possível pedir o arquivo ({e})"

        loop = asyncio.get_running_loop()
        queue_deadline = loop.time() + self.config.queue_timeout
        last_bytes = 0
        last_progress = loop.time()

        try:
            while True:
                if job.cancelled:
                    await self._abort(transfer)
                    return "cancelled", "cancelado"

                state = transfer.state.VALUE
                job.bytes_done = transfer.bytes_transfered or 0
                job.size = transfer.filesize or cand.size
                job.local_path = transfer.local_path or ""
                job.place_in_queue = transfer.place_in_queue or 0
                try:
                    job.speed = transfer.get_speed()
                except Exception:
                    job.speed = 0.0

                if state == TransferState.COMPLETE:
                    return "complete", ""

                if state == TransferState.FAILED:
                    return "failed", f"recusado por {cand.username} ({transfer.fail_reason or 'sem motivo'})"

                if state == TransferState.ABORTED:
                    return "failed", "transferência abortada"

                if state in (TransferState.DOWNLOADING, TransferState.INCOMPLETE):
                    if job.state != "downloading":
                        job.state = "downloading"
                        job.detail = f"Baixando de {cand.username}"
                    if job.bytes_done > last_bytes:
                        last_bytes = job.bytes_done
                        last_progress = loop.time()
                    elif loop.time() - last_progress > self.config.stall_timeout:
                        await self._abort(transfer)
                        return "failed", f"travou sem receber dados por {self.config.stall_timeout:.0f}s"
                else:
                    # QUEUED / INITIALIZING / VIRGIN
                    job.state = "queued"
                    job.detail = (
                        f"Na fila de {cand.username}"
                        + (f" (posição {job.place_in_queue})" if job.place_in_queue else "")
                    )
                    if loop.time() > queue_deadline:
                        await self._abort(transfer)
                        return "failed", f"fila de {cand.username} demorou demais"

                notify()
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            await self._abort(transfer)
            raise
        except Exception as e:
            await self._abort(transfer)
            return "failed", f"erro na transferência ({e})"

    async def _abort(self, transfer) -> None:
        if not transfer:
            return
        try:
            await self._client.transfers.abort(transfer)
        except Exception:
            pass


class _ResultCollector:
    """Acumula e deduplica resultados de uma busca enquanto eles chegam."""

    def __init__(self, cfg: SoulseekConfig):
        self.cfg = cfg
        self._items: dict[tuple, Candidate] = {}

    @property
    def count(self) -> int:
        return len(self._items)

    def add(self, result) -> None:
        entries = list(getattr(result, "shared_items", []) or [])
        for locked, items in ((False, entries), (True, list(getattr(result, "locked_results", []) or []))):
            for item in items:
                cand = _candidate_from_file_data(result, item, locked)
                if cand is None or not cand.is_audio:
                    continue
                cand.score = score_candidate(cand, self.cfg)
                existing = self._items.get(cand.key)
                if existing is None or cand.score > existing.score:
                    self._items[cand.key] = cand

    def ranked(self) -> list[Candidate]:
        return sorted(self._items.values(), key=lambda c: c.score, reverse=True)


def _candidate_from_file_data(result, item, locked: bool) -> Optional[Candidate]:
    """Converte um FileData do protocolo em Candidate."""
    try:
        attrs = {}
        for attribute in (getattr(item, "attributes", None) or []):
            attrs[getattr(attribute, "key", None)] = getattr(attribute, "value", 0)

        def attr(key: "AttributeKey") -> int:
            # A chave chega como int puro em alguns clientes e como enum em outros.
            return int(attrs.get(key, attrs.get(getattr(key, "value", key), 0)) or 0)

        return Candidate(
            username=result.username,
            remote_path=item.filename,
            size=int(getattr(item, "filesize", 0) or 0),
            extension=(getattr(item, "extension", "") or ""),
            bitrate=attr(AttributeKey.BITRATE),
            duration=attr(AttributeKey.DURATION),
            sample_rate=attr(AttributeKey.SAMPLE_RATE),
            bit_depth=attr(AttributeKey.BIT_DEPTH),
            vbr=bool(attr(AttributeKey.VBR)),
            free_slots=bool(getattr(result, "has_free_slots", False)),
            upload_speed=int(getattr(result, "avg_speed", 0) or 0),
            queue_size=int(getattr(result, "queue_size", 0) or 0),
            locked=locked,
        )
    except Exception:
        return None


# ============================== CLI DE TESTE ==============================

def _cli() -> int:  # pragma: no cover - utilitário manual
    """Uso: python -m core.soulseek buscar "termo"  |  baixar "termo" """
    import argparse

    parser = argparse.ArgumentParser(description="Teste rápido do motor Soulseek")
    parser.add_argument("acao", choices=["buscar", "baixar", "config"])
    parser.add_argument("termo", nargs="?", default="")
    parser.add_argument("--segundos", type=float, default=None)
    parser.add_argument("--usuario", default=None)
    parser.add_argument("--senha", default=None)
    args = parser.parse_args()

    cfg = SoulseekConfig.load()
    if args.usuario:
        cfg.username = args.usuario
    if args.senha:
        cfg.password = args.senha
    if args.acao == "config":
        cfg.save()
        print(f"Config salva em {SoulseekConfig.path()}")
        return 0

    engine = SoulseekEngine(cfg, log_callback=lambda m: print(f"  {m}"))
    ok, msg = engine.connect()
    print(msg)
    if not ok:
        return 1

    try:
        if args.acao == "buscar":
            results = filter_candidates(engine.search(args.termo, seconds=args.segundos), cfg)
            for c in dedupe_by_file(results)[:25]:
                slots = "livre" if c.free_slots else f"fila {c.queue_size}"
                print(f"  {c.score:6.1f}  {c.quality_label:<22} {c.size_mb:7.1f}MB  "
                      f"{c.username:<20} {slots:<10} {c.filename}")
        else:
            job = engine.search_and_download(args.termo, seconds=args.segundos)
            while job and not job.is_done:
                time.sleep(1)
                print(f"  {job.state}: {job.detail} {job.progress * 100:.0f}%")
            if job:
                print(f"Resultado: {job.state} — {job.detail or job.error}")
    finally:
        engine.shutdown()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
