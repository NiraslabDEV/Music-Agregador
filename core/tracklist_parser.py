"""Transforma um tracklist colado (com timestamp, artista, faixa e label) numa
lista limpa de buscas "Artista Título" — o formato que o resto do app espera.

A maioria dos tracklists colados (Mixesdb, 1001Tracklists, descrição de set no
YouTube/SoundCloud etc.) segue o mesmo esqueleto, só que com espaçamento
inconsistente:

    0:00:01   Claudio PRC   -   Nadir (Original Mix)   [Warm Up Recordings]
    1:27:00    Esteban Miranda    -    Olivos    [Signal LTD]

Um heurístico por regex resolve a esmagadora maioria desses casos, de graça e
na hora (`parse_tracklist_heuristic`). O que sobrar — linhas que não batem com
nenhum padrão conhecido, porque a pessoa colou de um jeito bem diferente — vai
pro fallback de IA (`core/ai_providers.py`), só para essas linhas, não pra
lista inteira. Se não houver IA configurada, a linha ainda entra na busca, só
que com uma limpeza mais simples (tira timestamp/label, mantém o resto).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# Marcador de timestamp no início da linha: "0:00:01", "1:27:00", "12:05".
TIMESTAMP_RE = re.compile(r"^\d{1,2}(:\d{2}){1,2}\b\s*")

# Numeração de lista no início: "01.", "3)", "12 -".
TRACK_NUMBER_RE = re.compile(r"^\d{1,3}\s*[\.\)]\s+")

# Label/gravadora entre colchetes ou parênteses no fim da linha: "[Warm Up Recordings]".
LABEL_RE = re.compile(r"\s*\[[^\[\]]*\]\s*$")

# Coluna separada por tab ou por 2+ espaços (o jeito mais comum de colar de
# uma planilha ou de uma lista alinhada visualmente).
COLUMN_SPLIT_RE = re.compile(r"\t+| {2,}")

# Traço isolado usado como separador de coluna própria (quando veio cercado
# por tabs/espaços largos e sobrou como item isolado depois do split acima).
LONE_DASH_RE = re.compile(r"^[-–—]$")

# Traço com espaço single dos dois lados, para o caso em que artista e "-"
# não ficaram em colunas separadas (só um espaço simples entre eles).
INLINE_DASH_RE = re.compile(r"\s[-–—]\s")

MIN_TITLE_LEN = 1


@dataclass
class ParsedTrack:
    """Uma linha do tracklist já resolvida em artista + título."""

    artist: str
    title: str
    raw: str
    label: str = ""
    source: str = "heuristic"   # "heuristic" | "ai" | "raw" (não deu pra separar)

    @property
    def query(self) -> str:
        """Texto pra mandar pra busca: sem o traço, artista e título juntos."""
        parts = [p for p in (self.artist, self.title) if p]
        return " ".join(parts).strip()


def _strip_label(line: str) -> tuple[str, str]:
    match = LABEL_RE.search(line)
    if not match:
        return line, ""
    label = match.group(0).strip()[1:-1].strip()
    return line[: match.start()].rstrip(), label


def _split_artist_title(text: str) -> Optional[tuple[str, str]]:
    """Separa "Artista - Título" (com variações de traço). None se não achar."""
    match = INLINE_DASH_RE.search(text)
    if not match:
        return None
    artist = text[: match.start()].strip()
    title = text[match.end():].strip()
    if not artist or len(title) < MIN_TITLE_LEN:
        return None
    return artist, title


def parse_line_heuristic(line: str) -> Optional[ParsedTrack]:
    """Tenta separar uma linha em artista/título. None se não reconhecer o formato."""
    raw = line
    line = line.strip()
    if not line:
        return None

    line, label = _strip_label(line)
    line = TIMESTAMP_RE.sub("", line)
    line = TRACK_NUMBER_RE.sub("", line)

    columns = [c.strip() for c in COLUMN_SPLIT_RE.split(line) if c.strip()]

    if len(columns) >= 3:
        # Formato em colunas com o traço isolado: [artista, "-", título, ...]
        dash_idx = next((i for i, c in enumerate(columns) if LONE_DASH_RE.match(c)), None)
        if dash_idx is not None and 0 < dash_idx < len(columns) - 1:
            artist = " ".join(columns[:dash_idx]).strip()
            title = " ".join(columns[dash_idx + 1:]).strip()
            if artist and title:
                return ParsedTrack(artist=artist, title=title, raw=raw, label=label)

    # Sem coluna de traço isolada: junta tudo nas colunas e procura "A - B" solto.
    joined = " ".join(columns) if columns else line
    split = _split_artist_title(joined)
    if split:
        artist, title = split
        # Sobrou um traço solto colado no fim do "artista" (ex.: "Nome -")? limpa.
        artist = re.sub(r"\s*[-–—]\s*$", "", artist).strip()
        if artist and title:
            return ParsedTrack(artist=artist, title=title, raw=raw, label=label)

    return None


def _soft_clean(line: str) -> str:
    """Última linha de defesa: tira timestamp/numeração/label e devolve o resto
    inteiro como busca — nunca descarta a linha, só limpa o que dá pra limpar."""
    line, _ = _strip_label(line.strip())
    line = TIMESTAMP_RE.sub("", line)
    line = TRACK_NUMBER_RE.sub("", line)
    line = re.sub(r"\s{2,}", " ", line).strip()
    line = re.sub(r"^[-–—]\s*", "", line)
    line = re.sub(r"\s*[-–—]\s*$", "", line)
    return line


def _split_raw_entries(text: str) -> list[str]:
    """Quebra o texto colado em "linhas de faixa": por quebra de linha, e — só
    quando não há quebra de linha nenhuma — por vírgula/ponto e vírgula (caso
    de uma lista simples digitada numa linha só, sem timestamp)."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [l for l in text.split("\n") if l.strip()]
    if len(lines) > 1:
        return lines
    if lines:
        return [p for p in re.split(r"[,;]+", lines[0]) if p.strip()]
    return []


