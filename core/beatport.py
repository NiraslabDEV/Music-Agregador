"""Cliente de busca do Beatport — sem autenticação.

O Beatport tem uma API v4 oficial (api.beatport.com), mas ela exige um token
OAuth de parceiro aprovado — inviável para um app pessoal. Em vez disso, a
própria página pública de busca (beatport.com/search) é uma SPA Next.js que
carrega os resultados como JSON dentro de um <script id="__NEXT_DATA__">.
É o mesmo JSON que a página usa para renderizar — só que a gente lê direto,
sem precisar rodar o React.

Confirmado ao vivo (2026-07-29): GET /search?q=... devolve 200 com o payload
em props.pageProps.dehydratedState.queries[0].state.data.tracks.data — uma
lista de faixas com preço, BPM, key, label, gênero, etc.

Como é HTML de terceiro fora do nosso controle, tudo aqui é best-effort: se a
estrutura mudar, devolve lista vazia em vez de quebrar o app.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

import requests

SEARCH_URL = "https://www.beatport.com/search"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "en-US,en;q=0.9,pt-BR;q=0.8",
}
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)
REQUEST_TIMEOUT = 15


@dataclass
class BeatportTrack:
    """Uma faixa encontrada no Beatport."""

    track_id: int
    title: str
    mix_name: str = ""
    artists: list[str] = field(default_factory=list)
    label: str = ""
    genre: str = ""
    bpm: int = 0
    key: str = ""
    price_value: float = 0.0
    price_currency: str = ""
    price_display: str = ""
    release_name: str = ""
    cover_url: str = ""
    publish_date: str = ""
    query: str = ""   # o termo que a pessoa digitou, para montar o link de busca

    @property
    def full_title(self) -> str:
        return f"{self.title} ({self.mix_name})" if self.mix_name else self.title

    @property
    def artist_label(self) -> str:
        return ", ".join(self.artists) if self.artists else "Artista desconhecido"

    @property
    def url(self) -> str:
        """Não há slug/URL direta no payload de busca — linka pra própria busca."""
        term = self.query or f"{self.artist_label} {self.full_title}"
        return f"https://www.beatport.com/search?q={requests.utils.quote(term)}"

    @property
    def duration_label(self) -> str:
        return ""

    @property
    def has_price(self) -> bool:
        return self.price_value > 0


def search(query: str, limit: int = 8, timeout: float = REQUEST_TIMEOUT) -> list[BeatportTrack]:
    """Busca faixas no Beatport. Nunca levanta exceção — devolve [] no erro."""
    query = (query or "").strip()
    if not query:
        return []

    try:
        resp = requests.get(
            SEARCH_URL, params={"q": query}, headers=HEADERS, timeout=timeout,
        )
        resp.raise_for_status()
    except requests.RequestException:
        return []

    return _parse_search_html(resp.text, query, limit)


def _parse_search_html(html: str, query: str, limit: int) -> list[BeatportTrack]:
    match = NEXT_DATA_RE.search(html)
    if not match:
        return []

    try:
        data = json.loads(match.group(1))
        queries = data["props"]["pageProps"]["dehydratedState"]["queries"]
        tracks_data = None
        for q in queries:
            payload = q.get("state", {}).get("data")
            if isinstance(payload, dict) and "tracks" in payload:
                tracks_data = payload["tracks"].get("data") or []
                break
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return []

    if not tracks_data:
        return []

    out = []
    for item in tracks_data[:limit]:
        track = _track_from_json(item, query)
        if track:
            out.append(track)
    return out


def _track_from_json(item: dict, query: str) -> Optional[BeatportTrack]:
    try:
        price = item.get("price") or {}
        label = item.get("label") or {}
        release = item.get("release") or {}
        genres = item.get("genre") or []
        artists = [a.get("artist_name", "") for a in (item.get("artists") or []) if a.get("artist_name")]

        return BeatportTrack(
            track_id=int(item.get("track_id") or 0),
            title=item.get("track_name") or "(sem título)",
            mix_name=item.get("mix_name") or "",
            artists=artists,
            label=label.get("label_name", ""),
            genre=", ".join(g.get("genre_name", "") for g in genres if g.get("genre_name")),
            bpm=int(item.get("bpm") or 0),
            key=item.get("key_name") or "",
            price_value=float(price.get("value") or 0),
            price_currency=price.get("code") or "",
            price_display=price.get("display") or "",
            release_name=release.get("release_name", ""),
            cover_url=release.get("release_image_uri", "") or item.get("track_image_uri", ""),
            publish_date=(item.get("publish_date") or "")[:10],
            query=query,
        )
    except (TypeError, ValueError):
        return None


def best_match(query: str, limit: int = 8) -> Optional[BeatportTrack]:
    """O resultado mais relevante (o Beatport já ordena por score de relevância)."""
    results = search(query, limit=limit)
    return results[0] if results else None


if __name__ == "__main__":  # pragma: no cover - checagem manual rápida
    import sys

    termo = " ".join(sys.argv[1:]) or "Blue Monday"
    for t in search(termo):
        print(f"{t.price_display or '?':>8}  {t.artist_label} - {t.full_title}  "
              f"[{t.label}]  {t.bpm}bpm {t.key}")
