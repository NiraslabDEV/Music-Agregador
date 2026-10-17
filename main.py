import json
import re
import threading

import flet as ft
import requests

from core.soulseek import _data_dir
from ui.aggregator_view import build_aggregator_view, HEADER_GRADIENT, INK, PRIMARY, PRIMARY_SOFT

# Substituido pelo CI (.github/workflows/build-desktop.yml) no build final -
# "0.0.0" significa "rodando local/dev", e nesse caso a checagem de versao
# abaixo nem dispara.
APP_BUILD_VERSION = "0.0.0"

GITHUB_REPO = "NiraslabDEV/Music-Agregador"
DOWNLOAD_PAGE_URL = "https://music-aggregator-ten.vercel.app/#download"

# Os controles usam nomes do esquema (ft.colors.SURFACE etc.); aqui é onde
# cada nome vira cor de verdade em cada tema.
LIGHT_SCHEME = ft.ColorScheme(
    primary="#4F46E5", on_primary="#FFFFFF",
    primary_container="#EEF0FE", on_primary_container="#312E81",
    surface="#FFFFFF", on_surface="#1E1B33",
    surface_variant="#F4F3FA", on_surface_variant="#6B6684",
    outline_variant="#EBE9F7", surface_tint="#FFFFFF",
)
DARK_SCHEME = ft.ColorScheme(
    primary="#A5B4FC", on_primary="#1E1B4B",
    primary_container="#2B2860", on_primary_container="#E0E7FF",
    surface="#181622", on_surface="#ECEAF6",
    surface_variant="#24212F", on_surface_variant="#A29EB9",
    outline_variant="#2D2A3B", surface_tint="#181622",
)
PAGE_BG_LIGHT = "#F3F2FB"   # lavanda bem suave
PAGE_BG_DARK = "#0E0D15"
UI_PREFS = "ui.json"


def _load_theme_mode() -> ft.ThemeMode:
    try:
        saved = json.loads((_data_dir() / UI_PREFS).read_text(encoding="utf-8")).get("theme")
    except (OSError, ValueError, AttributeError):
        saved = None
    return {"light": ft.ThemeMode.LIGHT, "dark": ft.ThemeMode.DARK}.get(saved, ft.ThemeMode.SYSTEM)


def _save_theme_mode(mode: ft.ThemeMode) -> None:
    try:
        (_data_dir() / UI_PREFS).write_text(
            json.dumps({"theme": "dark" if mode == ft.ThemeMode.DARK else "light"}),
            encoding="utf-8")
    except OSError:
        pass


def _is_dark(page: ft.Page) -> bool:
    if page.theme_mode == ft.ThemeMode.SYSTEM:
        return page.platform_brightness == ft.Brightness.DARK
    return page.theme_mode == ft.ThemeMode.DARK


def _parse_version(text: str):
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(part) for part in match.groups()) if match else None


def _check_for_update(page: ft.Page, banner: ft.Container) -> None:
    local_version = _parse_version(APP_BUILD_VERSION)
    if not local_version or local_version == (0, 0, 0):
        return  # build local/dev - nao interessa checar atualizacao
    try:
        response = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/latest",
            timeout=4,
            headers={"Accept": "application/vnd.github+json"},
        )
        if response.status_code != 200:
            return
        remote_version = _parse_version(response.json().get("name", ""))
    except Exception:
        return  # sem internet / GitHub fora do ar - ignora em silencio

    if remote_version and remote_version > local_version:
        banner.visible = True
        page.update()


