"""Favoritas: faixas que a pessoa quer ter, mesmo sem fonte no Soulseek ainda.

Mixin do AggregatorView. Uma favorita é só o texto da busca + o que a última
verificação achou. "Verificar" refaz a busca nas três fontes; se o Soulseek
tiver a faixa, os botões de download aparecem ali mesmo. O resultado do
Soulseek não é guardado entre sessões (na rede P2P a fonte de ontem pode estar
offline hoje) — só o resumo, pra lista mostrar o que mudou.
"""

import threading
from typing import Optional

import flet as ft

from core.aggregator import TrackResult
from core.crates import favorite_status
from core.soulseek import dedupe_by_file
from ui.dj_tools import _title_of
from ui.theme import (
    BORDER, FIELD_BG, FREE_COLOR, FREE_SOFT, INK, MUTED, PRIMARY, RADIUS_MD,
    RADIUS_SM, STAR_COLOR, SUBTLE, WARN_COLOR, YOUTUBE_COLOR, BEATPORT_COLOR, BANDCAMP_COLOR,
    _plural, _relative_time, card, outline_style, pill, pstyle, section_title,
)

FAV_FILTERS = {
    "all": "Todas",
    "available": "Com fonte no Soulseek",
    "missing": "Ainda sem fonte",
    "never": "Nunca verificadas",
    "owned": "Já na biblioteca",
}


