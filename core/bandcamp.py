"""Cliente de busca do Bandcamp — sem autenticação.

O Bandcamp não publica API oficial, mas o próprio site usa dois endpoints
públicos (mesmos que o navegador chama) que confirmei ao vivo (2026-07-29):

  1. POST bandcamp.com/api/bcsearch_public_api/1/autocomplete_elastic
     — busca geral, devolve faixas/álbuns/artistas com nome e URL, SEM preço.
  2. GET na página de cada faixa/álbum
     — o preço vem embutido no atributo `data-tralbum` (JSON com entidades
     HTML), e a moeda no atributo `data-band-currency`.

Por isso a busca é em duas etapas: primeiro lista candidatos (rápido), depois
busca o preço só dos top N (mais lento, um request por item). Tudo
best-effort — página de terceiro pode mudar a qualquer momento.

IMPORTANTE — por que via `curl.exe` e não `requests`:
O Bandcamp bloqueia por fingerprint de TLS/HTTP, não por header. Testado ao
vivo: `requests` puro do Python é bloqueado (volta uma página de erro
genérica de 3KB) tanto no endpoint de busca quanto nas páginas de faixa.
`curl_cffi` (que imita o TLS de um Chrome de verdade) resolve o endpoint de
busca, mas ainda é bloqueado nas páginas de faixa/álbum nos subdomínios
*.bandcamp.com — provavelmente uma configuração de bot-mitigation diferente
por subdomínio. O único cliente 100% confiável nos dois casos, em todos os
testes, foi o `curl.exe` do próprio Windows (vem de fábrica desde o Windows
10 1803) — por isso chamamos ele via subprocess em vez de uma lib Python.
Se o binário não existir no sistema, tudo aqui falha em silêncio (lista
vazia), nunca derruba o app.
"""

from __future__ import annotations

import html
import json
import re
import subprocess
from dataclasses import dataclass
from typing import Optional

SEARCH_URL = "https://bandcamp.com/api/bcsearch_public_api/1/autocomplete_elastic"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
TRALBUM_RE = re.compile(r'data-tralbum="([^"]*)"')
CURRENCY_RE = re.compile(r'data-band-currency="([A-Z]{3})"')
REQUEST_TIMEOUT = 15

FREE_DOWNLOAD_PREF = 1   # constante 'FREE' do próprio payload do Bandcamp


def _curl(args: list[str], timeout: float) -> Optional[str]:
    """Roda `curl.exe` com os argumentos dados e devolve o stdout (texto).

    Nunca levanta exceção: binário ausente, timeout ou erro de rede viram
    None, e quem chama trata como "não deu pra buscar agora".
    """
    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", str(int(timeout) + 2), *args],
            capture_output=True, timeout=timeout + 5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout.decode("utf-8", errors="replace")


def _curl_get(url: str, timeout: float) -> Optional[str]:
    return _curl(["-A", USER_AGENT, url], timeout)


def _curl_post_json(url: str, body: dict, timeout: float) -> Optional[dict]:
    text = _curl(
        ["-X", "POST", url, "-H", "Content-Type: application/json",
         "-A", USER_AGENT, "-d", json.dumps(body)],
        timeout,
    )
    if not text:
        return None
    try:
        return json.loads(text)
    except (ValueError, json.JSONDecodeError):
        return None


@dataclass
class BandcampTrack:
    """Uma faixa ou álbum encontrado no Bandcamp, já com preço resolvido."""

    title: str
    artist: str
    url: str
    item_type: str = "track"       # "track" | "album"
    cover_url: str = ""
    price_value: Optional[float] = None   # None = não deu pra confirmar
    currency: str = ""
    is_free: bool = False
    name_your_price: bool = False   # mínimo sugerido, mas comprador escolhe quanto paga

    @property
    def price_label(self) -> str:
        if self.is_free:
            return "Grátis"
        if self.price_value is None:
            return "Ver no Bandcamp"
        if self.name_your_price and self.price_value <= 0:
            return "Pague o quanto quiser"
        valor = f"{self.currency or ''} {self.price_value:.2f}".strip()
        return f"a partir de {valor}" if self.name_your_price else valor


def search(query: str, limit: int = 5, fetch_price: bool = True,
          timeout: float = REQUEST_TIMEOUT) -> list[BandcampTrack]:
    """Busca no Bandcamp. Nunca levanta exceção — devolve [] no erro."""
    query = (query or "").strip()
    if not query:
        return []

    payload = _curl_post_json(
        SEARCH_URL, timeout=timeout,
        body={"fan_id": None, "full_page": False, "search_filter": "", "search_text": query},
    )
    if not payload:
        return []

    items = (payload.get("auto") or {}).get("results") or []
    # só track/álbum tem preço — artista ('b') e fã ('f') não interessam aqui
    candidatos = [i for i in items if i.get("type") in ("t", "a")][:limit]

    out = []
    for item in candidatos:
        track = _candidate_from_json(item)
        if not track:
            continue
        if fetch_price:
            _fill_price(track, timeout)
        out.append(track)
    return out


def _candidate_from_json(item: dict) -> Optional[BandcampTrack]:
    try:
        url = item.get("item_url_path") or ""
        if not url:
            return None
        return BandcampTrack(
            title=item.get("name") or "(sem título)",
            artist=item.get("band_name") or "",
            url=url,
            item_type="track" if item.get("type") == "t" else "album",
            cover_url=item.get("img") or "",
        )
    except TypeError:
        return None


def _fill_price(track: BandcampTrack, timeout: float) -> None:
    """Busca a página do item e completa preço/moeda. Falha em silêncio."""
    page = _curl_get(track.url, timeout)
    if not page:
        return

    currency_match = CURRENCY_RE.search(page)
    if currency_match:
        track.currency = currency_match.group(1)

    tralbum_match = TRALBUM_RE.search(page)
    if not tralbum_match:
        return
    try:
        data = json.loads(html.unescape(tralbum_match.group(1)))
    except (ValueError, json.JSONDecodeError):
        return

    current = data.get("current") or {}
    download_pref = current.get("download_pref")
    minimum = current.get("minimum_price")
    is_set_price = bool(current.get("is_set_price"))

    if download_pref == FREE_DOWNLOAD_PREF and not minimum:
        track.is_free = True
        return

    if minimum is not None:
        try:
            track.price_value = float(minimum)
            track.name_your_price = not is_set_price and track.price_value <= (
                current.get("set_price") or track.price_value
            )
        except (TypeError, ValueError):
            pass


def best_match(query: str, limit: int = 5) -> Optional[BandcampTrack]:
    """O primeiro resultado da busca (o Bandcamp já ordena por relevância)."""
    results = search(query, limit=limit)
    return results[0] if results else None


if __name__ == "__main__":  # pragma: no cover - checagem manual rápida
    import sys

    termo = " ".join(sys.argv[1:]) or "Blue Monday"
    for t in search(termo):
        print(f"{t.price_label:>18}  {t.artist} - {t.title}  ({t.item_type})  {t.url}")
