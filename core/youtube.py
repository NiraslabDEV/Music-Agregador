"""Busca no YouTube pela API oficial — reserva da busca do yt-dlp.

A busca principal (e o download do áudio) fica em core/youtube_download.py e
não precisa de chave. Esta, pela YouTube Data API v3 (chave grátis em
console.cloud.google.com), só entra se a outra falhar e houver chave salva.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests

from .soulseek import _data_dir

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
REQUEST_TIMEOUT = 10


@dataclass
class YouTubeConfig:
    """Chave da API (opcional) e a pasta onde caem os áudios baixados do YouTube."""

    api_key: str = ""
    download_dir: str = ""   # vazio = ~/Music/YouTube

    @classmethod
    def path(cls) -> Path:
        return _data_dir() / "youtube.json"

    @classmethod
    def load(cls) -> "YouTubeConfig":
        cfg = cls()
        try:
            raw = json.loads(cls.path().read_text(encoding="utf-8"))
            cfg.api_key = raw.get("api_key", "") or ""
            cfg.download_dir = raw.get("download_dir", "") or ""
        except Exception:
            pass
        return cfg

    def save(self) -> None:
        try:
            self.path().write_text(
                json.dumps({"api_key": self.api_key, "download_dir": self.download_dir},
                           indent=2), encoding="utf-8")
        except Exception:
            pass


@dataclass
class YouTubeVideo:
    video_id: str
    title: str
    channel: str = ""
    thumbnail_url: str = ""
    duration: int = 0   # segundos; a API de busca não informa (fica 0)

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


def search(query: str, api_key: str, limit: int = 3,
          timeout: float = REQUEST_TIMEOUT) -> list[YouTubeVideo]:
    """Busca vídeos no YouTube. Nunca levanta exceção — devolve [] no erro
    (chave ausente/inválida, cota estourada, sem internet, etc.)."""
    query = (query or "").strip()
    api_key = (api_key or "").strip()
    if not query or not api_key:
        return []

    try:
        resp = requests.get(
            SEARCH_URL,
            params={
                "part": "snippet", "q": query, "type": "video",
                "maxResults": limit, "key": api_key,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        items = resp.json().get("items") or []
    except (requests.RequestException, ValueError):
        return []

    out = []
    for item in items:
        video = _video_from_json(item)
        if video:
            out.append(video)
    return out


def _video_from_json(item: dict) -> Optional[YouTubeVideo]:
    try:
        video_id = (item.get("id") or {}).get("videoId")
        if not video_id:
            return None
        snippet = item.get("snippet") or {}
        thumbs = snippet.get("thumbnails") or {}
        thumb = (thumbs.get("default") or {}).get("url", "")
        return YouTubeVideo(
            video_id=video_id,
            title=snippet.get("title") or "(sem título)",
            channel=snippet.get("channelTitle") or "",
            thumbnail_url=thumb,
        )
    except (TypeError, AttributeError):
        return None


def best_match(query: str, api_key: str) -> Optional[YouTubeVideo]:
    results = search(query, api_key, limit=1)
    return results[0] if results else None


if __name__ == "__main__":  # pragma: no cover - checagem manual rápida
    import sys

    if len(sys.argv) < 2:
        print("Uso: python -m core.youtube <chave_api> [termo de busca]")
        raise SystemExit(1)
    key = sys.argv[1]
    termo = " ".join(sys.argv[2:]) or "Blue Monday New Order"
    for v in search(termo, key):
        print(f"{v.title}  —  {v.channel}  —  {v.url}")
