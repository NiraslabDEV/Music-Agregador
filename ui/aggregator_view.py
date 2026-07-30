"""Tela principal: busca uma faixa e mostra Beatport, Bandcamp e Soulseek lado a lado.

Cada faixa buscada vira um card com capa de álbum (a melhor disponível entre
Beatport/Bandcamp) e três painéis coloridos por plataforma — preço em destaque
como "pill", link pra comprar ou botão pra baixar grátis. Os painéis reagem ao
hover (leve elevação + zoom) pra dar sensação de interatividade num app que,
por natureza, é basicamente uma lista de resultados.
"""

import threading
import time
from typing import Optional

import flet as ft

from core.aggregator import Aggregator, TrackResult
from core.soulseek import Candidate, DownloadJob, dedupe_by_file, parse_queries

SOULSEEK_FORMATS = ("flac", "wav", "mp3")

RENDER_INTERVAL = 0.8

# ============================== DESIGN TOKENS ==============================

PAGE_BG = "#F3F2FB"          # lavanda bem suave, menos "cinza de sistema"
INK = "#1E1B33"              # texto principal (quase preto, com tom roxo)
MUTED = "#6B6684"            # texto secundário

PRIMARY = "#4F46E5"          # indigo-600
PRIMARY_DARK = "#3730A3"     # indigo-800
PRIMARY_SOFT = "#EEF0FE"     # tint claro do primary, pra fundos/pills

BEATPORT_COLOR = "#E11D74"   # coral/magenta — remete a dance music sem copiar marca
BEATPORT_SOFT = "#FDEBF3"
BANDCAMP_COLOR = "#0F91A8"   # teal
BANDCAMP_SOFT = "#E6F6F9"
FREE_COLOR = "#16A34A"       # verde "grátis"
FREE_SOFT = "#E9F9EF"
SOULSEEK_NEUTRAL = "#475569" # cor neutra do painel Soulseek (o destaque é o verde do preço)

RADIUS_LG = 18
RADIUS_MD = 14
RADIUS_SM = 10

HEADER_GRADIENT = ft.LinearGradient(
    begin=ft.alignment.top_left, end=ft.alignment.bottom_right,
    colors=[PRIMARY_DARK, PRIMARY, "#7C3AED"],
)


def _plural(n: int, singular: str, plural: str = None) -> str:
    plural = plural or f"{singular}s"
    return f"{n} {singular}" if n == 1 else f"{n} {plural}"


def soft_shadow(blur=18, dy=6, opacity=0.07) -> ft.BoxShadow:
    return ft.BoxShadow(blur_radius=blur, spread_radius=0,
                        color=ft.colors.with_opacity(opacity, "#2E1065"),
                        offset=ft.Offset(0, dy))


def card(*controls, expand=False, padding=20) -> ft.Container:
    return ft.Container(
        content=ft.Column(list(controls), spacing=10, tight=True),
        bgcolor=ft.colors.WHITE, border_radius=RADIUS_LG, padding=padding,
        border=ft.border.all(1, "#EBE9F7"),
        shadow=soft_shadow(),
        expand=expand,
    )


def section_title(icon, title, subtitle=None, color=None) -> ft.Column:
    rows = [ft.Row([
        ft.Container(
            content=ft.Icon(icon, color=color or PRIMARY, size=17),
            width=32, height=32, border_radius=10, alignment=ft.alignment.center,
            bgcolor=ft.colors.with_opacity(0.10, color or PRIMARY),
        ),
        ft.Text(title, size=16, weight=ft.FontWeight.BOLD, color=INK),
    ], spacing=10)]
    if subtitle:
        rows.append(ft.Container(
            content=ft.Text(subtitle, size=12, color=MUTED), padding=ft.padding.only(left=42),
        ))
    return ft.Column(rows, spacing=6, tight=True)


def pill(text: str, color: str, soft: str, size=13, icon=None) -> ft.Container:
    """Badge arredondado (preço, tag de formato, status) — a peça central do design."""
    content = [ft.Text(text, size=size, weight=ft.FontWeight.BOLD, color=color)]
    if icon:
        content.insert(0, ft.Icon(icon, size=size + 2, color=color))
    return ft.Container(
        content=ft.Row(content, spacing=4, tight=True),
        bgcolor=soft, padding=ft.padding.symmetric(horizontal=10, vertical=5),
        border_radius=20,
    )