def main(page: ft.Page):
    page.title = "Music Aggregator"
    page.window.width = 1120
    page.window.height = 880
    page.window.min_width = 860
    page.window.min_height = 640
    page.theme_mode = _load_theme_mode()  # sem escolha salva, segue o sistema
    page.padding = 0
    page.fonts = {"Inter": "https://fonts.gstatic.com/s/inter/v18/UcCO3FwrK3iLTeHuS_fvQtMwCp50KnMa1ZL7.woff2"}
    page.theme = ft.Theme(color_scheme=LIGHT_SCHEME, use_material3=True, font_family="Inter")
    page.dark_theme = ft.Theme(color_scheme=DARK_SCHEME, use_material3=True, font_family="Inter")

    theme_btn = ft.IconButton(icon_color=ft.colors.WHITE, icon_size=20)

    def sync_theme_btn():
        dark = _is_dark(page)
        page.bgcolor = PAGE_BG_DARK if dark else PAGE_BG_LIGHT
        theme_btn.icon = ft.icons.LIGHT_MODE_ROUNDED if dark else ft.icons.DARK_MODE_ROUNDED
        theme_btn.tooltip = "Modo claro" if dark else "Modo escuro"

    def toggle_theme(e):
        page.theme_mode = ft.ThemeMode.LIGHT if _is_dark(page) else ft.ThemeMode.DARK
        _save_theme_mode(page.theme_mode)
        sync_theme_btn()
        page.update()

    def on_system_theme(e):
        if page.theme_mode == ft.ThemeMode.SYSTEM:
            sync_theme_btn()
            page.update()

    theme_btn.on_click = toggle_theme
    page.on_platform_brightness_change = on_system_theme
    sync_theme_btn()

    icon_badge = ft.Container(
        content=ft.Icon(ft.icons.ALBUM_ROUNDED, color=ft.colors.WHITE, size=22),
        width=42, height=42, border_radius=12,
        bgcolor=ft.colors.with_opacity(0.18, ft.colors.WHITE),
        alignment=ft.alignment.center,
    )

    header = ft.Container(
        content=ft.Row([
            icon_badge,
            ft.Column([
                ft.Text("Music Aggregator", size=19, weight=ft.FontWeight.BOLD,
                       color=ft.colors.WHITE),
                ft.Text("Preço em cada loja + a melhor opção grátis", size=11,
                       color=ft.colors.with_opacity(0.75, ft.colors.WHITE)),
            ], spacing=0, tight=True),
            ft.Container(expand=True),
            ft.Row([
                ft.Icon(ft.icons.SELL_OUTLINED, size=14, color=ft.colors.with_opacity(0.8, ft.colors.WHITE)),
                ft.Text("Beatport", size=12, color=ft.colors.with_opacity(0.85, ft.colors.WHITE)),
                ft.Text("·", size=12, color=ft.colors.with_opacity(0.5, ft.colors.WHITE)),
                ft.Text("Bandcamp", size=12, color=ft.colors.with_opacity(0.85, ft.colors.WHITE)),
                ft.Text("·", size=12, color=ft.colors.with_opacity(0.5, ft.colors.WHITE)),
                ft.Text("Soulseek", size=12, color=ft.colors.with_opacity(0.85, ft.colors.WHITE)),
            ], spacing=6),
            theme_btn,
        ], spacing=14, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        gradient=HEADER_GRADIENT,
        padding=ft.padding.symmetric(horizontal=24, vertical=16),
    )

    update_banner = ft.Container(
        visible=False,
        bgcolor=PRIMARY_SOFT,
        padding=ft.padding.symmetric(horizontal=16, vertical=10),
        content=ft.Row(
            [
                ft.Icon(ft.icons.NEW_RELEASES_OUTLINED, color=PRIMARY, size=18),
                ft.Text("Tem uma versão nova do app disponível.", size=13, color=INK, expand=True),
                ft.TextButton(
                    "Baixar atualização",
                    on_click=lambda e: page.launch_url(DOWNLOAD_PAGE_URL),
                ),
            ],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )

    view = build_aggregator_view(page)
    page.on_disconnect = lambda e: view.shutdown()

    page.add(
        ft.Column([
            header,
            update_banner,
            ft.Container(content=view.control, expand=True),
        ], expand=True, spacing=0)
    )

    threading.Thread(target=_check_for_update, args=(page, update_banner), daemon=True).start()


if __name__ == "__main__":
    ft.app(target=main)
