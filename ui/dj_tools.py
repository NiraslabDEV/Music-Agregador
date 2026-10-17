"""Ferramentas de DJ: BPM/tom da biblioteca, sugestões Camelot e crates.

Mixin do AggregatorView — usa o player, os snacks e a biblioteca dele. A
análise roda numa thread só, em fila, pra não disputar CPU com a interface;
o resultado fica em cache (core/crates.py) e só é refeito se o arquivo mudar.
"""

import os
import threading
import time
import weakref
from collections import deque
from typing import Callable, Optional

import flet as ft

from core import analysis
from core.camelot import (
    WHEEL_COLORS, camelot_color, camelot_label, harmonic_order, match, to_camelot,
)
from core.crates import LibraryStore
from ui.theme import (
    BORDER, FIELD_BG, FREE_COLOR, INK, MUTED, PRIMARY, PRIMARY_SOFT, RADIUS_MD, RADIUS_SM,
    SUBTLE, SURFACE, _plural, card, outline_style, pstyle, section_title,
)

WARN_COLOR = "#D97706"
SUGGESTIONS_MAX = 15
LIBRARY_PAGE = 40            # linhas por vez: 100+ linhas de uma vez travam a janela
STARTUP_ANALYSIS_DELAY = 4.0  # deixa o app abrir antes de começar a analisar

LIBRARY_SORTS = {"recent": "Mais recentes", "bpm": "BPM", "key": "Tom (Camelot)"}


def _title_of(label: str) -> str:
    title, _, fmt = (label or "").rpartition(" · ")
    return title or label or ""


def _camelot_sort_key(code: Optional[str]) -> tuple:
    if not code:
        return (99, "Z")
    return (int(code[:-1]), code[-1])