def pstyle(bg, fg=None) -> ft.ButtonStyle:
    return ft.ButtonStyle(
        bgcolor=bg, color=fg or ft.colors.WHITE,
        shape=ft.RoundedRectangleBorder(radius=RADIUS_SM),
        padding=ft.padding.symmetric(horizontal=20, vertical=12),
        elevation=0,
    )


def outline_style(color) -> ft.ButtonStyle:
    return ft.ButtonStyle(
        color=color, side=ft.BorderSide(1.4, color),
        shape=ft.RoundedRectangleBorder(radius=RADIUS_SM),
        padding=ft.padding.symmetric(horizontal=14, vertical=10),
    )


def platform_badge(icon, color) -> ft.Container:
    return ft.Container(
        content=ft.Icon(icon, size=15, color=ft.colors.WHITE),
        width=26, height=26, border_radius=8, bgcolor=color, alignment=ft.alignment.center,
    )


class AggregatorView:
    def __init__(self, page: ft.Page):
        self.page = page
        self.page.bgcolor = PAGE_BG
        self.aggregator = Aggregator(log_callback=self.log)
        self.engine = self.aggregator.soulseek
        self.config = self.engine.config

        self.result_groups: dict[str, TrackResult] = {}
        self.searching = {"value": False}
        self._last_render = 0.0
        self._render_lock = threading.Lock()

        self._build_controls()
        self.control = self._build_layout()
        self._render_results()
        self._render_jobs()

    # ================= infraestrutura =================

    def log(self, msg: str) -> None:
        self.log_area.controls.append(
            ft.Text(f"›  {msg}", size=12, color="#3A3560", selectable=True,
                    font_family="Consolas")
        )
        if len(self.log_area.controls) > 300:
            del self.log_area.controls[:80]
        self._safe_update()

    def _safe_update(self) -> None:
        try:
            self.page.update()
        except Exception:
            pass

    def _snack(self, msg: str, color=None) -> None:
        self.page.snack_bar = ft.SnackBar(ft.Text(msg), bgcolor=color)
        self.page.snack_bar.open = True
        self._safe_update()

    def shutdown(self) -> None:
        try:
            self.aggregator.shutdown()
        except Exception:
            pass

    # ================= controles =================

    def _build_controls(self) -> None:
        c = self.config
        field_style = dict(
            border_radius=RADIUS_SM, filled=True, bgcolor="#F7F7FC",
            border_color="transparent", focused_border_color=PRIMARY,
        )
        self.user_field = ft.TextField(label="Usuário do Soulseek", value=c.username,
                                       height=50, expand=True, **field_style)
        self.pass_field = ft.TextField(label="Senha", value=c.password, height=50,
                                       expand=True, password=True, can_reveal_password=True,
                                       **field_style)
        self.folder_text = ft.Text(c.download_dir, size=12, color=MUTED,
                                   no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS, expand=True)
        self.share_check = ft.Checkbox(label="Compartilhar a pasta de downloads (recomendado)",
                                       value=c.share_download_dir, active_color=PRIMARY)

        self.status_dot = ft.Container(width=8, height=8, border_radius=4, bgcolor=MUTED)
        self.status_text = ft.Text("Desconectado", size=12, weight=ft.FontWeight.BOLD, color=MUTED)
        self.status_pill = ft.Container(
            content=ft.Row([self.status_dot, self.status_text], spacing=8,
                           tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            bgcolor="#F1F0F9", padding=ft.padding.symmetric(horizontal=12, vertical=8),
            border_radius=20,
        )
        self.connect_btn = ft.ElevatedButton("Conectar", icon=ft.icons.POWER_SETTINGS_NEW,
                                             style=pstyle(SOULSEEK_NEUTRAL),
                                             on_click=self._toggle_connection)
        self.folder_picker = ft.FilePicker(on_result=self._on_folder_picked)
        self.page.overlay.append(self.folder_picker)

        self.query_field = ft.TextField(
            label="Faixa (ou várias, separadas por vírgula)",
            hint_text="New Order - Blue Monday, Daft Punk - Around The World",
            height=56, expand=True, **field_style,
        )
        self.window_dd = ft.Dropdown(
            label="Coleta Soulseek", width=150, height=56, value="10",
            border_radius=RADIUS_SM, filled=True, bgcolor="#F7F7FC", border_color="transparent",
            options=[ft.dropdown.Option(v, f"{v}s") for v in ("6", "10", "20")],
        )
        self.search_btn = ft.ElevatedButton("Buscar", icon=ft.icons.SEARCH, height=56,
                                            style=pstyle(PRIMARY),
                                            on_click=lambda e: self._start_search())
        self.search_progress = ft.ProgressBar(value=0, color=PRIMARY,
                                              bgcolor=PRIMARY_SOFT, visible=False,
                                              border_radius=4)

        self.results_count = ft.Text("", size=12, color=MUTED)
        self.results_list = ft.ListView(expand=True, spacing=12, auto_scroll=False)

        self.jobs_list = ft.ListView(expand=True, spacing=6, auto_scroll=False)
        self.jobs_count = ft.Text("Nenhum download na fila", size=12, color=MUTED)

        self.log_area = ft.ListView(expand=True, spacing=3, auto_scroll=True, height=120)

    def _build_layout(self):
        conn_card = card(
            section_title(ft.icons.CLOUD_SYNC, "Conta Soulseek",
                          "Não existe cadastro no site — a conta nasce no primeiro login. "
                          "Escolha nome e senha; se já existir, a senha precisa bater.",
                          color=SOULSEEK_NEUTRAL),
            ft.Container(
                content=ft.Column([
                    ft.Row([self.user_field, self.pass_field], spacing=10),
                    ft.Row([
                        ft.OutlinedButton("Pasta de downloads", icon=ft.icons.FOLDER_OPEN,
                                         style=outline_style(SOULSEEK_NEUTRAL),
                                         on_click=lambda e: self.folder_picker.get_directory_path(
                                             dialog_title="Onde salvar as músicas do Soulseek")),
                        self.folder_text,
                    ], spacing=10),
                    self.share_check,
                ], spacing=10),
                bgcolor="#FAFAFD", border_radius=RADIUS_MD, padding=14,
            ),
            ft.Row([self.connect_btn, ft.Container(expand=True), self.status_pill]),
        )

        search_card = card(
            section_title(ft.icons.SEARCH, "Buscar faixa",
                          "Mostra onde comprar (Beatport, Bandcamp) e a melhor opção grátis "
                          "no Soulseek, lado a lado.", color=PRIMARY),
            ft.Row([self.query_field, self.window_dd, self.search_btn], spacing=10),
            self.search_progress,
            ft.Row([
                pill("Beatport", BEATPORT_COLOR, BEATPORT_SOFT, size=11, icon=ft.icons.SELL_OUTLINED),
                pill("Bandcamp", BANDCAMP_COLOR, BANDCAMP_SOFT, size=11, icon=ft.icons.SELL_OUTLINED),
                pill("Soulseek grátis", FREE_COLOR, FREE_SOFT, size=11, icon=ft.icons.DOWNLOAD_DONE),
            ], spacing=8),
        )

        results_card = card(
            ft.Row([section_title(ft.icons.ALBUM, "Resultados"),
                   ft.Container(expand=True), self.results_count]),
            ft.Divider(height=1, color="#EEEDF8"),
            ft.Container(content=self.results_list, height=440),
        )

        jobs_card = card(
            section_title(ft.icons.DOWNLOADING, "Downloads (Soulseek)", color=FREE_COLOR),
            ft.Row([self.jobs_count, ft.Container(expand=True),
                   ft.TextButton("Limpar concluídos", icon=ft.icons.CLEANING_SERVICES,
                                style=ft.ButtonStyle(color=MUTED),
                                on_click=lambda e: self._clear_finished())]),
            ft.Container(content=self.jobs_list, height=180),
        )

        log_card = card(
            section_title(ft.icons.TERMINAL, "Log"),
            ft.Container(content=self.log_area, height=120, bgcolor="#FAFAFD",
                        border_radius=RADIUS_MD, padding=10),
        )

        return ft.Column(
            [conn_card, search_card, results_card, jobs_card, log_card],
            scroll=ft.ScrollMode.AUTO, expand=True, spacing=16,
        )

    # ================= conexão =================

    def _on_folder_picked(self, e: ft.FilePickerResultEvent) -> None:
        if not e.path:
            return
        self.config.download_dir = e.path
        self.folder_text.value = e.path
        self.config.save()
        self._safe_update()

    def _save_from_fields(self) -> None:
        c = self.config
        c.username = (self.user_field.value or "").strip()
        c.password = self.pass_field.value or ""
        c.share_download_dir = bool(self.share_check.value)
        c.save()

    def _set_status(self, color: str, text: str) -> None:
        self.status_dot.bgcolor = color
        self.status_text.value = text
        self.status_text.color = color

    def _toggle_connection(self, e=None) -> None:
        if self.engine.is_connected:
            self.engine.disconnect()
            self._set_status(MUTED, "Desconectado")
            self.connect_btn.text = "Conectar"
            self._safe_update()
            return

        self._save_from_fields()
        if not self.config.username or not self.config.password:
            self._snack("Preencha usuário e senha do Soulseek.", ft.colors.RED_600)
            return

        self.connect_btn.disabled = True
        self._set_status("#D97706", "Conectando...")
        self._safe_update()

        def worker():
            ok, msg = self.engine.connect()
            self.connect_btn.disabled = False
            if ok:
                self._set_status(FREE_COLOR, f"Conectado como {self.config.username}")
                self.connect_btn.text = "Desconectar"
            else:
                self._set_status("#DC2626", "Falha ao conectar")
                self._snack(msg, ft.colors.RED_600)
            self._safe_update()

        threading.Thread(target=worker, daemon=True).start()

    # ================= busca =================

    def _start_search(self) -> None:
        queries = parse_queries(self.query_field.value or "")
        if not queries:
            self._snack("Digite ao menos uma faixa.", ft.colors.ORANGE_700)
            return
        if self.searching["value"]:
            return

        self._save_from_fields()
        self.searching["value"] = True
        self.search_btn.disabled = True
        self.search_progress.visible = True
        self.search_progress.value = None
        self.result_groups = {q: TrackResult(query=q) for q in queries}
        self._safe_update()

        seconds = float(self.window_dd.value or 10)

        def on_update(query, result):
            self.result_groups[query] = result
            now = time.monotonic()
            if result.done or now - self._last_render >= RENDER_INTERVAL:
                self._last_render = now
                self._render_results()

        def worker():
            try:
                self.result_groups = self.aggregator.search_many(
                    queries, soulseek_seconds=seconds, on_update=on_update,
                )
            except Exception as ex:
                self.log(f"Erro na busca: {ex}")
            finally:
                self.searching["value"] = False
                self.search_btn.disabled = False
                self.search_progress.visible = False
                self.search_progress.value = 0
                self._render_results()

        threading.Thread(target=worker, daemon=True).start()

    # ================= resultados =================

    def _render_results(self) -> None:
        with self._render_lock:
            self.results_list.controls.clear()
            groups = list(self.result_groups.items())

            if not groups:
                self.results_list.controls.append(self._empty_state())
                self.results_count.value = ""
                self._safe_update()
                return

            for query, result in groups:
                self.results_list.controls.append(self._track_card(query, result))

            prontos = sum(1 for _, r in groups if r.done)
            self.results_count.value = f"{prontos}/{len(groups)} pronto(s)"
            self._safe_update()

    def _empty_state(self) -> ft.Container:
        return ft.Container(
            content=ft.Column([
                ft.Container(
                    content=ft.Icon(ft.icons.GRAPHIC_EQ_ROUNDED, size=34, color=PRIMARY),
                    width=72, height=72, border_radius=36, bgcolor=PRIMARY_SOFT,
                    alignment=ft.alignment.center,
                ),
                ft.Text("Busque uma faixa pra começar", size=15, weight=ft.FontWeight.BOLD,
                       color=INK),
                ft.Text("Cole o nome de uma música ou uma lista separada por vírgula.",
                       size=12, color=MUTED),
            ], spacing=10, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            padding=40, alignment=ft.alignment.center,
        )

    def _best_cover(self, result: TrackResult) -> str:
        if result.beatport_best and result.beatport_best.cover_url:
            return result.beatport_best.cover_url
        if result.bandcamp_best and result.bandcamp_best.cover_url:
            return result.bandcamp_best.cover_url
        return ""

    def _cover_thumb(self, url: str) -> ft.Container:
        if url:
            content = ft.Image(
                src=url, width=64, height=64, fit=ft.ImageFit.COVER, border_radius=12,
                error_content=ft.Icon(ft.icons.MUSIC_NOTE_ROUNDED, color=ft.colors.WHITE, size=24),
            )
        else:
            content = ft.Icon(ft.icons.MUSIC_NOTE_ROUNDED, color=ft.colors.WHITE, size=24)
        return ft.Container(
            content=content, width=64, height=64, border_radius=12,
            gradient=ft.LinearGradient(
                begin=ft.alignment.top_left, end=ft.alignment.bottom_right,
                colors=[PRIMARY, "#7C3AED"],
            ),
            alignment=ft.alignment.center, clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        )

    def _track_card(self, query: str, result: TrackResult) -> ft.Container:
        spinner = ft.ProgressRing(width=15, height=15, stroke_width=2, color=PRIMARY)
        fontes = (len(result.beatport) + len(result.bandcamp) + len(result.soulseek))
        subtitle = (f"{_plural(fontes, 'fonte')} encontrada(s)" if result.done
                   else "buscando nas três fontes...")

        return ft.Container(
            content=ft.Column([
                ft.Row([
                    self._cover_thumb(self._best_cover(result)),
                    ft.Column([
                        ft.Text(query, size=15, weight=ft.FontWeight.BOLD, color=INK,
                               no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Row([
                            *([spinner] if not result.done else []),
                            ft.Text(subtitle, size=11, color=MUTED),
                        ], spacing=6),
                    ], spacing=4, tight=True, expand=True,
                       alignment=ft.MainAxisAlignment.CENTER),
                ], spacing=12),
                ft.Row([
                    self._beatport_panel(result),
                    self._bandcamp_panel(result),
                    self._soulseek_panel(query, result),
                ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.START),
            ], spacing=12, tight=True),
            padding=16, bgcolor="#FBFBFE", border_radius=RADIUS_MD,
            border=ft.border.all(1, "#EEEDF8"),
        )

    # ---------- painéis de plataforma ----------

    def _panel_shell(self, icon, title, color, soft, body) -> ft.Container:
        container = ft.Container(
            content=ft.Column([
                ft.Row([platform_badge(icon, color),
                       ft.Text(title, size=12, weight=ft.FontWeight.BOLD, color=INK)], spacing=8),
                body,
            ], spacing=10, tight=True),
            expand=True, padding=14, bgcolor=ft.colors.WHITE, border_radius=RADIUS_MD,
            border=ft.border.all(1, "#EEEDF8"),
            shadow=ft.BoxShadow(blur_radius=0, color=ft.colors.TRANSPARENT),
            animate=ft.Animation(160, ft.AnimationCurve.EASE_OUT),
            scale=1.0, animate_scale=ft.Animation(160, ft.AnimationCurve.EASE_OUT),
        )

        def on_hover(e: ft.HoverEvent):
            hovering = e.data == "true"
            container.scale = 1.015 if hovering else 1.0
            container.shadow = soft_shadow(blur=20, dy=8, opacity=0.10) if hovering else \
                ft.BoxShadow(blur_radius=0, color=ft.colors.TRANSPARENT)
            container.border = ft.border.all(1, color if hovering else "#EEEDF8")
            container.update()

        container.on_hover = on_hover
        return container

    def _empty_body(self, text: str) -> ft.Text:
        return ft.Text(text, size=11, color=MUTED, italic=True)

    def _beatport_panel(self, result: TrackResult) -> ft.Container:
        t = result.beatport_best
        if not t:
            body = self._empty_body("não encontrado" if result.done else "buscando...")
        else:
            body = ft.Column([
                ft.Text(t.full_title, size=12, weight=ft.FontWeight.W_600, color=INK,
                       no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(t.artist_label, size=11, color=MUTED,
                       no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Row([
                    pill(t.price_display or "?", BEATPORT_COLOR, BEATPORT_SOFT),
                    ft.Text(f"{t.bpm}bpm  {t.key}".strip(), size=10, color=MUTED),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Text(t.label, size=10, color=MUTED,
                       no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
                ft.OutlinedButton("Ver no Beatport", icon=ft.icons.OPEN_IN_NEW,
                                 style=outline_style(BEATPORT_COLOR),
                                 url=t.url, url_target=ft.UrlTarget.BLANK),
            ], spacing=4, tight=True)
        return self._panel_shell(ft.icons.SELL_ROUNDED, "Beatport", BEATPORT_COLOR,
                                 BEATPORT_SOFT, body)

    def _bandcamp_panel(self, result: TrackResult) -> ft.Container:
        t = result.bandcamp_best
        if not t:
            body = self._empty_body("não encontrado" if result.done else "buscando...")
        else:
            body = ft.Column([
                ft.Text(t.title, size=12, weight=ft.FontWeight.W_600, color=INK,
                       no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(t.artist, size=11, color=MUTED,
                       no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Row([
                    pill(t.price_label, BANDCAMP_COLOR, BANDCAMP_SOFT),
                    ft.Text(t.item_type, size=10, color=MUTED),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.OutlinedButton("Ver no Bandcamp", icon=ft.icons.OPEN_IN_NEW,
                                 style=outline_style(BANDCAMP_COLOR),
                                 url=t.url, url_target=ft.UrlTarget.BLANK),
            ], spacing=4, tight=True)
        return self._panel_shell(ft.icons.SELL_ROUNDED, "Bandcamp", BANDCAMP_COLOR,
                                 BANDCAMP_SOFT, body)

    def _soulseek_panel(self, query: str, result: TrackResult) -> ft.Container:
        by_format = result.soulseek_best_by_format
        if not any(by_format.values()):
            hint = "conecte-se ao Soulseek" if not self.engine.is_connected else (
                "não encontrado" if result.done else "buscando...")
            body = self._empty_body(hint)
        else:
            fontes = len(dedupe_by_file(result.soulseek))
            body = ft.Column([
                ft.Row([
                    pill("Grátis", FREE_COLOR, FREE_SOFT, icon=ft.icons.CHECK_CIRCLE_ROUNDED),
                    ft.Text(_plural(fontes, "fonte"), size=10, color=MUTED),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                *[self._format_row(query, result, fmt, by_format[fmt]) for fmt in SOULSEEK_FORMATS],
            ], spacing=6, tight=True)
        return self._panel_shell(ft.icons.PEOPLE_ALT_ROUNDED, "Soulseek", SOULSEEK_NEUTRAL,
                                 "#F1F5F9", body)

    def _format_row(self, query: str, result: TrackResult, fmt: str,
                    candidate: Optional[Candidate]) -> ft.Row:
        tag = ft.Container(
            content=ft.Text(fmt.upper(), size=9.5, weight=ft.FontWeight.BOLD, color=FREE_COLOR),
            bgcolor=FREE_SOFT, padding=ft.padding.symmetric(horizontal=6, vertical=3),
            border_radius=6, width=42, alignment=ft.alignment.center,
        )
        if not candidate:
            return ft.Row([
                tag,
                ft.Text("indisponível", size=10, color=MUTED, italic=True, expand=True),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        detail = f"{candidate.quality_label} · {candidate.size_mb:.1f} MB"
        return ft.Row([
            tag,
            ft.Text(detail, size=10, color=MUTED, expand=True,
                   no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
            ft.IconButton(ft.icons.DOWNLOAD_ROUNDED, icon_size=17, icon_color=FREE_COLOR,
                         tooltip=f"Baixar em {fmt.upper()} — de {candidate.username}",
                         on_click=lambda e, q=query, r=result, c=candidate: self._download_candidate(q, r, c)),
        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def _download_candidate(self, query: str, result: TrackResult, candidate: Candidate) -> None:
        if not self.engine.is_connected:
            self._snack("Conecte-se ao Soulseek primeiro.", ft.colors.ORANGE_700)
            return
        # Fallback só entre fontes do mesmo formato: quem escolheu FLAC não quer
        # que a fila caia num MP3 silenciosamente se a fonte escolhida falhar.
        same_format_pool = [c for c in result.soulseek if c.ext == candidate.ext]
        label = f"{query} · {candidate.ext.upper()}"
        self.engine.enqueue(candidate, pool=same_format_pool, label=label,
                            on_update=self._on_job_update)
        self.log(f"Na fila: {candidate.filename} ({candidate.quality_label}) de {candidate.username}")
        self._render_jobs()

    # ================= fila de downloads =================

    def _on_job_update(self, job: DownloadJob) -> None:
        now = time.monotonic()
        if job.is_done or now - self._last_render >= RENDER_INTERVAL:
            self._last_render = now
            self._render_jobs()

    def _clear_finished(self) -> None:
        self.engine.clear_finished_jobs()
        self._render_jobs()

    def _render_jobs(self) -> None:
        jobs = self.engine.jobs()
        self.jobs_list.controls.clear()
        if not jobs:
            self.jobs_count.value = "Nenhum download na fila"
            self.jobs_list.controls.append(
                ft.Container(content=ft.Text("A fila aparece aqui.", size=12, color=MUTED),
                            padding=16, alignment=ft.alignment.center)
            )
            self._safe_update()
            return

        done = sum(1 for j in jobs if j.state == "complete")
        failed = sum(1 for j in jobs if j.state == "failed")
        self.jobs_count.value = (f"{_plural(len(jobs), 'item')} · {done} concluído(s) · "
                                 f"{failed} falhou(ram)")
        for job in jobs:
            self.jobs_list.controls.append(self._job_row(job))
        self._safe_update()

    def _job_row(self, job: DownloadJob) -> ft.Container:
        palette = {
            "complete": (FREE_COLOR, ft.icons.CHECK_CIRCLE),
            "failed": ("#DC2626", ft.icons.ERROR_OUTLINE),
            "cancelled": (MUTED, ft.icons.CANCEL),
            "downloading": (PRIMARY, ft.icons.DOWNLOADING),
            "queued": ("#D97706", ft.icons.HOURGLASS_TOP),
        }
        color, icon = palette.get(job.state, (MUTED, ft.icons.SCHEDULE))
        detail = job.detail or job.error or ""
        if job.state == "downloading" and job.size:
            detail += f"  ·  {job.bytes_done / (1024 * 1024):.1f}/{job.size / (1024 * 1024):.1f} MB"
        right = []
        if job.state == "downloading":
            right.append(ft.Text(job.speed_label, size=11, color=MUTED))
        if not job.is_done:
            right.append(ft.IconButton(ft.icons.CLOSE, icon_size=16, icon_color=MUTED,
                                       tooltip="Cancelar",
                                       on_click=lambda e, j=job: (self.engine.cancel(j),
                                                                  self._render_jobs())))
        return ft.Container(
            content=ft.Column([
                ft.Row([ft.Icon(icon, size=16, color=color),
                       ft.Text(job.label, size=12, weight=ft.FontWeight.W_500, expand=True,
                              color=INK, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
                       *right], spacing=6),
                ft.ProgressBar(value=job.progress if job.state != "queued" else None,
                             color=color, bgcolor="#F1F0F9", height=4, border_radius=2)
                if not job.is_done or job.state == "complete" else ft.Container(height=4),
                ft.Text(detail, size=11, color=MUTED,
                       no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
            ], spacing=5, tight=True),
            padding=ft.padding.symmetric(horizontal=12, vertical=10),
            bgcolor=ft.colors.WHITE, border_radius=RADIUS_SM,
            border=ft.border.only(left=ft.BorderSide(3, color)),
        )


def build_aggregator_view(page: ft.Page) -> AggregatorView:
    return AggregatorView(page)
