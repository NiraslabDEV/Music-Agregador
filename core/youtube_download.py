"""Baixa o áudio de vídeos do YouTube (yt-dlp) numa pasta só pra eles.

É a saída pra faixa que não existe em loja nem no Soulseek: remix raro, edit
de DJ, set ao vivo. Fica numa pasta separada da do Soulseek de propósito — a
qualidade é a do YouTube (AAC ~128 kbps), não dá pra confundir com um FLAC, e
a pasta do Soulseek é compartilhada na rede, essa não.

Com ffmpeg no PATH o áudio sai sempre em .m4a (o YouTube às vezes só entrega
Opus/WebM, que Rekordbox/Serato não abrem); sem ffmpeg, baixa o .m4a que o
YouTube já oferece pronto, sem conversão.
"""

from __future__ import annotations

import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .soulseek import DownloadHistory
from .youtube import YouTubeVideo

try:
    import yt_dlp
    from yt_dlp.utils import DownloadCancelled, DownloadError
    YTDLP_AVAILABLE = True
except ImportError:  # pragma: no cover - o app roda sem, só perde o botão
    yt_dlp = None
    DownloadCancelled = DownloadError = Exception
    YTDLP_AVAILABLE = False

YOUTUBE_USER = "youtube"   # valor de `username` no histórico pras faixas daqui
MAX_PARALLEL = 2
SEARCH_LIMIT = 6


def default_download_dir() -> str:
    return str(Path.home() / "Music" / "YouTube")


def ffmpeg_path() -> Optional[str]:
    return shutil.which("ffmpeg")


def is_youtube_entry(entry: dict) -> bool:
    return (entry or {}).get("username") == YOUTUBE_USER


def format_duration(seconds) -> str:
    try:
        total = int(seconds or 0)
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def search_videos(query: str, limit: int = SEARCH_LIMIT) -> list[YouTubeVideo]:
    """Busca sem chave de API (a mesma busca do site). Levanta exceção no
    erro de rede — quem chama decide se cai pra API oficial."""
    query = (query or "").strip()
    if not query or not YTDLP_AVAILABLE:
        return []
    opts = {"quiet": True, "no_warnings": True, "skip_download": True,
            "extract_flat": "in_playlist", "noplaylist": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False) or {}
    return [v for v in (_video_from_entry(e) for e in info.get("entries") or []) if v]


def _video_from_entry(entry: dict) -> Optional[YouTubeVideo]:
    if not isinstance(entry, dict) or not entry.get("id"):
        return None
    thumbs = entry.get("thumbnails") or []
    thumb = thumbs[0].get("url", "") if thumbs and isinstance(thumbs[0], dict) else ""
    return YouTubeVideo(
        video_id=entry["id"],
        title=entry.get("title") or "(sem título)",
        channel=entry.get("channel") or entry.get("uploader") or "",
        thumbnail_url=thumb,
        duration=int(entry.get("duration") or 0),
    )


def quality_label(info: dict, path: str) -> str:
    ext = Path(path).suffix.lstrip(".").upper() or "?"
    abr = info.get("abr")
    try:
        return f"{ext} ~{round(float(abr))}kbps" if abr else ext
    except (TypeError, ValueError):
        return ext


@dataclass
class YouTubeJob:
    """Mesmos campos que a tela de downloads lê de um DownloadJob do Soulseek."""

    label: str
    video: YouTubeVideo
    state: str = "queued"      # queued|downloading|converting|complete|failed|cancelled
    detail: str = ""
    bytes_done: int = 0
    size: int = 0
    speed: float = 0.0
    local_path: str = ""
    error: str = ""
    cancelled: bool = False
    _on_update: Optional[Callable[["YouTubeJob"], None]] = field(default=None, repr=False)

    @property
    def progress(self) -> float:
        if self.state in ("converting", "complete"):
            return 1.0
        return min(self.bytes_done / self.size, 1.0) if self.size else 0.0

    @property
    def speed_label(self) -> str:
        if self.speed <= 0:
            return ""
        mbps = self.speed / (1024 * 1024)
        return f"{mbps:.1f} MB/s" if mbps >= 1 else f"{self.speed / 1024:.0f} KB/s"

    @property
    def is_done(self) -> bool:
        return self.state in ("complete", "failed", "cancelled")