class DjToolsMixin:
    # ================= estado =================

    def _dj_init(self) -> None:
        self.store = LibraryStore()
        self._analysis: dict[str, dict] = self.store.all_analysis()
        self._analysis_queue: deque[str] = deque()
        self._analysis_lock = threading.Lock()
        self._analysis_running = False
        self._analysis_done = 0
        self._pill_rows: dict[str, weakref.WeakSet] = {}  # path -> etiquetas BPM/tom na tela
        self._color_filter: Optional[int] = None          # número da roda Camelot (1-12)
        self._library_shown = LIBRARY_PAGE
        self._crates: list[dict] = self.store.crates()
        ids = [c["id"] for c in self._crates]
        last = self.store.get_pref("last_crate")
        self._selected_crate: Optional[int] = (
            int(last) if last and int(last) in ids else (ids[0] if ids else None))
        self._suggest_for: Optional[tuple[str, str]] = None  # (path, título) do diálogo aberto
        self._dialog: Optional[ft.AlertDialog] = None

    def _dj_build_controls(self) -> None:
        self.library_sort = ft.Dropdown(
            label="Ordenar", value="recent", width=170, dense=True,
            border_radius=RADIUS_SM, filled=True, bgcolor=FIELD_BG, border_color="transparent",
            options=[ft.dropdown.Option(k, v) for k, v in LIBRARY_SORTS.items()],
            on_change=lambda e: self._reset_library_paging(),
        )
        self.color_neighbors = ft.Checkbox(
            label="+ vizinhos (±1)", value=False, active_color=PRIMARY,
            tooltip="Mostra também as cores ao lado na roda — também mixam bem",
            on_change=lambda e: self._reset_library_paging())
        self.color_filter_row = ft.Row(spacing=6, scroll=ft.ScrollMode.AUTO,
                                       vertical_alignment=ft.CrossAxisAlignment.CENTER)
        self.analysis_ring = ft.ProgressRing(width=14, height=14, stroke_width=2, color=PRIMARY)
        self.analysis_text = ft.Text("", size=11, color=MUTED)
        self.analysis_status = ft.Row([self.analysis_ring, self.analysis_text], spacing=6,
                                      visible=False, tight=True)

        self.crate_list = ft.ListView(spacing=6, expand=True)
        self.crate_detail = ft.Column(spacing=10, expand=True, tight=True)
        self.suggest_list = ft.ListView(spacing=6, height=420, width=560)

    # ================= análise =================

    def _track_meta(self, path: str) -> tuple[Optional[float], Optional[str]]:
        row = self._analysis.get(path) or {}
        return row.get("bpm"), row.get("camelot")

    def _needs_analysis(self, path: str) -> bool:
        """Igual a store.needs_analysis, mas contra o cache em memória: na
        abertura do app são centenas de faixas e uma consulta SQL por faixa
        somava segundos."""
        try:
            st = os.stat(path)
        except OSError:
            return False
        row = self._analysis.get(path)
        if row is None:
            return True
        if row.get("source") == "manual":
            return False
        return (row.get("mtime"), row.get("size")) != (st.st_mtime, st.st_size)

    def _queue_analysis(self, paths: list[str], delay: float = 0.0) -> None:
        with self._analysis_lock:
            pending = set(self._analysis_queue)
            for path in dict.fromkeys(paths):
                if path not in pending and self._needs_analysis(path):
                    self._analysis_queue.append(path)
            if not self._analysis_queue or self._analysis_running:
                return
            self._analysis_running = True
            self._analysis_done = 0
        threading.Thread(target=self._analysis_worker, args=(delay,), daemon=True).start()

    def _analysis_worker(self, delay: float = 0.0) -> None:
        time.sleep(delay)
        self._update_analysis_status()
        while True:
            with self._analysis_lock:
                if not self._analysis_queue:
                    self._analysis_running = False
                    break
                path = self._analysis_queue[0]
            try:
                result = analysis.analyze(path)
                self.store.save_analysis(path, result.bpm, result.camelot, result.source)
            except Exception as ex:
                self.log(f"Análise de BPM/tom falhou em {os.path.basename(path)}: {ex}")
                self.store.save_analysis(path, None, None, "erro", str(ex))
            self._analysis[path] = self.store.analysis_for(path) or {}
            with self._analysis_lock:
                if self._analysis_queue and self._analysis_queue[0] == path:
                    self._analysis_queue.popleft()
                self._analysis_done += 1
            # Só as etiquetas daquela faixa — nunca a lista inteira.
            self._refresh_pills(path)
            self._update_analysis_status()
            # Com música tocando, cede a CPU pro player entre uma faixa e outra.
            time.sleep(0.3 if self._playing_key else 0.02)
        self._update_analysis_status()
        # Ordem, filtro de cor e transições dos crates dependem da análise.
        if self.library_sort.value != "recent" or self._color_filter is not None:
            self._render_library()
        self._render_crates()

    def _update_analysis_status(self) -> None:
        remaining = len(self._analysis_queue)
        running = self._analysis_running and remaining > 0
        self.analysis_status.visible = running
        if running:
            total = self._analysis_done + remaining
            self.analysis_text.value = f"Analisando BPM e tom… {self._analysis_done}/{total}"
        self._update_controls(self.analysis_status)

    # ================= peças visuais =================

    def _bpm_pill(self, bpm: Optional[float]) -> ft.Container:
        text = "—" if not bpm else (f"{bpm:.0f}" if float(bpm).is_integer() else f"{bpm:.1f}")
        return ft.Container(
            content=ft.Row([ft.Text(text, size=12, weight=ft.FontWeight.BOLD, color=INK),
                            ft.Text("BPM", size=9, color=MUTED)], spacing=3, tight=True,
                           vertical_alignment=ft.CrossAxisAlignment.END),
            bgcolor=FIELD_BG, border_radius=8, width=66, alignment=ft.alignment.center,
            padding=ft.padding.symmetric(horizontal=8, vertical=5),
        )

    def _key_pill(self, code: Optional[str], analyzing: bool = False) -> ft.Container:
        color = camelot_color(code)
        if not code:
            return ft.Container(
                content=ft.Text("…" if analyzing else "—", size=12, color=MUTED,
                                weight=ft.FontWeight.BOLD),
                bgcolor=FIELD_BG, border_radius=8, width=66, alignment=ft.alignment.center,
                padding=ft.padding.symmetric(horizontal=8, vertical=5),
                tooltip="Analisando…" if analyzing else "Tom desconhecido",
            )
        return ft.Container(
            content=ft.Row([ft.Text(code, size=12, weight=ft.FontWeight.BOLD, color=color),
                            ft.Text(camelot_label(code), size=9, color=MUTED)],
                           spacing=4, tight=True, vertical_alignment=ft.CrossAxisAlignment.END),
            bgcolor=ft.colors.with_opacity(0.14, color),
            border=ft.border.all(1, ft.colors.with_opacity(0.35, color)),
            border_radius=8, width=66, alignment=ft.alignment.center,
            padding=ft.padding.symmetric(horizontal=8, vertical=4),
            tooltip=f"Camelot {code} = {camelot_label(code)}",
        )

    def _pill_controls(self, path: str) -> list:
        bpm, code = self._track_meta(path)
        return [self._bpm_pill(bpm), self._key_pill(code, path in self._analysis_queue)]

    def _meta_pills(self, path: str) -> ft.Row:
        row = ft.Row(self._pill_controls(path), spacing=6, tight=True)
        self._pill_rows.setdefault(path, weakref.WeakSet()).add(row)
        return row

    def _refresh_pills(self, path: str) -> None:
        rows = list(self._pill_rows.get(path, ()))
        for row in rows:
            row.controls = self._pill_controls(path)
        self._update_controls(*rows)

    def _play_button(self, path: str, title: str, subtitle: str, size: int = 30):
        if not path or not os.path.exists(path):
            return ft.IconButton(ft.icons.PLAY_CIRCLE_FILL_ROUNDED, icon_color=BORDER,
                                 icon_size=size, disabled=True)
        return self._preview_button(
            f"file:{path}", PRIMARY, lambda: self.media.url_for(path),
            title=title, subtitle=subtitle, size=size,
            play_icon=ft.icons.PLAY_CIRCLE_FILL_ROUNDED, idle_tooltip="Ouvir aqui no app")

    def _crate_menu(self, path: str, label: str) -> ft.PopupMenuButton:
        items = [ft.PopupMenuItem(text=f"{c['name']}  ({c['count']})",
                                  icon=ft.icons.QUEUE_MUSIC_ROUNDED,
                                  on_click=lambda e, cid=c["id"]: self._add_to_crate(cid, path, label))
                 for c in self._crates]
        if items:
            items.append(ft.PopupMenuItem())
        items.append(ft.PopupMenuItem(text="Novo crate…", icon=ft.icons.ADD_ROUNDED,
                                      on_click=lambda e: self._new_crate(add=(path, label))))
        return ft.PopupMenuButton(icon=ft.icons.PLAYLIST_ADD_ROUNDED, icon_color=MUTED,
                                  tooltip="Adicionar a um crate", items=items)

    def _library_actions(self, entry: dict, title: str, path: str, exists: bool) -> list:
        # Menus prontos em cada linha multiplicavam os controles da biblioteca
        # (um item por crate, por faixa); aqui eles só existem depois do clique.
        return [
            ft.IconButton(ft.icons.AUTO_AWESOME_ROUNDED, icon_color=PRIMARY, icon_size=20,
                          tooltip="O que mixa bem depois desta (Camelot + BPM)",
                          disabled=not exists,
                          on_click=lambda e: self._open_suggestions(path, title)),
            ft.IconButton(ft.icons.MORE_VERT_ROUNDED, icon_color=MUTED, icon_size=20,
                          tooltip="Adicionar a crate e mais opções",
                          on_click=lambda e: self._open_track_actions(entry, title, path, exists)),
        ]

    def _open_track_actions(self, entry: dict, title: str, path: str, exists: bool) -> None:
        label = entry.get("label") or title

        def then(fn):
            def handler(e):
                self._close_dialog()
                fn()
            return handler

        def action(icon, text, fn, disabled=False):
            return ft.TextButton(
                content=ft.Row([ft.Icon(icon, size=18, color=MUTED if disabled else INK),
                                ft.Text(text, size=13, color=MUTED if disabled else INK)],
                               spacing=12),
                disabled=disabled, on_click=then(fn),
                style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=RADIUS_SM),
                                     padding=ft.padding.symmetric(horizontal=12, vertical=12)),
            )

        crate_chips = [
            ft.OutlinedButton(f"{c['name']} ({c['count']})", icon=ft.icons.QUEUE_MUSIC_ROUNDED,
                              style=outline_style(PRIMARY), disabled=not exists,
                              on_click=then(lambda cid=c["id"]: self._add_to_crate(cid, path, label)))
            for c in self._crates
        ]
        crate_chips.append(ft.TextButton("Novo crate…", icon=ft.icons.ADD_ROUNDED, disabled=not exists,
                                         on_click=lambda e: self._new_crate(add=(path, label))))
        self._show_dialog(ft.AlertDialog(
            title=ft.Text(title, size=16, weight=ft.FontWeight.BOLD, color=INK,
                          max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
            content=ft.Column([
                ft.Text("Adicionar a um crate", size=12, weight=ft.FontWeight.BOLD, color=MUTED),
                ft.Row(crate_chips, wrap=True, spacing=8, run_spacing=8),
                ft.Divider(height=8, color=BORDER),
                action(ft.icons.EDIT_ROUNDED, "Corrigir BPM/tom",
                       lambda: self._edit_analysis(path, title), not exists),
                action(ft.icons.OPEN_IN_NEW_ROUNDED, "Abrir no player do computador",
                       lambda: self._open_file(path), not exists),
                action(ft.icons.FOLDER_OUTLINED, "Mostrar na pasta",
                       lambda: self._open_file(path, reveal=True), not exists),
                action(ft.icons.MANAGE_SEARCH_ROUNDED, "Buscar de novo (preço, outras versões)",
                       lambda: self._repeat_search(title)),
                action(ft.icons.DELETE_OUTLINE_ROUNDED,
                       "Tirar da lista (o arquivo continua no disco)",
                       lambda: self._remove_from_library(entry.get("id"))),
            ], spacing=4, tight=True, width=420),
            actions=[ft.TextButton("Fechar", on_click=lambda e: self._close_dialog())],
        ))

    def _sort_library(self, entries: list[dict]) -> list[dict]:
        mode = self.library_sort.value
        if mode == "bpm":
            return sorted(entries, key=lambda e: self._track_meta(e.get("local_path"))[0] or 999)
        if mode == "key":
            return sorted(entries, key=lambda e: (
                _camelot_sort_key(self._track_meta(e.get("local_path"))[1]),
                self._track_meta(e.get("local_path"))[0] or 999))
        return entries

    def _color_matches(self, code: Optional[str]) -> bool:
        if self._color_filter is None:
            return True
        if not code:
            return False
        step = (int(code[:-1]) - self._color_filter) % 12
        return step == 0 or (bool(self.color_neighbors.value) and step in (1, 11))

    def _set_color_filter(self, number: Optional[int]) -> None:
        self._color_filter = None if number == self._color_filter else number
        self._reset_library_paging()

    def _reset_library_paging(self) -> None:
        self._library_shown = LIBRARY_PAGE
        self._render_library()

    def _render_color_filter(self) -> None:
        counts = [0] * 13
        for e in self._library:
            code = self._track_meta(e.get("local_path"))[1]
            if code:
                counts[int(code[:-1])] += 1
        chips = [ft.Text("Cor", size=11, weight=ft.FontWeight.BOLD, color=MUTED)]
        for n in range(1, 13):
            color, selected = WHEEL_COLORS[n - 1], self._color_filter == n
            chips.append(ft.Container(
                content=ft.Text(str(n), size=11, weight=ft.FontWeight.BOLD,
                                color="#14121F"),  # escuro lê bem em todas as 12 cores
                width=30, height=30, border_radius=15, bgcolor=color,
                alignment=ft.alignment.center, opacity=1 if counts[n] else 0.3,
                border=ft.border.all(3, INK) if selected else None,
                tooltip=f"{n}A / {n}B ({camelot_label(f'{n}A')} / {camelot_label(f'{n}B')})"
                        f" · {_plural(counts[n], 'faixa')}",
                on_click=lambda e, num=n: self._set_color_filter(num),
            ))
        chips.append(ft.Container(width=6))
        chips.append(self.color_neighbors)
        if self._color_filter is not None:
            chips.append(ft.TextButton("Limpar cor", icon=ft.icons.CLOSE_ROUNDED,
                                       on_click=lambda e: self._set_color_filter(None)))
        self.color_filter_row.controls = chips

    def _library_matches(self, entry: dict, needle: str) -> bool:
        path = entry.get("local_path") or ""
        code = self._track_meta(path)[1]
        if not self._color_matches(code):
            return False
        if not needle:
            return True
        haystack = [entry.get("label") or "", os.path.basename(path)]
        if code:
            haystack += [code, camelot_label(code)]
        if needle.upper() == (code or "").upper():  # "8a" acha só 8A, não 18A
            return True
        return any(needle in h.casefold() for h in haystack)

    # ================= diálogos =================

    def _show_dialog(self, dialog: ft.AlertDialog) -> None:
        self._close_dialog()
        self._dialog = dialog
        self.page.overlay.append(dialog)
        dialog.open = True
        self._safe_update()

    def _close_dialog(self) -> None:
        dialog, self._dialog = self._dialog, None
        self._suggest_for = None
        if dialog is not None:
            dialog.open = False
            self._safe_update()
            if dialog in self.page.overlay:
                self.page.overlay.remove(dialog)

    def _prompt(self, title: str, fields: list[ft.TextField],
                on_ok: Callable[[list[str]], Optional[str]], ok_text: str = "Salvar") -> None:
        """Diálogo de texto. `on_ok` devolve uma mensagem de erro, ou None se deu certo."""
        error = ft.Text("", size=12, color="#DC2626", visible=False)

        def submit(e=None):
            message = on_ok([(f.value or "").strip() for f in fields])
            if message:
                error.value, error.visible = message, True
                self._safe_update()
            else:
                self._close_dialog()

        for f in fields:
            f.on_submit = submit
        self._show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text(title, weight=ft.FontWeight.BOLD, color=INK),
            content=ft.Column([*fields, error], tight=True, spacing=12, width=360),
            actions=[ft.TextButton("Cancelar", on_click=lambda e: self._close_dialog()),
                     ft.ElevatedButton(ok_text, style=pstyle(PRIMARY), on_click=submit)],
            actions_alignment=ft.MainAxisAlignment.END,
        ))

    @staticmethod
    def _field(label: str, value: str = "", hint: str = "") -> ft.TextField:
        return ft.TextField(label=label, value=value, hint_text=hint, autofocus=True,
                            border_radius=RADIUS_SM, filled=True, bgcolor=FIELD_BG,
                            border_color="transparent", focused_border_color=PRIMARY)

    def _edit_analysis(self, path: str, title: str) -> None:
        bpm, code = self._track_meta(path)
        bpm_field = self._field("BPM", f"{bpm:g}" if bpm else "", "ex.: 128")
        key_field = self._field("Tom", code or "", "ex.: 8A, Am, F#m, Db")

        def save(values):
            bpm_text, key_text = values
            new_bpm = None
            if bpm_text:
                try:
                    new_bpm = round(float(bpm_text.replace(",", ".")), 1)
                except ValueError:
                    return "BPM precisa ser um número."
                if not 40 <= new_bpm <= 250:
                    return "BPM fora do normal (40–250)."
            new_code = to_camelot(key_text) if key_text else None
            if key_text and not new_code:
                return "Não entendi o tom. Use Camelot (8A) ou nota (Am, F#, Dbm)."
            self.store.save_analysis(path, new_bpm, new_code, "manual")
            self._analysis[path] = self.store.analysis_for(path) or {}
            self._render_library()
            self._render_crates()
            return None

        self._prompt(f"Corrigir BPM/tom — {title}", [bpm_field, key_field], save)

    # ================= sugestões =================

    def _suggestions(self, path: str) -> list[tuple[dict, object]]:
        bpm, code = self._track_meta(path)
        seen, out = {path}, []
        for entry in self._library:
            other = entry.get("local_path") or ""
            if other in seen or not os.path.exists(other):
                continue
            seen.add(other)
            o_bpm, o_code = self._track_meta(other)
            if not o_bpm and not o_code:
                continue
            out.append((entry, match(code, bpm, o_code, o_bpm)))
        out.sort(key=lambda pair: pair[1].score, reverse=True)
        return [p for p in out if p[1].score >= 0.45][:SUGGESTIONS_MAX]

    def _open_suggestions(self, path: str, title: str) -> None:
        self._show_dialog(ft.AlertDialog(
            title=ft.Row([ft.Icon(ft.icons.AUTO_AWESOME_ROUNDED, color=PRIMARY),
                          ft.Text("Mixa bem depois de", weight=ft.FontWeight.BOLD, color=INK)],
                         spacing=8),
            content=self.suggest_list,
            actions=[ft.TextButton("Fechar", on_click=lambda e: self._close_dialog())],
        ))
        self._suggest_for = (path, title)
        self._render_suggestions()

    def _render_suggestions(self) -> None:
        if not self._suggest_for:
            return
        path, title = self._suggest_for
        bpm, code = self._track_meta(path)
        lst = self.suggest_list
        lst.controls.clear()
        lst.controls.append(ft.Container(
            content=ft.Row([
                self._meta_pills(path),
                ft.Text(title, size=13, weight=ft.FontWeight.BOLD, color=INK, expand=True,
                        no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
            ], spacing=10),
            bgcolor=PRIMARY_SOFT, border_radius=RADIUS_SM, padding=10,
        ))
        if not bpm and not code:
            lst.controls.append(ft.Text("Essa faixa ainda não foi analisada.", size=12, color=MUTED))
        suggestions = self._suggestions(path)
        if not suggestions and (bpm or code):
            lst.controls.append(ft.Container(
                content=ft.Text("Nada na biblioteca combina bem com ela ainda. Baixe mais "
                                f"faixas em {code or '?'} ou vizinhas na roda Camelot.",
                                size=12, color=MUTED), padding=10))
        for entry, m in suggestions:
            other = entry.get("local_path")
            o_title = _title_of(entry.get("label"))
            lst.controls.append(ft.Container(
                content=ft.Row([
                    self._play_button(other, o_title, "Sugestão · Biblioteca", size=26),
                    ft.Column([
                        ft.Text(o_title, size=12, weight=ft.FontWeight.W_600, color=INK,
                                no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Text(f"{m.key_reason} · {m.bpm_reason}", size=11,
                                color=FREE_COLOR if m.harmonic else MUTED),
                    ], spacing=1, tight=True, expand=True),
                    self._meta_pills(other),
                    self._crate_menu(other, entry.get("label") or o_title),
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                bgcolor=SUBTLE, border_radius=RADIUS_SM,
                padding=ft.padding.symmetric(horizontal=8, vertical=6),
            ))
        self._safe_update()

    # ================= crates =================

    def _reload_crates(self) -> None:
        self._crates = self.store.crates()
        ids = [c["id"] for c in self._crates]
        if self._selected_crate not in ids:
            self._selected_crate = ids[0] if ids else None
        self._render_crates()

    def _new_crate(self, add: Optional[tuple[str, str]] = None) -> None:
        def create(values):
            name = values[0]
            if not name:
                return "Dê um nome pro crate."
            crate_id = self.store.create_crate(name)
            self._selected_crate = crate_id
            self.store.set_pref("last_crate", str(crate_id))
            if add:
                self.store.add_to_crate(crate_id, *add)
                self._snack(f"Adicionada a “{name}”.", FREE_COLOR)
            self._reload_crates()
            return None

        self._prompt("Novo crate", [self._field("Nome", hint="ex.: Warm-up, Pico da noite")],
                     create, ok_text="Criar")

    def _rename_crate(self, crate: dict) -> None:
        def rename(values):
            if not values[0]:
                return "Dê um nome pro crate."
            self.store.rename_crate(crate["id"], values[0])
            self._reload_crates()
            return None

        self._prompt("Renomear crate", [self._field("Nome", crate["name"])], rename)

    def _delete_crate(self, crate: dict) -> None:
        def delete(e):
            self.store.delete_crate(crate["id"])
            self._close_dialog()
            self._reload_crates()

        self._show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text(f"Apagar “{crate['name']}”?", weight=ft.FontWeight.BOLD),
            content=ft.Text("Só o crate é apagado — as músicas continuam na biblioteca e no disco.",
                            size=13, color=MUTED),
            actions=[ft.TextButton("Cancelar", on_click=lambda e: self._close_dialog()),
                     ft.ElevatedButton("Apagar", style=pstyle("#DC2626", ft.colors.WHITE),
                                       on_click=delete)],
            actions_alignment=ft.MainAxisAlignment.END,
        ))

    def _add_to_crate(self, crate_id: int, path: str, label: str) -> None:
        name = next((c["name"] for c in self._crates if c["id"] == crate_id), "crate")
        if self.store.add_to_crate(crate_id, path, label):
            self._snack(f"Adicionada a “{name}”.", FREE_COLOR)
        else:
            self._snack(f"Essa faixa já está em “{name}”.")
        self._reload_crates()

    def _select_crate(self, crate_id: int) -> None:
        self._selected_crate = crate_id
        self.store.set_pref("last_crate", str(crate_id))
        self._render_crates()

    def _crate_action(self, fn) -> None:
        fn()
        self._reload_crates()

    def _harmonic_sort(self, crate_id: int) -> None:
        tracks = self.store.crate_tracks(crate_id)
        enriched = [dict(t, bpm=self._track_meta(t["path"])[0],
                         camelot=self._track_meta(t["path"])[1]) for t in tracks]
        ordered = harmonic_order(enriched)
        self.store.set_order(crate_id, [t["id"] for t in ordered])
        self._snack("Crate reordenado pela roda Camelot (a 1ª faixa ficou no lugar).")
        self._render_crates()

    def _crates_card(self) -> ft.Container:
        sidebar = ft.Container(
            width=230,
            content=ft.Column([
                ft.ElevatedButton("Novo crate", icon=ft.icons.ADD_ROUNDED, style=pstyle(PRIMARY),
                                  on_click=lambda e: self._new_crate()),
                ft.Container(content=self.crate_list, height=470),
            ], spacing=12, tight=True),
        )
        return card(
            section_title(ft.icons.QUEUE_MUSIC_ROUNDED, "Crates",
                          "Monte sets com as faixas da biblioteca — o app avisa quais "
                          "transições batem no tom e no BPM.", color=PRIMARY),
            ft.Divider(height=1, color=BORDER),
            ft.Row([sidebar, ft.VerticalDivider(width=1, color=BORDER),
                    ft.Container(content=self.crate_detail, expand=True, height=520)],
                   spacing=16, vertical_alignment=ft.CrossAxisAlignment.START),
        )

    def _render_crates(self) -> None:
        if not self._showing_crates():
            return  # _go_to desenha ao abrir a aba
        self.crate_list.controls.clear()
        for c in self._crates:
            selected = c["id"] == self._selected_crate
            self.crate_list.controls.append(ft.Container(
                content=ft.Row([
                    ft.Icon(ft.icons.QUEUE_MUSIC_ROUNDED, size=18,
                            color=PRIMARY if selected else MUTED),
                    ft.Text(c["name"], size=13, color=INK, expand=True,
                            weight=ft.FontWeight.BOLD if selected else ft.FontWeight.NORMAL,
                            no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(str(c["count"]), size=11, color=MUTED),
                ], spacing=8),
                bgcolor=PRIMARY_SOFT if selected else None, border_radius=RADIUS_SM,
                padding=ft.padding.symmetric(horizontal=12, vertical=10),
                on_click=lambda e, cid=c["id"]: self._select_crate(cid),
            ))
        if not self._crates:
            self.crate_list.controls.append(
                ft.Text("Nenhum crate ainda.", size=12, color=MUTED))
        self._render_crate_detail()
        self._safe_update()

    def _render_crate_detail(self) -> None:
        detail = self.crate_detail
        detail.controls.clear()
        crate = next((c for c in self._crates if c["id"] == self._selected_crate), None)
        if crate is None:
            detail.controls.append(ft.Container(
                content=ft.Column([
                    ft.Container(content=ft.Icon(ft.icons.QUEUE_MUSIC_ROUNDED, size=30,
                                                 color=PRIMARY),
                                 width=64, height=64, border_radius=32, bgcolor=PRIMARY_SOFT,
                                 alignment=ft.alignment.center),
                    ft.Text("Crie seu primeiro crate", size=15, weight=ft.FontWeight.BOLD,
                            color=INK),
                    ft.Text("Depois adicione faixas pelo botão “Adicionar a um crate” "
                            "na Biblioteca.", size=12, color=MUTED),
                ], spacing=10, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                padding=40, alignment=ft.alignment.center,
            ))
            return

        tracks = self.store.crate_tracks(crate["id"])
        bpms = [b for b in (self._track_meta(t["path"])[0] for t in tracks) if b]
        clashes = 0
        rows = []
        for i, t in enumerate(tracks):
            if i:
                prev = tracks[i - 1]
                p_bpm, p_code = self._track_meta(prev["path"])
                bpm, code = self._track_meta(t["path"])
                m = match(p_code, p_bpm, code, bpm)
                clashes += 0 if m.harmonic else 1
                rows.append(self._transition_row(m))
            rows.append(self._crate_track_row(crate["id"], i, t, len(tracks)))

        summary = _plural(len(tracks), "faixa")
        if bpms:
            summary += f" · {min(bpms):g}–{max(bpms):g} BPM"
        if len(tracks) > 1:
            summary += (" · todas as transições harmônicas" if not clashes
                        else f" · {_plural(clashes, 'transição', 'transições')} pra revisar")
        detail.controls.append(ft.Row([
            ft.Column([ft.Text(crate["name"], size=18, weight=ft.FontWeight.BOLD, color=INK),
                       ft.Text(summary, size=12, color=MUTED)], spacing=2, tight=True,
                      expand=True),
            ft.OutlinedButton("Ordenar por harmonia", icon=ft.icons.AUTO_FIX_HIGH_ROUNDED,
                              style=outline_style(PRIMARY), disabled=len(tracks) < 3,
                              tooltip="Mantém a 1ª faixa e encadeia o resto pela roda Camelot",
                              on_click=lambda e: self._harmonic_sort(crate["id"])),
            ft.IconButton(ft.icons.EDIT_OUTLINED, icon_color=MUTED, tooltip="Renomear",
                          on_click=lambda e: self._rename_crate(crate)),
            ft.IconButton(ft.icons.DELETE_OUTLINE_ROUNDED, icon_color=MUTED, tooltip="Apagar crate",
                          on_click=lambda e: self._delete_crate(crate)),
        ], vertical_alignment=ft.CrossAxisAlignment.CENTER))
        if not tracks:
            detail.controls.append(ft.Text(
                "Crate vazio. Na Biblioteca, use o botão “Adicionar a um crate”.",
                size=12, color=MUTED))
            return
        detail.controls.append(ft.ListView(rows, spacing=2, height=440))

    def _transition_row(self, m) -> ft.Container:
        ok = m.harmonic
        color = FREE_COLOR if ok else WARN_COLOR
        return ft.Container(
            content=ft.Row([
                ft.Icon(ft.icons.SOUTH_ROUNDED, size=14, color=color),
                ft.Text(f"{m.key_reason} · {m.bpm_reason}", size=11, color=color),
            ], spacing=6),
            padding=ft.padding.only(left=58, top=1, bottom=1),
        )

    def _crate_track_row(self, crate_id: int, index: int, t: dict, total: int) -> ft.Container:
        path, title = t["path"], _title_of(t["label"])
        exists = os.path.exists(path)
        store = self.store
        return ft.Container(
            content=ft.Row([
                ft.Text(f"{index + 1:02d}", size=12, color=MUTED, width=22,
                        weight=ft.FontWeight.BOLD),
                self._play_button(path, title, "Crate", size=28),
                ft.Text(title, size=13, weight=ft.FontWeight.W_600,
                        color=INK if exists else MUTED, expand=True,
                        no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS,
                        tooltip=None if exists else "arquivo não encontrado"),
                self._meta_pills(path),
                ft.IconButton(ft.icons.KEYBOARD_ARROW_UP_ROUNDED, icon_color=MUTED, icon_size=20,
                              tooltip="Subir", disabled=index == 0,
                              on_click=lambda e: self._crate_action(
                                  lambda: store.move(crate_id, t["id"], -1))),
                ft.IconButton(ft.icons.KEYBOARD_ARROW_DOWN_ROUNDED, icon_color=MUTED, icon_size=20,
                              tooltip="Descer", disabled=index == total - 1,
                              on_click=lambda e: self._crate_action(
                                  lambda: store.move(crate_id, t["id"], 1))),
                ft.IconButton(ft.icons.AUTO_AWESOME_ROUNDED, icon_color=PRIMARY, icon_size=18,
                              tooltip="O que mixa bem depois desta", disabled=not exists,
                              on_click=lambda e: self._open_suggestions(path, title)),
                ft.IconButton(ft.icons.CLOSE_ROUNDED, icon_color=MUTED, icon_size=18,
                              tooltip="Tirar do crate",
                              on_click=lambda e: self._crate_action(
                                  lambda: store.remove_from_crate(t["id"]))),
            ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            bgcolor=SUBTLE, border_radius=RADIUS_MD, border=ft.border.all(1, BORDER),
            padding=ft.padding.symmetric(horizontal=10, vertical=6),
        )

