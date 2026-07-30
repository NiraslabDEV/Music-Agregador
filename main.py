import re
import threading

import flet as ft
import requests

from ui.aggregator_view import build_aggregator_view, HEADER_GRADIENT, INK, PAGE_BG, PRIMARY, PRIMARY_SOFT

# Substituido pelo CI (.github/workflows/build-desktop.yml) no build final -
# "0.0.0" significa "rodando local/dev", e nesse caso a checagem de versao
# abaixo nem dispara.
APP_BUILD_VERSION = "0.0.0"

GITHUB_REPO = "NiraslabDEV/Music-Agregador"
DOWNLOAD_PAGE_URL = "https://music-aggregator-ten.vercel.app/#download"


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
    page.theme_mode = ft.ThemeMode.LIGHT
    page.bgcolor = PAGE_BG
    page.padding = 0
    page.fonts = {"Inter": "https://fonts.gstatic.com/s/inter/v18/UcCO3FwrK3iLTeHuS_fvQtMwCp50KnMa1ZL7.woff2"}
    page.theme = ft.Theme(color_scheme_seed=ft.colors.INDIGO, use_material3=True,
                          font_family="Inter")

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
            ft.Container(content=view.control, expand=True, padding=22),
        ], expand=True, spacing=0)
    )

    threading.Thread(target=_check_for_update, args=(page, update_banner), daemon=True).start()


if __name__ == "__main__":
    ft.app(target=main)