def parse_tracklist_heuristic(text: str) -> tuple[list[ParsedTrack], list[str]]:
    """Aplica o heurístico linha a linha.

    Devolve (resolvidas, não_resolvidas) — a segunda lista é o texto original
    de cada linha que não bateu com nenhum padrão, na ordem em que apareceram,
    pronta para ir pro fallback de IA (ou pra limpeza simples).
    """
    resolved: list[ParsedTrack] = []
    unresolved: list[str] = []
    for entry in _split_raw_entries(text):
        parsed = parse_line_heuristic(entry)
        if parsed:
            resolved.append(parsed)
        else:
            unresolved.append(entry.strip())
    return resolved, unresolved


def parse_tracklist(
    text: str,
    ai_settings=None,
    log=None,
) -> list[ParsedTrack]:
    """Ponto de entrada único: heurístico primeiro, IA só pro que sobrar.

    `ai_settings`, se dado, é um `core.ai_providers.AISettings` já carregado
    com pelo menos uma chave configurada. Sem isso (ou se a chamada falhar),
    as linhas não resolvidas ainda entram na busca — só com limpeza simples,
    nunca são descartadas.
    """
    log = log or (lambda *_: None)
    resolved, unresolved = parse_tracklist_heuristic(text)
    log(f"Tracklist: {len(resolved)} linha(s) reconhecida(s) por regex, "
        f"{len(unresolved)} precisando de ajuda.")

    if unresolved:
        ai_resolved: list[ParsedTrack] = []
        if ai_settings is not None and ai_settings.has_any_key():
            try:
                from . import ai_providers
                ai_resolved = ai_providers.extract_tracks_with_ai(unresolved, ai_settings, log=log)
            except Exception as e:
                log(f"Fallback de IA falhou: {e}")
        resolved_by_raw = {t.raw.strip(): t for t in ai_resolved}
        for raw_line in unresolved:
            hit = resolved_by_raw.get(raw_line.strip())
            if hit:
                resolved.append(hit)
            else:
                cleaned = _soft_clean(raw_line)
                if cleaned:
                    resolved.append(ParsedTrack(artist="", title=cleaned, raw=raw_line, source="raw"))

    return resolved


def parse_tracklist_to_queries(text: str, ai_settings=None, log=None) -> list[str]:
    """Mesma coisa, mas já devolve strings de busca prontas (sem duplicata)."""
    out: list[str] = []
    seen = set()
    for track in parse_tracklist(text, ai_settings=ai_settings, log=log):
        q = track.query
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out


if __name__ == "__main__":  # pragma: no cover - checagem manual rápida
    import sys

    sample = sys.stdin.read() if not sys.stdin.isatty() else """
0:00:01\tClaudio PRC\t-\tNadir (Original Mix)\t[Warm Up Recordings]
1:27:00    Esteban Miranda    -    Olivos    [Signal LTD]
2:12:00   Regal and Alien Rain -  Acid Affair Pt.1  [Involve Records]
""".strip()

    resolved, unresolved = parse_tracklist_heuristic(sample)
    for t in resolved:
        print(f"[ok] {t.artist!r:35} | {t.title!r:40} | label={t.label!r} -> query={t.query!r}")
    for u in unresolved:
        print(f"[??] {u!r}")
    print(f"\n{len(resolved)} reconhecidas, {len(unresolved)} não reconhecidas de "
          f"{len(resolved) + len(unresolved)} linhas.")
