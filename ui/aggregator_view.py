"""Tela principal: busca uma faixa e mostra Beatport, Bandcamp e Soulseek lado a lado.

Cada faixa buscada vira um card com capa de álbum (a melhor disponível entre
Beatport/Bandcamp) e três painéis coloridos por plataforma — preço em destaque
como "pill", link pra comprar ou botão pra baixar grátis. Os painéis reagem ao
hover (leve elevação + zoom) pra dar sensação de interatividade num app que,
por natureza, é basicamente uma lista de resultados.
"""

import json
import threading
import time
from typing import Callable, Optional

import flet as ft

from core import bandcamp, beatport, youtube
from core.producer import search_producer
from core.aggregator import Aggregator, TrackResult
from core.ai_providers import PROVIDERS, AISettings
from core.soulseek import Candidate, DownloadJob, dedupe_by_file, _data_dir
from core.tracklist_parser import parse_tracklist_to_queries
from core.youtube import YouTubeConfig

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
        self.ai_settings = AISettings.load()
        self.youtube_config = YouTubeConfig.load()
        self._playing_key: Optional[str] = None
        self._paused_key: Optional[str] = None

        self.result_groups: dict[str, TrackResult] = {}
        self.searching = {"value": False}
        self._last_render = 0.0
        self._render_lock = threading.Lock()

        self._build_controls()
        self._main_layout = self._build_layout()
        self._login_screen = self._build_login_screen()
        # Sempre entra pela tela de login — mesmo com credenciais salvas, a
        # pessoa dá o clique em "Conectar" (mas os campos já vêm preenchidos).
        self.control = ft.Container(content=self._login_screen, expand=True,
                                    bgcolor=PAGE_BG)
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
        self._stop_preview()
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
            border_radius=20, tooltip="Gerenciar conta",
            on_click=lambda e: self._show_login_screen(),
        )
        # Botão único: conecta na tela de entrada, e também serve pra
        # desconectar quando a pessoa volta nela pelo status pill.
        self.login_btn = ft.ElevatedButton(
            "Conectar", icon=ft.icons.ARROW_FORWARD_ROUNDED, height=50,
            style=pstyle(PRIMARY), on_click=lambda e: self._do_login(),
        )
        self.login_error_text = ft.Text("", size=12, color="#DC2626", visible=False)
        self.folder_picker = ft.FilePicker(on_result=self._on_folder_picked)
        self.page.overlay.append(self.folder_picker)

        # Player único, reaproveitado por todas as prévias (Beatport/Bandcamp):
        # tocar uma nova troca o `src` da mesma instância, então só uma prévia
        # toca por vez — sem precisar de um Audio por resultado. Criado só na
        # primeira prévia (não agora): o controle Audio do Flet exige um `src`
        # não-vazio já no primeiro `page.update()`, e ainda não temos URL nenhuma.
        self.audio_player: Optional[ft.Audio] = None
        self._audio_lock = threading.RLock()
        self._preview_generation = 0
        self._audio_duration = 0
        self._audio_position = 0
        self._audio_volume = 0.20
        try:
            saved = json.loads((_data_dir() / "player.json").read_text(encoding="utf-8"))
            value = float(saved.get("volume", 0.20))
            if 0 <= value <= 1:
                self._audio_volume = value
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        self._unmuted_volume = self._audio_volume or 0.20
        self.volume_label = ft.Text(f"{round(self._audio_volume * 100)}%", size=11, color=ft.colors.WHITE)
        self.volume_slider = ft.Slider(
            min=0, max=100, value=self._audio_volume * 100, divisions=100,
            label="{value}%", width=130, active_color=ft.colors.WHITE,
            on_change=self._change_volume, on_change_end=lambda e: self._save_volume(),
        )
        self.mute_btn = ft.IconButton(
            ft.icons.VOLUME_OFF if self._audio_volume == 0 else ft.icons.VOLUME_UP,
            icon_color=ft.colors.WHITE, tooltip="Ativar som" if self._audio_volume == 0 else "Silenciar",
            on_click=lambda e: self._toggle_mute(),
        )

        # Barra "tocando agora", fixa embaixo da área rolável (não some ao
        # rolar a lista de resultados) — mostra o que está tocando e dá
        # controle de pausar/retomar/parar sem precisar achar o card de novo.
        self.now_playing_icon = ft.Icon(ft.icons.GRAPHIC_EQ_ROUNDED, color=ft.colors.WHITE, size=18)
        self.now_playing_title = ft.Text("", size=13, weight=ft.FontWeight.BOLD,
                                         color=ft.colors.WHITE, no_wrap=True,
                                         overflow=ft.TextOverflow.ELLIPSIS)
        self.now_playing_subtitle = ft.Text("", size=11,
                                            color=ft.colors.with_opacity(0.75, ft.colors.WHITE),
                                            no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS)
        self.now_playing_time = ft.Text("", size=11,
                                        color=ft.colors.with_opacity(0.75, ft.colors.WHITE))
        self.now_playing_toggle_btn = ft.IconButton(
            ft.icons.PAUSE_CIRCLE_ROUNDED, icon_color=ft.colors.WHITE, icon_size=28,
            tooltip="Pausar", on_click=lambda e: self._now_playing_toggle_click(),
        )
        self.now_playing_stop_btn = ft.IconButton(
            ft.icons.STOP_CIRCLE_ROUNDED, icon_color=ft.colors.WHITE, icon_size=28,
            tooltip="Parar", on_click=lambda e: self._stop_preview(),
        )
        self.now_playing_bar = ft.Container(
            visible=False, bgcolor=PRIMARY_DARK,
            padding=ft.padding.symmetric(horizontal=20, vertical=10),
            content=ft.Row([
                self.now_playing_icon,
                ft.Column([self.now_playing_title, self.now_playing_subtitle],
                         spacing=0, tight=True, expand=True),
                self.now_playing_time,
                self.mute_btn,
                self.volume_slider,
                self.volume_label,
                self.now_playing_toggle_btn,
                self.now_playing_stop_btn,
            ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        )
        self._now_playing_call: Optional[Callable[[], None]] = None
        self._now_playing_meta: tuple[str, str] = ("", "")


        self.query_field = ft.TextField(
            label="Faixa, lista separada por vírgula, ou um tracklist colado inteiro",
            hint_text="New Order - Blue Monday, Daft Punk - Around The World  ·  ou cole um "
                     "tracklist com horário e gravadora que a gente limpa sozinho",
            multiline=True, min_lines=1, max_lines=8, expand=True, **field_style,
        )
        self.search_mode = ft.Dropdown(
            label="Tipo de busca", value="tracks", width=180,
            options=[ft.dropdown.Option("tracks", "Faixas / tracklist"),
                     ft.dropdown.Option("producer", "Produtor")],
            on_change=lambda e: self._change_search_mode(),
        )
        self.producer_notice = ft.Text("", size=12, color=MUTED, visible=False)
        self.window_dd = ft.Dropdown(
            label="Coleta Soulseek", width=150, height=56, value="10",
            border_radius=RADIUS_SM, filled=True, bgcolor="#F7F7FC", border_color="transparent",
            options=[ft.dropdown.Option(v, f"{v}s") for v in ("6", "10", "20")],
        )
        self.search_btn = ft.ElevatedButton("Buscar", icon=ft.icons.SEARCH, height=56,
                                            style=pstyle(PRIMARY),
                                            on_click=lambda e: self._start_search())
        self.ai_settings_btn = ft.IconButton(
            ft.icons.SMART_TOY_OUTLINED, icon_color=MUTED, height=56,
            tooltip="Configurar IA para linhas de tracklist que o regex não entender",
            on_click=lambda e: self._open_ai_settings(),
        )
        self._build_ai_settings_dialog()
        self.search_progress = ft.ProgressBar(value=0, color=PRIMARY,
                                              bgcolor=PRIMARY_SOFT, visible=False,
                                              border_radius=4)

        self.result_filter = ft.TextField(
            label="Filtrar faixas da lista", prefix_icon=ft.icons.FILTER_LIST,
            on_change=lambda e: self._render_results(), expand=True,
            border_radius=RADIUS_SM, filled=True, bgcolor="#F7F7FC",
        )
        self.source_filter = ft.Dropdown(
            label="Fonte disponível", value="all", width=190,
            options=[ft.dropdown.Option("all", "Todas as fontes"),
                     ft.dropdown.Option("beatport", "Beatport"),
                     ft.dropdown.Option("bandcamp", "Bandcamp"),
                     ft.dropdown.Option("soulseek", "Soulseek")],
            on_change=lambda e: self._render_results(),
        )
        self.results_count = ft.Text("", size=12, color=MUTED)
        self.results_list = ft.ListView(expand=True, spacing=12, auto_scroll=False)

        self.jobs_list = ft.ListView(expand=True, spacing=6, auto_scroll=False)
        self.jobs_count = ft.Text("Nenhum download na fila", size=12, color=MUTED)

        self.log_area = ft.ListView(expand=True, spacing=3, auto_scroll=True, height=120)

    def _build_ai_settings_dialog(self) -> None:
        field_style = dict(
            border_radius=RADIUS_SM, filled=True, bgcolor="#F7F7FC",
            border_color="transparent", focused_border_color=PRIMARY, height=50,
        )
        self.ai_provider_dd = ft.Dropdown(
            label="Provedor padrão", value=self.ai_settings.active_provider,
            border_radius=RADIUS_SM, filled=True, bgcolor="#F7F7FC", border_color="transparent",
            options=[ft.dropdown.Option(p.id, p.label) for p in PROVIDERS.values()],
        )
        self.ai_key_fields: dict[str, ft.TextField] = {}
        key_rows = []
        for provider in PROVIDERS.values():
            key_field = ft.TextField(
                label=f"Chave da API — {provider.label}",
                value=self.ai_settings.keys.get(provider.id, ""),
                password=True, can_reveal_password=True, **field_style,
            )
            self.ai_key_fields[provider.id] = key_field
            key_rows.append(ft.Column([
                key_field,
                ft.Text(provider.signup_hint, size=11, color=MUTED),
            ], spacing=4, tight=True))

        self.youtube_key_field = ft.TextField(
            label="Chave da API — YouTube Data v3",
            value=self.youtube_config.api_key,
            password=True, can_reveal_password=True, **field_style,
        )

        self.ai_dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Chaves de API — IA e YouTube", weight=ft.FontWeight.BOLD,
                          color=INK),
            content=ft.Container(
                width=420, height=480,
                content=ft.Column([
                    ft.Text("O regex já resolve a maioria dos tracklists colados — a IA só "
                           "entra em ação nas poucas linhas que ele não conseguir separar em "
                           "artista/faixa. O YouTube é usado como opção de \"ouvir\" quando a "
                           "faixa não aparece no Beatport nem no Bandcamp. Nada aqui é "
                           "obrigatório.", size=12, color=MUTED),
                    self.ai_provider_dd,
                    *key_rows,
                    ft.Divider(height=1, color="#EEEDF8"),
                    ft.Column([
                        self.youtube_key_field,
                        ft.Text("Chave grátis em console.cloud.google.com "
                               "(ative \"YouTube Data API v3\").", size=11, color=MUTED),
                    ], spacing=4, tight=True),
                ], spacing=14, tight=True, scroll=ft.ScrollMode.AUTO),
            ),
            actions=[
                ft.TextButton("Cancelar", on_click=lambda e: self._close_ai_settings()),
                ft.ElevatedButton("Salvar", style=pstyle(PRIMARY),
                                  on_click=lambda e: self._save_ai_settings()),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.overlay.append(self.ai_dialog)

    def _open_ai_settings(self) -> None:
        self.page.dialog = self.ai_dialog
        self.ai_dialog.open = True
        self._safe_update()

    def _close_ai_settings(self) -> None:
        self.ai_dialog.open = False
        self._safe_update()

    def _save_ai_settings(self) -> None:
        self.ai_settings.active_provider = self.ai_provider_dd.value or "glm"
        for provider_id, key_field in self.ai_key_fields.items():
            self.ai_settings.keys[provider_id] = (key_field.value or "").strip()
        self.ai_settings.save()
        self.youtube_config.api_key = (self.youtube_key_field.value or "").strip()
        self.youtube_config.save()
        self._close_ai_settings()
        self._snack("Chaves salvas.", FREE_COLOR)

    # ================= prévia de áudio =================

    def _save_volume(self) -> None:
        try:
            (_data_dir() / "player.json").write_text(
                json.dumps({"volume": self._audio_volume}), encoding="utf-8")
        except OSError as ex:
            self.log(f"Não foi possível salvar o volume: {ex}")

    def _apply_volume(self, value: float) -> None:
        with self._audio_lock:
            self._audio_volume = max(0.0, min(1.0, value))
            if self._audio_volume > 0:
                self._unmuted_volume = self._audio_volume
            self.volume_slider.value = self._audio_volume * 100
            self.volume_label.value = f"{round(self._audio_volume * 100)}%"
            self.mute_btn.icon = ft.icons.VOLUME_OFF if self._audio_volume == 0 else ft.icons.VOLUME_UP
            self.mute_btn.tooltip = "Ativar som" if self._audio_volume == 0 else "Silenciar"
            if self.audio_player is not None:
                self.audio_player.volume = self._audio_volume
            self._safe_update()

    def _change_volume(self, e) -> None:
        try:
            self._apply_volume(float(e.control.value) / 100)
        except (TypeError, ValueError):
            return

    def _toggle_mute(self) -> None:
        self._apply_volume(self._unmuted_volume if self._audio_volume == 0 else 0)
        self._save_volume()

    def _dispose_audio(self) -> None:
        player = self.audio_player
        self.audio_player = None  # Ignore delayed events from the old control.
        if player is not None:
            try:
                player.pause()
            except Exception as ex:
                self.log(f"Erro ao pausar prévia anterior: {ex}")
            try:
                player.release()
            except Exception as ex:
                self.log(f"Erro ao liberar prévia: {ex}")
            if player in self.page.overlay:
                self.page.overlay.remove(player)

    def _on_audio_state(self, e) -> None:
        with self._audio_lock:
            if e.control is not self.audio_player:
                return
            if e.data == "completed":
                self._playing_key = None
                self._paused_key = None
                self._now_playing_call = None
                self._update_now_playing_bar()
                self._render_results()

    def _on_audio_time(self, e, duration=False) -> None:
        with self._audio_lock:
            if e.control is not self.audio_player:
                return
            try:
                value = max(0, int(e.data))
            except (TypeError, ValueError):
                return
            if duration:
                self._audio_duration = value
            else:
                self._audio_position = value
            self.now_playing_time.value = f"{self._fmt_ms(self._audio_position)} / {self._fmt_ms(self._audio_duration)}"
            self._safe_update()

    def _toggle_preview(self, key: str, fetch_fn, title: str = "", subtitle: str = "") -> None:
        with self._audio_lock:
            if self._playing_key == key and self.audio_player is not None:
                try:
                    self.audio_player.pause()
                except Exception as ex:
                    self.log(f"Erro ao pausar prévia: {ex}")
                    self._snack("Não foi possível pausar a prévia.", ft.colors.ORANGE_700)
                    return
                self._playing_key = None
                self._paused_key = key
                self._update_now_playing_bar()
                self._render_results()
                return
            if self._paused_key == key and self.audio_player is not None:
                try:
                    self.audio_player.resume()
                except Exception as ex:
                    self.log(f"Erro ao retomar prévia: {ex}")
                    return
                self._paused_key = None
                self._playing_key = key
                self._update_now_playing_bar()
                self._render_results()
                return
            self._preview_generation += 1
            generation = self._preview_generation
            self._dispose_audio()
            self._paused_key = None
            self._playing_key = f"loading:{key}"
            self._audio_duration = self._audio_position = 0
            self.now_playing_time.value = "Carregando…"
            self._now_playing_call = lambda: self._toggle_preview(key, fetch_fn, title, subtitle)
            self._now_playing_meta = (title, subtitle)
            self._update_now_playing_bar()
            self._render_results()

        def worker():
            try:
                url = fetch_fn()
            except Exception as ex:
                self.log(f"Erro ao buscar prévia: {ex}")
                url = None
            with self._audio_lock:
                if generation != self._preview_generation:
                    return
                if not url:
                    self._playing_key = None
                    self._now_playing_call = None
                    self._update_now_playing_bar()
                    self._snack("Prévia indisponível. Tente ouvir na página da loja.", ft.colors.ORANGE_700)
                    self._render_results()
                    return
                def loaded(e):
                    with self._audio_lock:
                        if generation != self._preview_generation or e.control is not self.audio_player:
                            return
                        try:
                            self.audio_player.play()
                        except Exception as ex:
                            self.log(f"Erro ao iniciar prévia: {ex}")
                            self._stop_preview()
                            self._snack("Não foi possível reproduzir a prévia.", ft.colors.ORANGE_700)
                            return
                        self._playing_key = key
                        self.now_playing_time.value = "0:00"
                        self._update_now_playing_bar()
                        self._render_results()
                self.audio_player = ft.Audio(
                    src=url, autoplay=False, volume=self._audio_volume,
                    on_loaded=loaded, on_state_changed=self._on_audio_state,
                    on_position_changed=self._on_audio_time,
                    on_duration_changed=lambda e: self._on_audio_time(e, duration=True),
                )
                self.page.overlay.append(self.audio_player)
                # Mount before play(); play is invoked only by on_loaded.
                self._safe_update()

            def timeout():
                time.sleep(20)
                with self._audio_lock:
                    if generation == self._preview_generation and self._playing_key == f"loading:{key}":
                        self._stop_preview()
                        self._snack("A prévia demorou para carregar. Tente novamente ou abra a loja.", ft.colors.ORANGE_700)
            threading.Thread(target=timeout, daemon=True).start()
        threading.Thread(target=worker, daemon=True).start()

    def _stop_preview(self) -> None:
        with self._audio_lock:
            self._preview_generation += 1
            self._dispose_audio()
            self._playing_key = None
            self._paused_key = None
            self._now_playing_call = None
            self._update_now_playing_bar()
            self._render_results()

    def _now_playing_toggle_click(self) -> None:
        if self._now_playing_call:
            self._now_playing_call()

    def _update_now_playing_bar(self) -> None:
        active_key = self._playing_key or self._paused_key
        if not active_key or not self._now_playing_call:
            self.now_playing_bar.visible = False
            self._safe_update()
            return

        title, subtitle = getattr(self, "_now_playing_meta", ("", ""))
        self.now_playing_title.value = title or "Prévia"
        self.now_playing_subtitle.value = subtitle
        self.now_playing_toggle_btn.disabled = active_key.startswith("loading:")
        is_paused = self._paused_key == active_key
        self.now_playing_toggle_btn.icon = (
            ft.icons.PLAY_CIRCLE_ROUNDED if is_paused else ft.icons.PAUSE_CIRCLE_ROUNDED)
        self.now_playing_toggle_btn.tooltip = "Retomar" if is_paused else "Pausar"
        if is_paused:
            self.now_playing_time.value = f"{self.now_playing_time.value.split(' / ')[0]} · pausado" \
                if self.now_playing_time.value else "pausado"
        self.now_playing_bar.visible = True
        self._safe_update()

    @staticmethod
    def _fmt_ms(ms: Optional[int]) -> str:
        if not ms or ms < 0:
            return "0:00"
        s = int(ms / 1000)
        m, s = divmod(s, 60)
        return f"{m}:{s:02d}"

    def _preview_button(self, key: str, color: str, fetch_fn,
                        title: str = "", subtitle: str = "") -> ft.IconButton:
        loading = self._playing_key == f"loading:{key}"
        playing = self._playing_key == key
        paused = self._paused_key == key
        icon = ft.icons.HOURGLASS_TOP_ROUNDED if loading else (
            ft.icons.PAUSE_CIRCLE_ROUNDED if playing else ft.icons.PLAY_CIRCLE_OUTLINE_ROUNDED)
        tooltip = "Pausar prévia" if playing else (
            "Retomar prévia" if paused else "Ouvir prévia")
        return ft.IconButton(
            icon, icon_color=color, icon_size=26, disabled=loading,
            tooltip=tooltip,
            on_click=lambda e: self._toggle_preview(key, fetch_fn, title, subtitle),
        )

    def _open_youtube_listen(self, query: str) -> None:
        api_key = self.youtube_config.api_key
        if not api_key:
            self._snack("Configure a chave do YouTube primeiro (ícone do robô).",
                        ft.colors.ORANGE_700)
            self._open_ai_settings()
            return

        def worker():
            video = youtube.best_match(query, api_key)
            if video:
                self.page.launch_url(video.url)
            else:
                self._snack("Nada encontrado no YouTube para essa busca.", ft.colors.ORANGE_700)

        threading.Thread(target=worker, daemon=True).start()

    def _build_login_screen(self) -> ft.Container:
        """Primeira tela do app: uma conta só, sem mencionar a rede por trás
        (Soulseek) — a pessoa só vê "conectar" e ganha acesso a tudo."""
        icon_badge = ft.Container(
            content=ft.Icon(ft.icons.ALBUM_ROUNDED, color=ft.colors.WHITE, size=30),
            width=64, height=64, border_radius=18,
            gradient=HEADER_GRADIENT, alignment=ft.alignment.center,
        )
        login_card = ft.Container(
            content=ft.Column([
                icon_badge,
                ft.Text("Music Aggregator", size=22, weight=ft.FontWeight.BOLD, color=INK),
                ft.Text(
                    "Uma conta só te dá acesso a preço em cada loja, prévia pra "
                    "confirmar a faixa e download grátis.",
                    size=13, color=MUTED, text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=6),
                self.user_field,
                self.pass_field,
                self.login_error_text,
                self.login_btn,
                ft.Text(
                    "Criada automaticamente no primeiro acesso — sem cadastro em "
                    "site nenhum.",
                    size=11, color=MUTED, text_align=ft.TextAlign.CENTER,
                ),
                ft.Divider(height=1, color="#EEEDF8"),
                ft.Row([
                    ft.OutlinedButton("Pasta de downloads", icon=ft.icons.FOLDER_OPEN,
                                     style=outline_style(MUTED),
                                     on_click=lambda e: self.folder_picker.get_directory_path(
                                         dialog_title="Onde salvar as músicas baixadas")),
                    self.folder_text,
                ], spacing=10),
                self.share_check,
            ], spacing=14, tight=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
               width=340),
            bgcolor=ft.colors.WHITE, border_radius=RADIUS_LG, padding=32,
            border=ft.border.all(1, "#EBE9F7"), shadow=soft_shadow(blur=30, dy=10, opacity=0.10),
        )
        return ft.Container(content=login_card, alignment=ft.alignment.center, expand=True)

    def _build_layout(self):
        account_bar = ft.Row([ft.Container(expand=True), self.status_pill],
                             alignment=ft.MainAxisAlignment.END)

        search_card = card(
            section_title(ft.icons.SEARCH, "Descobrir músicas",
                          "Mostra onde comprar (Beatport, Bandcamp) e a melhor opção grátis "
                          "no Soulseek, lado a lado.", color=PRIMARY),
            self.search_mode,
            self.producer_notice,
            ft.Row([self.query_field, self.window_dd, self.search_btn, self.ai_settings_btn],
                  spacing=10, vertical_alignment=ft.CrossAxisAlignment.START),
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
            ft.Row([self.result_filter, self.source_filter], spacing=10),
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

        scrollable = ft.Column(
            [account_bar, search_card, results_card, jobs_card, log_card],
            scroll=ft.ScrollMode.AUTO, expand=True, spacing=16,
        )
        # A barra "tocando agora" fica fora da área rolável — sempre visível,
        # não some quando a lista de resultados rola pra baixo.
        return ft.Column([scrollable, self.now_playing_bar], expand=True, spacing=0)

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

    @staticmethod
    def _debrand(msg: str) -> str:
        """Mensagens do motor às vezes citam "Soulseek" (útil no log técnico),
        mas a tela de entrada não deve expor esse nome — vira "sistema"."""
        return msg.replace("Soulseek", "sistema").replace("soulseek", "sistema")

    def _show_login_screen(self) -> None:
        self.login_error_text.visible = False
        if self.engine.is_connected:
            self.login_btn.text = "Desconectar"
            self.login_btn.icon = ft.icons.LOGOUT_ROUNDED
        else:
            self.login_btn.text = "Conectar"
            self.login_btn.icon = ft.icons.ARROW_FORWARD_ROUNDED
        self.control.content = self._login_screen
        self._safe_update()

    def _show_main_app(self) -> None:
        self.control.content = self._main_layout
        self._render_results()
        self._render_jobs()
        self._safe_update()

    def _do_login(self) -> None:
        if self.engine.is_connected:
            self.engine.disconnect()
            self._set_status(MUTED, "Desconectado")
            self.login_btn.text = "Conectar"
            self.login_btn.icon = ft.icons.ARROW_FORWARD_ROUNDED
            self._safe_update()
            return

        self._save_from_fields()
        if not self.config.username or not self.config.password:
            self.login_error_text.value = "Preencha usuário e senha."
            self.login_error_text.visible = True
            self._safe_update()
            return

        self.login_btn.disabled = True
        self.login_btn.text = "Conectando..."
        self.login_error_text.visible = False
        self._set_status("#D97706", "Conectando...")
        self._safe_update()

        def worker():
            ok, msg = self.engine.connect()
            self.login_btn.disabled = False
            if ok:
                self._set_status(FREE_COLOR, f"Conectado como {self.config.username}")
                self._show_main_app()
            else:
                self.login_btn.text = "Conectar"
                self._set_status("#DC2626", "Falha ao conectar")
                self.login_error_text.value = self._debrand(msg)
                self.login_error_text.visible = True
                self._safe_update()

        threading.Thread(target=worker, daemon=True).start()

    # ================= busca =================

    def _change_search_mode(self) -> None:
        producer = self.search_mode.value == "producer"
        self.query_field.label = "Nome artístico do produtor" if producer else "Faixa ou tracklist"
        self.query_field.hint_text = "Ex.: Claudio PRC" if producer else "Artista - Faixa, ou cole um tracklist"
        self.query_field.max_lines = 1 if producer else 8
        self.producer_notice.visible = producer
        self.producer_notice.value = "Créditos das lojas filtrados pelo nome exato. Soulseek: correspondências por nome de arquivo, sem autenticação do áudio."
        self._safe_update()

    def _start_producer_search(self) -> None:
        name = (self.query_field.value or "").strip()
        if not name:
            self._snack("Digite o nome artístico do produtor.", ft.colors.ORANGE_700)
            return
        if self.searching["value"]:
            return
        self._save_from_fields()
        self.searching["value"] = True
        self.search_btn.disabled = True
        self.search_mode.disabled = True
        self.search_progress.visible = True
        self.search_progress.value = None
        self.result_groups = {}
        self.producer_notice.value = f"Procurando faixas creditadas a {name}…"
        self._render_results()

        def update(result):
            self.result_groups = dict(result.tracks)
            self.producer_notice.value = (f"{len(result.tracks)} faixa(s) com crédito correspondente. "
                "Soulseek: correspondência de nomes, sem autenticação do áudio. "
                + " ".join(result.warnings))
            self._render_results()

        def worker():
            try:
                search_producer(name, self.engine, float(self.window_dd.value or 10), update)
            except Exception as ex:
                self.producer_notice.value = f"Não foi possível concluir: {ex}"
                self.log(f"Busca por produtor: {ex}")
            finally:
                self.searching["value"] = False
                self.search_btn.disabled = False
                self.search_mode.disabled = False
                self.search_progress.visible = False
                self._render_results()
        threading.Thread(target=worker, daemon=True).start()

    def _start_search(self) -> None:
        if self.search_mode.value == "producer":
            self._start_producer_search()
            return
        raw = self.query_field.value or ""
        if self.searching["value"]:
            return
        queries = parse_tracklist_to_queries(raw, ai_settings=self.ai_settings, log=self.log)
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

            filtered = [(q, r) for q, r in groups
                        if (self.result_filter.value or "").casefold() in q.casefold()
                        and (self.source_filter.value == "all" or bool(getattr(r, self.source_filter.value or "soulseek", [])))]
            for query, result in filtered:
                self.results_list.controls.append(self._track_card(query, result))
            if not filtered:
                self.results_list.controls.append(ft.Text("Nenhuma faixa corresponde aos filtros.", color=MUTED))

            prontos = sum(1 for _, r in groups if r.done)
            self.results_count.value = f"{len(filtered)} faixa(s) · {prontos}/{len(groups)} concluída(s)"
            self._safe_update()

    def _empty_state(self) -> ft.Container:
        return ft.Container(
            content=ft.Column([
                ft.Container(
                    content=ft.Icon(ft.icons.GRAPHIC_EQ_ROUNDED, size=34, color=PRIMARY),
                    width=72, height=72, border_radius=36, bgcolor=PRIMARY_SOFT,
                    alignment=ft.alignment.center,
                ),
                ft.Text("Busque uma faixa ou um produtor", size=15, weight=ft.FontWeight.BOLD,
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
                    ft.IconButton(
                        ft.icons.SMART_DISPLAY_OUTLINED, icon_color=MUTED, icon_size=22,
                        tooltip="Ouvir no YouTube (abre no navegador) — bom pra faixa que "
                               "não apareceu no Beatport/Bandcamp",
                        on_click=lambda e, q=query: self._open_youtube_listen(q),
                    ),
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
                ft.Row([
                    ft.OutlinedButton("Ver no Beatport", icon=ft.icons.OPEN_IN_NEW,
                                     style=outline_style(BEATPORT_COLOR), expand=True,
                                     url=t.url, url_target=ft.UrlTarget.BLANK),
                    self._preview_button(f"beatport:{t.track_id}", BEATPORT_COLOR,
                                        lambda tid=t.track_id: beatport.get_preview_url(tid),
                                        title=t.full_title,
                                        subtitle=f"{t.artist_label} · Beatport"),
                ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
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
                ft.Row([
                    ft.OutlinedButton("Ver no Bandcamp", icon=ft.icons.OPEN_IN_NEW,
                                     style=outline_style(BANDCAMP_COLOR), expand=True,
                                     url=t.url, url_target=ft.UrlTarget.BLANK),
                    self._preview_button(f"bandcamp:{t.url}", BANDCAMP_COLOR,
                                        lambda u=t.url: bandcamp.get_preview_url(u),
                                        title=t.title,
                                        subtitle=f"{t.artist} · Bandcamp"),
                ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ], spacing=4, tight=True)
        return self._panel_shell(ft.icons.SELL_ROUNDED, "Bandcamp", BANDCAMP_COLOR,
                                 BANDCAMP_SOFT, body)

    def _soulseek_panel(self, query: str, result: TrackResult) -> ft.Container:
        by_format = result.soulseek_best_by_format
        if not any(by_format.values()):
            hint = "conecte sua conta" if not self.engine.is_connected else (
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
            self._snack("Conecte sua conta primeiro.", ft.colors.ORANGE_700)
            self._show_login_screen()
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
