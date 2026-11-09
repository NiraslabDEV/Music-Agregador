"""Links do Spotify viram buscas "Artista - Faixa".

O Spotify serve de referência (o que a pessoa ouviu, a playlist de um amigo),
mas não de fonte: o áudio de lá é protegido (DRM) e não dá pra baixar. O app
lê só os metadados do link e busca a faixa onde ela pode ser comprada ou
baixada — Beatport, Bandcamp, Soulseek e YouTube.

Sem conta de desenvolvedor: lê o JSON da página de "embed" (o player que o
Spotify deixa qualquer site incorporar), que traz nome, artistas, duração e a
prévia de 30 s. Playlists muito grandes podem vir cortadas pelo próprio
embed. Nunca levanta exceção — link inválido ou fora do ar vira None.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

import requests

EMBED_URL = "https://open.spotify.com/embed/{kind}/{id}"
REQUEST_TIMEOUT = 12
KINDS = ("track", "album", "playlist")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"}

# open.spotify.com/track/ID, /intl-pt/album/ID?si=..., /embed/playlist/ID, spotify:track:ID
LINK_RE = re.compile(
    r"(?:https?://)?open\.spotify\.com/(?:intl-[\w-]+/)?(?:embed/)?"
    r"(?P<kind>track|album|playlist)/(?P<id>[A-Za-z0-9]{22})[^\s,]*"
    r"|spotify:(?P<kind2>track|album|playlist):(?P<id2>[A-Za-z0-9]{22})")
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)


@dataclass
class SpotifyTrack:
    track_id: str
    title: str
    artists: list[str] = field(default_factory=list)
    duration_ms: int = 0
    preview_url: str = ""

    @property
    def artist_label(self) -> str:
        return ", ".join(self.artists)

    @property
    def query(self) -> str:
        return f"{self.artist_label} - {self.title}" if self.artists else self.title

    @property
    def url(self) -> str:
        return f"https://open.spotify.com/track/{self.track_id}"


@dataclass
class SpotifyItem:
    kind: str           # track | album | playlist
    item_id: str
    name: str
    tracks: list[SpotifyTrack] = field(default_factory=list)


def find_links(text: str) -> list[tuple[str, str]]:
    """(tipo, id) de cada link do Spotify no texto, na ordem, sem repetir."""
    found = []
    for m in LINK_RE.finditer(text or ""):
        pair = (m.group("kind") or m.group("kind2"), m.group("id") or m.group("id2"))
        if pair not in found:
            found.append(pair)
    return found


def strip_links(text: str) -> str:
    """O texto sem os links — o que sobra é buscado normalmente."""
    return LINK_RE.sub("", text or "")


def _clean(text) -> str:
    return " ".join(str(text or "").replace("\xa0", " ").split())


def _split_artists(text: str) -> list[str]:
    return [a for a in (_clean(p) for p in (text or "").split(",")) if a]


def _track_from_list_item(item: dict) -> Optional[SpotifyTrack]:
    uri = item.get("uri") or ""
    if item.get("entityType", "track") != "track" or not uri.startswith("spotify:track:"):
        return None  # episódio de podcast numa playlist, etc.
    return SpotifyTrack(
        track_id=uri.rsplit(":", 1)[-1],
        title=_clean(item.get("title")),
        artists=_split_artists(item.get("subtitle") or ""),
        duration_ms=int(item.get("duration") or 0),
        preview_url=(item.get("audioPreview") or {}).get("url") or "",
    )


def parse_embed(kind: str, item_id: str, html: str) -> Optional[SpotifyItem]:
    match = NEXT_DATA_RE.search(html or "")
    if not match:
        return None
    try:
        entity = json.loads(match.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
    except (ValueError, KeyError, TypeError):
        return None  # página de erro (link apagado/privado) não tem "state"
    name = _clean(entity.get("name") or entity.get("title"))
    if kind == "track":
        track = SpotifyTrack(
            track_id=entity.get("id") or item_id,
            title=name,
            artists=[_clean(a.get("name")) for a in entity.get("artists") or []
                     if isinstance(a, dict) and a.get("name")],
            duration_ms=int(entity.get("duration") or 0),
            preview_url=(entity.get("audioPreview") or {}).get("url") or "",
        )
        return SpotifyItem(kind, item_id, name, [track]) if track.title else None
    tracks = [t for t in (_track_from_list_item(i) for i in entity.get("trackList") or []
                          if isinstance(i, dict)) if t and t.title]
    return SpotifyItem(kind, item_id, name, tracks)


def fetch(kind: str, item_id: str, timeout: float = REQUEST_TIMEOUT) -> Optional[SpotifyItem]:
    if kind not in KINDS:
        return None
    try:
        resp = requests.get(EMBED_URL.format(kind=kind, id=item_id), headers=HEADERS,
                            timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException:
        return None
    return parse_embed(kind, item_id, resp.text)


if __name__ == "__main__":  # pragma: no cover - checagem manual rápida
    import sys

    for k, i in find_links(" ".join(sys.argv[1:])):
        item = fetch(k, i)
        print(f"{k} {i}: {item.name if item else 'não encontrado'}")
        for t in (item.tracks if item else []):
            print(f"  {t.query}")
