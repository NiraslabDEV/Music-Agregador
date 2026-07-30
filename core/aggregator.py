"""Junta Beatport, Bandcamp e Soulseek numa busca só, por faixa.

Beatport e Bandcamp são requests HTTP simples (bloqueantes) — rodam numa
thread pool pequena, para não abrir dezenas de conexões de uma vez quando o
usuário cola uma lista de faixas. O Soulseek já tem seu próprio motor
assíncrono (core/soulseek.py) e corre em paralelo com os outros dois.

Uma busca por nome vira um `TrackResult` com o que cada plataforma achou,
já ordenado por relevância/pontuação — a UI decide o que mostrar.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import bandcamp, beatport
from .bandcamp import BandcampTrack
from .beatport import BeatportTrack
from .soulseek import Candidate, SoulseekConfig, SoulseekEngine, dedupe_by_file, filter_candidates

# Beatport + Bandcamp não escalam bem com muita concorrência de uma vez —
# poucos workers evita parecer um ataque de força bruta nos servidores deles.
MAX_STORE_WORKERS = 3
STAGGER_SECONDS = 1.5   # intervalo entre disparos de queries diferentes


@dataclass
class TrackResult:
    """O que se sabe sobre uma faixa em cada plataforma."""

    query: str
    beatport: list[BeatportTrack] = field(default_factory=list)
    bandcamp: list[BandcampTrack] = field(default_factory=list)
    soulseek: list[Candidate] = field(default_factory=list)
    done: bool = False

    @property
    def beatport_best(self) -> Optional[BeatportTrack]:
        return self.beatport[0] if self.beatport else None

    @property
    def bandcamp_best(self) -> Optional[BandcampTrack]:
        return self.bandcamp[0] if self.bandcamp else None

    @property
    def soulseek_best(self) -> Optional[Candidate]:
        return dedupe_by_file(self.soulseek)[0] if self.soulseek else None

    @property
    def soulseek_best_by_format(self) -> dict[str, Optional[Candidate]]:
        """Melhor candidato em cada formato-alvo (FLAC, WAV, MP3), pra escolher."""
        best: dict[str, Optional[Candidate]] = {"flac": None, "wav": None, "mp3": None}
        for c in dedupe_by_file(self.soulseek):
            if c.ext in best and best[c.ext] is None:
                best[c.ext] = c
        return best

    @property
    def has_any(self) -> bool:
        return bool(self.beatport or self.bandcamp or self.soulseek)


class Aggregator:
    """Orquestra as três fontes. Mantém uma instância só do motor Soulseek."""

    def __init__(
        self,
        soulseek_engine: Optional[SoulseekEngine] = None,
        soulseek_config: Optional[SoulseekConfig] = None,
        log_callback: Optional[Callable[[str], None]] = None,
        beatport_limit: int = 6,
        bandcamp_limit: int = 5,
    ):
        self.log = log_callback or (lambda *_: None)
        self.soulseek = soulseek_engine or SoulseekEngine(
            soulseek_config, log_callback=self.log,
        )
        self.beatport_limit = beatport_limit
        self.bandcamp_limit = bandcamp_limit
        self._stores = ThreadPoolExecutor(max_workers=MAX_STORE_WORKERS, thread_name_prefix="store")

    def shutdown(self) -> None:
        self._stores.shutdown(wait=False, cancel_futures=True)
        try:
            self.soulseek.shutdown()
        except Exception:
            pass

    # ---------- uma faixa ----------

    def search_one(
        self,
        query: str,
        soulseek_seconds: Optional[float] = None,
        on_update: Optional[Callable[[TrackResult], None]] = None,
    ) -> TrackResult:
        """Busca uma faixa nas três fontes. Bloqueia até todas terminarem.

        `on_update` é chamado a cada fonte que termina (Beatport e Bandcamp
        costumam ser rápidos; o Soulseek é o que demora, por isso ele também
        reporta parciais durante a própria janela de coleta).
        """
        result = TrackResult(query=query)

        def notify():
            if on_update:
                try:
                    on_update(result)
                except Exception:
                    pass

        def run_beatport():
            try:
                result.beatport = beatport.search(query, limit=self.beatport_limit)
            except Exception as e:
                self.log(f"Beatport falhou para '{query}': {e}")
            notify()

        def run_bandcamp():
            try:
                result.bandcamp = bandcamp.search(query, limit=self.bandcamp_limit)
            except Exception as e:
                self.log(f"Bandcamp falhou para '{query}': {e}")
            notify()

        def run_soulseek():
            def on_partial(found):
                cfg = self.soulseek.config
                result.soulseek = filter_candidates(found, cfg)
                notify()
            try:
                found = self.soulseek.search(query, seconds=soulseek_seconds, on_partial=on_partial)
                result.soulseek = filter_candidates(found, self.soulseek.config)
            except Exception as e:
                self.log(f"Soulseek falhou para '{query}': {e}")
            notify()

        futures = [
            self._stores.submit(run_beatport),
            self._stores.submit(run_bandcamp),
        ]
        soulseek_thread = threading.Thread(target=run_soulseek, daemon=True)
        soulseek_thread.start()

        for f in futures:
            f.result()
        soulseek_thread.join()

        result.done = True
        notify()
        return result

    # ---------- várias faixas ----------

    def search_many(
        self,
        queries: list[str],
        soulseek_seconds: Optional[float] = None,
        on_update: Optional[Callable[[str, TrackResult], None]] = None,
        stagger: float = STAGGER_SECONDS,
    ) -> dict[str, TrackResult]:
        """Busca várias faixas, uma de cada vez na fila do Soulseek (com
        intervalo entre elas), mas com Beatport/Bandcamp podendo se
        adiantar em paralelo — eles não têm o mesmo limite de rajada."""
        results: dict[str, TrackResult] = {}
        threads = []

        for i, query in enumerate(queries):
            def worker(q=query, delay=i * stagger):
                if delay:
                    time.sleep(delay)
                cb = (lambda r, qq=q: on_update(qq, r)) if on_update else None
                results[q] = self.search_one(q, soulseek_seconds=soulseek_seconds, on_update=cb)

            t = threading.Thread(target=worker, daemon=True)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()
        return results


if __name__ == "__main__":  # pragma: no cover - checagem manual rápida
    import sys

    termo = " ".join(sys.argv[1:]) or "Blue Monday"
    agg = Aggregator(log_callback=lambda m: print(f"  log: {m}"))
    try:
        r = agg.search_one(termo, soulseek_seconds=8)
        print(f"\nBeatport: {r.beatport_best.price_display if r.beatport_best else '—'} "
              f"({len(r.beatport)} resultado(s))")
        print(f"Bandcamp: {r.bandcamp_best.price_label if r.bandcamp_best else '—'} "
              f"({len(r.bandcamp)} resultado(s))")
        print(f"Soulseek: {r.soulseek_best.quality_label if r.soulseek_best else '—'} "
              f"({len(r.soulseek)} resultado(s))")
    finally:
        agg.shutdown()
