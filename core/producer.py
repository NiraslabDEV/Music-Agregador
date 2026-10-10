"""Conservative producer discovery: store credits first, P2P evidence separately."""
from __future__ import annotations

import re
import html
import json
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import beatport, bandcamp
from .aggregator import TrackResult
from .soulseek import filter_candidates


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).casefold()
    return " ".join(re.findall(r"[^\W_]+", "".join(c for c in value if not unicodedata.combining(c))))


def credited_to(credit: str, name: str) -> bool:
    # Never accept a substring: Sol must not match Solomun.
    return any(normalize(part) == normalize(name) for part in re.split(r"\s*(?:,|&| / | feat\.? | featuring | vs\.? )\s*", credit, flags=re.I))


def file_matches(candidate, artist: str, title: str) -> bool:
    # Require the artist in the filename, not an enclosing DJ-set folder.
    stem = candidate.filename.rsplit(".", 1)[0]
    parts = re.split(r"\s+[-–—]\s+", stem)
    if len(parts) < 2:
        return False
    credit = re.sub(r"^\d+[ ._-]+", "", parts[0])
    if not credited_to(credit, artist):
        return False
    actual = normalize(" - ".join(parts[1:]))
    expected = normalize(title)
    # Keep mix/version names; a different remix is a different recording.
    return actual == expected or actual == normalize(title + " Original Mix")


def album_tracks(album, name):
    """Expand a bounded number of credited albums; do not infer VA authorship."""
    page = bandcamp._curl_get(album.url, 10)
    match = bandcamp.TRALBUM_RE.search(page or "")
    if not match:
        return []
    try:
        data = json.loads(html.unescape(match.group(1)))
        artist = data.get("artist") or album.artist
        out = []
        from urllib.parse import urljoin
        for item in (data.get("trackinfo") or [])[:50]:
            credit = item.get("artist") or artist
            path = item.get("title_link")
            if credited_to(credit, name) and path and item.get("title"):
                out.append(bandcamp.BandcampTrack(item["title"], credit,
                    urljoin(album.url, path), cover_url=album.cover_url))
        return out
    except (TypeError, ValueError):
        return []


@dataclass
class ProducerResult:
    name: str
    tracks: dict[str, TrackResult] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def search_producer(name: str, engine, seconds: float = 10, on_update=None) -> ProducerResult:
    name = " ".join(name.split())
    if not name or len(name) > 120:
        raise ValueError("Digite um nome de produtor com até 120 caracteres.")
    result = ProducerResult(name)
    result.warnings.append("Seleção dos resultados públicos das lojas; não representa a discografia completa.")

    def notify():
        if on_update:
            on_update(result)

    def fetch(source, callback):
        try:
            return callback()
        except Exception:
            result.warnings.append(f"{source} indisponível nesta busca.")
            return []

    with ThreadPoolExecutor(max_workers=2) as pool:
        bp = pool.submit(fetch, "Beatport", lambda: beatport.search(name, limit=60))
        bc = pool.submit(fetch, "Bandcamp", lambda: bandcamp.search(name, limit=40, fetch_price=False))
        bp_tracks, bc_tracks = bp.result(), bc.result()
    matching = [t for t in bp_tracks if any(normalize(a) == normalize(name) for a in t.artists)]
    ids = {artist_id for t in matching for a, artist_id in zip(t.artists, t.artist_ids) if normalize(a) == normalize(name)}
    if len(ids) > 1:
        result.warnings.append("Há artistas homônimos no Beatport. Refine o nome; essas faixas foram omitidas.")
        matching = []
    for t in matching:
        key = f"{name} - {t.full_title}"
        result.tracks.setdefault(key, TrackResult(key)).beatport.append(t)
    albums = [t for t in bc_tracks if t.item_type == "album" and credited_to(t.artist, name)][:6]
    with ThreadPoolExecutor(max_workers=2) as pool:
        for expanded in pool.map(lambda album: album_tracks(album, name), albums):
            bc_tracks.extend(expanded)
    for t in bc_tracks:
        if t.item_type != "track" or not credited_to(t.artist, name):
            continue
        key = f"{name} - {t.title}"
        # Merge only exact normalized titles, including the version.
        key = next((k for k in result.tracks if normalize(k) == normalize(key)), key)
        group = result.tracks.setdefault(key, TrackResult(key))
        if not any(existing.url == t.url for existing in group.bandcamp):
            group.bandcamp.append(t)
    if not result.tracks:
        result.warnings.append("Nenhuma faixa com crédito exato encontrada. Tente o nome artístico completo. Álbuns e menções foram omitidos.")
    notify()
    if engine.is_connected and result.tracks:
        try:
            candidates = filter_candidates(engine.search(name, seconds=seconds), engine.config)
            for key, track in result.tracks.items():
                title = key[len(name) + 3:]
                track.soulseek = [c for c in candidates if file_matches(c, name, title)]
        except Exception:
            result.warnings.append("Soulseek indisponível nesta busca.")
    elif not engine.is_connected:
        result.warnings.append("Conecte o Soulseek para procurar os arquivos dessas faixas.")
    for track in result.tracks.values():
        track.done = True
    notify()
    return result