class FavoritesMixin:
    # ================= estado =================

    def _fav_init(self) -> None:
        self._favorites: list[dict] = self.store.favorites()
        self._fav_results: dict[int, TrackResult] = {}   # verificação desta sessão
        self._fav_checking: set[int] = set()
        self._fav_batch = {"running": False, "stop": False}

    def _fav_build_controls(self) -> None:
        self.fav_filter = ft.TextField(
            label="Procurar nas favoritas", prefix_icon=ft.icons.SEARCH, expand=True,
            border_radius=RADIUS_SM, filled=True, bgcolor=FIELD_BG,
            border_color="transparent", focused_border_color=PRIMARY,
            on_change=lambda e: self._render_favorites(),
        )
        self.fav_status_dd = ft.Dropdown(
            label="Mostrar", value="all", width=210, dense=True,
            border_radius=RADIUS_SM, filled=True, bgcolor=FIELD_BG, border_color="transparent",
            options=[ft.dropdown.Option(k, v) for k, v in FAV_FILTERS.items()],
            on_change=lambda e: self._render_favorites(),
        )
        self.fav_count = ft.Text("", size=12, color=MUTED)
        self.fav_check_all_btn = ft.ElevatedButton(
            "Verificar todas", icon=ft.icons.TRAVEL_EXPLORE_ROUNDED, style=pstyle(PRIMARY),
            tooltip="Busca de novo cada favorita que ainda não está na biblioteca",
            on_click=lambda e: self._toggle_check_all(),
        )
        self.fav_batch_text = ft.Text("", size=12, color=MUTED, visible=False)
        self.fav_list = ft.ListView(expand=True, spacing=8, auto_scroll=False)

    def _favorites_card(self) -> ft.Container:
        return card(
            ft.Row([
                section_title(ft.icons.STAR_ROUNDED, "Favoritas",
                              "Faixas que você quer ter, mesmo as que ainda não têm fonte no "
                              "Soulseek. Volte aqui e verifique de novo de tempos em tempos.",
                              color=STAR_COLOR),
                ft.Container(expand=True),
                ft.OutlinedButton("Adicionar", icon=ft.icons.ADD_ROUNDED,
                                  style=outline_style(STAR_COLOR),
                                  tooltip="Favoritar uma faixa sem precisar buscar antes",
                                  on_click=lambda e: self._add_favorite_manually()),
                self.fav_check_all_btn,
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.START),
            ft.Row([self.fav_filter, self.fav_status_dd, self.fav_count], spacing=12),
            self.fav_batch_text,
            ft.Divider(height=1, color=BORDER),
            ft.Container(content=self.fav_list, height=560),
        )

    # ================= dados =================

    def _favorite_for(self, query: str) -> Optional[dict]:
        key = " ".join((query or "").split()).casefold()
        return next((f for f in self._favorites if f["query"].casefold() == key), None)

    def _reload_favorites(self) -> None:
        self._favorites = self.store.favorites()
        self._render_favorites()

    @staticmethod
    def _store_info(result: Optional[TrackResult]) -> dict:
        if result is None:
            return {}
        bp, bc = result.beatport_best, result.bandcamp_best
        prices = []
        if bp and bp.price_display:
            prices.append(f"Beatport {bp.price_display}")
        if bc and bc.price_label:
            prices.append(f"Bandcamp {bc.price_label}")
        cover = (bp.cover_url if bp and bp.cover_url else "") or \
                (bc.cover_url if bc and bc.cover_url else "")
        return {"cover_url": cover, "beatport_url": bp.url if bp else "",
                "bandcamp_url": bc.url if bc else "", "price": " · ".join(prices)}

    def _owned_titles(self) -> set[str]:
        return {_title_of(e.get("label") or "").casefold() for e in self._library}

    def _record_favorite_result(self, query: str, result: TrackResult) -> None:
        """Toda busca concluída de uma faixa favoritada atualiza a favorita —
        tanto o botão "Verificar" quanto uma busca normal em Descobrir."""
        fav = self._favorite_for(query)
        if fav is None or not result.done:
            return
        self._fav_results[fav["id"]] = result
        if not self.engine.is_connected:
            return  # sem conexão o "não achou" do Soulseek não significa nada
        best = result.soulseek_best
        self.store.record_check(fav["id"], len(dedupe_by_file(result.soulseek)),
                                best.quality_label if best else "", **self._store_info(result))
        self._reload_favorites()

    # ================= estrela nos resultados =================

    def _fav_star_button(self, query: str, result: TrackResult) -> ft.IconButton:
        btn = ft.IconButton(icon_size=22)

        def paint():
            on = self._favorite_for(query) is not None
            btn.icon = ft.icons.STAR_ROUNDED if on else ft.icons.STAR_BORDER_ROUNDED
            btn.icon_color = STAR_COLOR if on else MUTED
            btn.tooltip = ("Tirar das favoritas" if on else
                           "Favoritar — fica guardada pra você verificar depois se apareceu "
                           "no Soulseek")

        def toggle(e):
            fav = self._favorite_for(query)
            if fav:
                self.store.remove_favorite(fav["id"])
                self._fav_results.pop(fav["id"], None)
                self._reload_favorites()
                self._snack(f"Tirada das favoritas: {query}")
            else:
                self.store.add_favorite(query, **self._store_info(result))
                self._reload_favorites()
                if result.done:
                    self._record_favorite_result(query, result)
                self._snack(f"Favoritada: {query}", STAR_COLOR, action="Ver favoritas",
                            on_action=lambda e: self._go_to(self.NAV_FAVORITES))
            paint()
            self._update_controls(btn)

        btn.on_click = toggle
        paint()
        return btn

    def _add_favorite_manually(self) -> None:
        field = self._field("Faixa", hint="Artista - Faixa (Remix)")

        def save(values):
            query = values[0]
            if not query:
                return "Digite o nome da faixa."
            if self._favorite_for(query):
                return "Essa faixa já está nas favoritas."
            self.store.add_favorite(query)
            self._reload_favorites()
            return None

        self._prompt("Nova favorita", [field], save, ok_text="Favoritar")

    def _remove_favorite(self, fav: dict) -> None:
        self.store.remove_favorite(fav["id"])
        self._fav_results.pop(fav["id"], None)
        self._reload_favorites()
        self._render_results()  # a estrela do card volta a ficar vazia

    # ================= verificação =================

    def _check_favorite(self, fav: dict) -> None:
        if fav["id"] in self._fav_checking:
            return
        if not self.engine.is_connected:
            self._snack("Conecte sua conta primeiro.", ft.colors.ORANGE_700)
            self._show_login_screen()
            return
        self._fav_checking.add(fav["id"])
        self._render_favorites()
        threading.Thread(target=self._run_check, args=(fav,), daemon=True).start()

    def _run_check(self, fav: dict) -> Optional[TrackResult]:
        try:
            result = self.aggregator.search_one(
                fav["query"], soulseek_seconds=float(self.window_dd.value or 10))
            self._record_favorite_result(fav["query"], result)
            return result
        except Exception as ex:
            self.log(f"Verificação da favorita '{fav['query']}' falhou: {ex}")
            return None
        finally:
            self._fav_checking.discard(fav["id"])
            self._render_favorites()

    def _toggle_check_all(self) -> None:
        if self._fav_batch["running"]:
            self._fav_batch["stop"] = True
            self.fav_check_all_btn.text = "Parando…"
            self.fav_check_all_btn.disabled = True
            self._safe_update()
            return
        if not self.engine.is_connected:
            self._snack("Conecte sua conta primeiro.", ft.colors.ORANGE_700)
            self._show_login_screen()
            return
        owned = self._owned_titles()
        targets = [f for f in self._favorites if f["query"].casefold() not in owned]
        if not targets:
            self._snack("Nada pra verificar — as favoritas já estão na biblioteca.")
            return
        self._fav_batch.update(running=True, stop=False)
        self.fav_check_all_btn.text = "Parar"
        self.fav_check_all_btn.icon = ft.icons.STOP_ROUNDED
        self.fav_batch_text.visible = True
        self._safe_update()
        threading.Thread(target=self._check_all_worker, args=(targets,), daemon=True).start()

    def _check_all_worker(self, targets: list[dict]) -> None:
        # Uma de cada vez: dezenas de buscas simultâneas no Soulseek viram
        # flood pro servidor, e a busca normal em Descobrir continua usável.
        newly_available, checked = [], 0
        for i, fav in enumerate(targets, 1):
            if self._fav_batch["stop"]:
                break
            self.fav_batch_text.value = f"Verificando {i} de {len(targets)}: {fav['query']}"
            self._update_controls(self.fav_batch_text)
            was = favorite_status(fav)
            self._fav_checking.add(fav["id"])
            self._render_favorites()
            result = self._run_check(fav)
            checked += 1
            if result is not None and result.soulseek and was != "available":
                newly_available.append(fav["query"])
        self._fav_batch.update(running=False, stop=False)
        self.fav_check_all_btn.text = "Verificar todas"
        self.fav_check_all_btn.icon = ft.icons.TRAVEL_EXPLORE_ROUNDED
        self.fav_check_all_btn.disabled = False
        self.fav_batch_text.visible = False
        self._render_favorites()
        if newly_available:
            self._snack(f"{_plural(len(newly_available), 'favorita')} apareceu(ram) no "
                        f"Soulseek: {', '.join(newly_available[:3])}"
                        + ("…" if len(newly_available) > 3 else ""), FREE_COLOR,
                        action="Ver", on_action=lambda e: self._show_fav_filter("available"))
        else:
            self._snack(f"{_plural(checked, 'favorita')} verificada(s) — nenhuma novidade "
                        "no Soulseek.")

    def _show_fav_filter(self, value: str) -> None:
        self.fav_status_dd.value = value
        self._go_to(self.NAV_FAVORITES)

    # ================= lista =================

    def _render_favorites(self) -> None:
        if not self._showing(self.NAV_FAVORITES):
            return  # _go_to desenha ao abrir a aba
        owned = self._owned_titles()
        needle = (self.fav_filter.value or "").strip().casefold()
        mode = self.fav_status_dd.value or "all"

        def visible(f):
            if needle and needle not in f["query"].casefold():
                return False
            is_owned = f["query"].casefold() in owned
            if mode == "owned":
                return is_owned
            return mode == "all" or (favorite_status(f) == mode and not is_owned)

        shown = [f for f in self._favorites if visible(f)]
        available = sum(1 for f in self._favorites if favorite_status(f) == "available"
                        and f["query"].casefold() not in owned)
        self.fav_count.value = (f"{len(shown)} de {len(self._favorites)}"
                                if len(shown) != len(self._favorites)
                                else _plural(len(self._favorites), "faixa"))
        if available:
            self.fav_count.value += f" · {available} com fonte"
        self.fav_list.controls = [self._favorite_row(f, f["query"].casefold() in owned)
                                  for f in shown]
        if not self._favorites:
            self.fav_list.controls.append(ft.Container(
                content=ft.Column([
                    ft.Container(content=ft.Icon(ft.icons.STAR_BORDER_ROUNDED, size=30,
                                                 color=STAR_COLOR),
                                 width=64, height=64, border_radius=32,
                                 bgcolor=ft.colors.with_opacity(0.13, STAR_COLOR),
                                 alignment=ft.alignment.center),
                    ft.Text("Nenhuma favorita ainda", size=15, weight=ft.FontWeight.BOLD,
                            color=INK),
                    ft.Text("Toque na estrela de uma faixa em Descobrir — mesmo que o "
                            "Soulseek não tenha ela hoje.", size=12, color=MUTED),
                ], spacing=10, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                padding=40, alignment=ft.alignment.center,
            ))
        elif not shown:
            self.fav_list.controls.append(
                ft.Text("Nenhuma favorita com esse filtro.", size=12, color=MUTED))
        self._safe_update()

    def _fav_status_line(self, fav: dict, owned: bool, checking: bool) -> ft.Control:
        if checking:
            return ft.Row([ft.ProgressRing(width=12, height=12, stroke_width=2, color=PRIMARY),
                           ft.Text("verificando nas três fontes…", size=11, color=MUTED)],
                          spacing=6)
        status = favorite_status(fav)
        when = _relative_time(fav.get("checked_at") or "")
        if owned:
            text, color = "Já está na sua biblioteca", PRIMARY
        elif status == "never":
            text, color = "Ainda não verificada no Soulseek", MUTED
        elif status == "missing":
            text, color = f"Sem fonte no Soulseek ({when})", WARN_COLOR
        else:
            best = f" · melhor: {fav['soulseek_best']}" if fav.get("soulseek_best") else ""
            text = (f"No Soulseek: {_plural(fav['soulseek_count'], 'fonte')}{best} ({when})")
            color = FREE_COLOR
        return ft.Text(text, size=11, color=color, weight=ft.FontWeight.W_600,
                       no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS)

    def _favorite_row(self, fav: dict, owned: bool) -> ft.Container:
        query = fav["query"]
        checking = fav["id"] in self._fav_checking
        result = self._fav_results.get(fav["id"])
        status = favorite_status(fav)

        actions = [
            ft.IconButton(ft.icons.REFRESH_ROUNDED, icon_color=PRIMARY, icon_size=20,
                          tooltip="Verificar agora (Soulseek, Beatport e Bandcamp)",
                          disabled=checking, on_click=lambda e: self._check_favorite(fav)),
            ft.IconButton(ft.icons.SMART_DISPLAY_ROUNDED, icon_color=YOUTUBE_COLOR, icon_size=20,
                          tooltip="Baixar o áudio do YouTube",
                          on_click=lambda e: self._open_youtube_dialog(query)),
        ]
        if fav.get("beatport_url"):
            actions.append(ft.IconButton(ft.icons.SELL_ROUNDED, icon_color=BEATPORT_COLOR,
                                         icon_size=18, tooltip="Ver no Beatport",
                                         url=fav["beatport_url"], url_target=ft.UrlTarget.BLANK))
        if fav.get("bandcamp_url"):
            actions.append(ft.IconButton(ft.icons.SELL_ROUNDED, icon_color=BANDCAMP_COLOR,
                                         icon_size=18, tooltip="Ver no Bandcamp",
                                         url=fav["bandcamp_url"], url_target=ft.UrlTarget.BLANK))
        actions += [
            ft.IconButton(ft.icons.MANAGE_SEARCH_ROUNDED, icon_color=MUTED, icon_size=20,
                          tooltip="Abrir a busca completa em Descobrir",
                          on_click=lambda e: self._repeat_search(query)),
            ft.IconButton(ft.icons.STAR_ROUNDED, icon_color=STAR_COLOR, icon_size=20,
                          tooltip="Tirar das favoritas",
                          on_click=lambda e: self._remove_favorite(fav)),
        ]

        subtitle = [self._fav_status_line(fav, owned, checking)]
        if fav.get("price"):
            subtitle.append(ft.Text(fav["price"], size=11, color=MUTED, no_wrap=True,
                                    overflow=ft.TextOverflow.ELLIPSIS))
        header = ft.Row([
            self._fav_cover(fav.get("cover_url") or ""),
            ft.Column([
                ft.Text(query, size=13, weight=ft.FontWeight.W_600, color=INK,
                        no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
                *subtitle,
            ], spacing=2, tight=True, expand=True),
            *actions,
        ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        body = [header]
        if result is not None and result.soulseek and not checking:
            body.append(self._fav_download_rows(query, result))
        elif status == "available" and result is None and not checking and not owned:
            body.append(ft.Text("Toque em ⟳ pra buscar as fontes de novo e baixar — na rede "
                                "P2P quem tinha a faixa ontem pode estar offline hoje.",
                                size=11, color=MUTED, italic=True))
        accent = FREE_COLOR if status == "available" and not owned else BORDER
        return ft.Container(
            content=ft.Column(body, spacing=8, tight=True),
            padding=ft.padding.symmetric(horizontal=12, vertical=10),
            bgcolor=SUBTLE, border_radius=RADIUS_MD,
            border=ft.border.all(1, accent),
        )

    def _fav_download_rows(self, query: str, result: TrackResult) -> ft.Container:
        by_format = result.soulseek_best_by_format
        return ft.Container(
            content=ft.Column([
                ft.Row([pill("Grátis", FREE_COLOR, FREE_SOFT, size=11,
                             icon=ft.icons.CHECK_CIRCLE_ROUNDED),
                        ft.TextButton(f"Ver todos os {_plural(len(result.soulseek), 'arquivo')}",
                                      icon=ft.icons.LIST_ROUNDED,
                                      on_click=lambda e: self._open_soulseek_all(query, result))],
                       alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                *[self._format_row(query, result, fmt, by_format[fmt]) for fmt in by_format],
            ], spacing=4, tight=True),
            padding=ft.padding.only(left=58, right=8),
        )

    @staticmethod
    def _fav_cover(url: str) -> ft.Container:
        icon = ft.Icon(ft.icons.STAR_ROUNDED, color=ft.colors.WHITE, size=20)
        return ft.Container(
            content=ft.Image(src=url, width=48, height=48, fit=ft.ImageFit.COVER,
                             border_radius=10, error_content=icon) if url else icon,
            width=48, height=48, border_radius=10, alignment=ft.alignment.center,
            gradient=ft.LinearGradient(begin=ft.alignment.top_left,
                                       end=ft.alignment.bottom_right,
                                       colors=[STAR_COLOR, "#EA580C"]),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        )