class YouTubeDownloader:
    def __init__(self, get_history: Callable[[], DownloadHistory],
                 get_download_dir: Callable[[], str],
                 log: Optional[Callable[[str], None]] = None):
        self.get_history = get_history  # o mesmo histórico da Biblioteca
        self.get_download_dir = get_download_dir
        self.log = log or (lambda *_: None)
        self._jobs: list[YouTubeJob] = []
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=MAX_PARALLEL, thread_name_prefix="youtube")

    @property
    def available(self) -> bool:
        return YTDLP_AVAILABLE

    def jobs(self) -> list[YouTubeJob]:
        with self._lock:
            return list(self._jobs)

    def clear_finished(self) -> None:
        with self._lock:
            self._jobs = [j for j in self._jobs if not j.is_done]

    def cancel(self, job: YouTubeJob) -> None:
        job.cancelled = True
        if job.state == "queued":
            job.state, job.detail = "cancelled", "cancelado"
            self._notify(job)

    def shutdown(self) -> None:
        for job in self.jobs():
            job.cancelled = True
        self._pool.shutdown(wait=False, cancel_futures=True)

    def enqueue(self, video: YouTubeVideo, label: str,
                on_update: Optional[Callable[[YouTubeJob], None]] = None) -> YouTubeJob:
        job = YouTubeJob(label=label, video=video, detail="na fila", _on_update=on_update)
        with self._lock:
            self._jobs.insert(0, job)
        self._pool.submit(self._run, job)
        self._notify(job)
        return job

    def _notify(self, job: YouTubeJob) -> None:
        if job._on_update:
            try:
                job._on_update(job)
            except Exception:
                pass

    def _options(self, job: YouTubeJob, folder: Path) -> dict:
        def hook(d):
            if job.cancelled:
                raise DownloadCancelled("cancelado")
            if d.get("status") == "downloading":
                job.state = "downloading"
                job.bytes_done = int(d.get("downloaded_bytes") or 0)
                job.size = int(d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
                job.speed = float(d.get("speed") or 0)
                job.detail = f"{job.video.channel} · YouTube".strip(" ·")
                self._notify(job)
            elif d.get("status") == "finished":
                job.state, job.detail, job.speed = "converting", "finalizando o arquivo…", 0
                self._notify(job)

        opts = {
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "outtmpl": str(folder / "%(title).150B [%(id)s].%(ext)s"),
            "windowsfilenames": True,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "progress_hooks": [hook],
        }
        ffmpeg = ffmpeg_path()
        if ffmpeg:
            opts["ffmpeg_location"] = ffmpeg
            # Já-em-m4a passa direto (sem reencodar); Opus/WebM vira AAC.
            opts["postprocessors"] = [
                {"key": "FFmpegExtractAudio", "preferredcodec": "m4a"},
                {"key": "FFmpegMetadata"},
            ]
        return opts

    def _run(self, job: YouTubeJob) -> None:
        if job.cancelled:
            if job.state != "cancelled":
                job.state, job.detail = "cancelled", "cancelado"
                self._notify(job)
            return
        folder = Path(self.get_download_dir() or default_download_dir())
        try:
            folder.mkdir(parents=True, exist_ok=True)
            job.state, job.detail = "downloading", "conectando ao YouTube…"
            self._notify(job)
            with yt_dlp.YoutubeDL(self._options(job, folder)) as ydl:
                info = ydl.extract_info(job.video.url, download=True) or {}
            downloads = info.get("requested_downloads") or [{}]
            path = downloads[-1].get("filepath") or info.get("filepath") or ""
            if not path or not Path(path).is_file():
                raise RuntimeError("o arquivo baixado não foi encontrado")
            job.local_path = path
            job.size = job.bytes_done = Path(path).stat().st_size
            quality = quality_label(info, path)
            self.get_history().add_entry(job.label, YOUTUBE_USER, job.video.url, path,
                                   job.size, f"YouTube · {quality}")
            job.state, job.detail = "complete", f"{quality} · {Path(path).name}"
            self.log(f"YouTube: baixado {Path(path).name}")
        except DownloadCancelled:
            job.state, job.detail = "cancelled", "cancelado"
        except Exception as ex:
            if job.cancelled:
                job.state, job.detail = "cancelled", "cancelado"
            else:
                message = str(ex).replace("ERROR: ", "").strip() or ex.__class__.__name__
                job.state, job.error, job.detail = "failed", message, message
                self.log(f"YouTube falhou para '{job.label}': {message}")
        self._notify(job)
