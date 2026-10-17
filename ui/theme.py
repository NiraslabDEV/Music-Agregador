"""Tokens de design e peças visuais reaproveitadas pelas telas do app."""

import os
import subprocess
import sys
from datetime import datetime, timezone

import flet as ft

# ============================== DESIGN TOKENS ==============================

# As cores de superfície/texto são nomes do esquema de cores do tema (definido
# em main.py), não hex: o Flet resolve cada uma no tema ativo, então alternar
# claro/escuro repinta o app inteiro sem reconstruir nenhum controle.
# O fundo da página vem de page.bgcolor (main.py, por tema): no Flet 0.24 o
# nome "background" do esquema virou apelido de "surface" e não serve aqui.
PAGE_BG = ft.colors.TRANSPARENT
SURFACE = ft.colors.SURFACE                 # cards e painéis
SUBTLE = ft.colors.with_opacity(0.035, ft.colors.ON_SURFACE)  # item dentro de card
FIELD_BG = ft.colors.SURFACE_VARIANT        # campos, trilhos, pills neutras
BORDER = ft.colors.OUTLINE_VARIANT
INK = ft.colors.ON_SURFACE                  # texto principal
MUTED = ft.colors.ON_SURFACE_VARIANT        # texto secundário

PRIMARY = ft.colors.PRIMARY
PRIMARY_SOFT = ft.colors.PRIMARY_CONTAINER  # fundos/pills com tom do primary
BRAND = "#4F46E5"            # indigo-600 fixo, pra gradientes (iguais nos dois temas)
PRIMARY_DARK = "#3730A3"     # indigo-800

BEATPORT_COLOR = "#E11D74"   # coral/magenta — remete a dance music sem copiar marca
BANDCAMP_COLOR = "#0F91A8"   # teal
FREE_COLOR = "#16A34A"       # verde "grátis"
# Tints translúcidos funcionam sobre fundo claro e escuro.
BEATPORT_SOFT = ft.colors.with_opacity(0.13, BEATPORT_COLOR)
BANDCAMP_SOFT = ft.colors.with_opacity(0.13, BANDCAMP_COLOR)
FREE_SOFT = ft.colors.with_opacity(0.13, FREE_COLOR)
SOULSEEK_NEUTRAL = MUTED     # cor neutra do painel Soulseek (o destaque é o verde do preço)

RADIUS_LG = 18
RADIUS_MD = 14
RADIUS_SM = 10

HEADER_GRADIENT = ft.LinearGradient(
    begin=ft.alignment.top_left, end=ft.alignment.bottom_right,
    colors=[PRIMARY_DARK, BRAND, "#7C3AED"],
)


def _plural(n: int, singular: str, plural: str = None) -> str:
    plural = plural or f"{singular}s"
    return f"{n} {singular}" if n == 1 else f"{n} {plural}"


def soft_shadow(blur=18, dy=6, opacity=0.07) -> ft.BoxShadow:
    return ft.BoxShadow(blur_radius=blur, spread_radius=0,
                        color=ft.colors.with_opacity(opacity, "#2E1065"),
                        offset=ft.Offset(0, dy))


def _relative_time(sqlite_utc: str) -> str:
    """'2026-10-16 17:02:11' (UTC do SQLite) -> 'há 5 min', 'ontem 14:32'..."""
    try:
        when = datetime.strptime(sqlite_utc, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return ""
    when = when.replace(tzinfo=timezone.utc).astimezone()
    now = datetime.now().astimezone()
    seconds = (now - when).total_seconds()
    if seconds < 60:
        return "agora há pouco"
    if seconds < 3600:
        return f"há {int(seconds // 60)} min"
    days = (now.date() - when.date()).days
    if days == 0:
        return f"hoje {when:%H:%M}"
    if days == 1:
        return f"ontem {when:%H:%M}"
    return f"{when:%d/%m/%Y}"


def _open_path(path: str, reveal: bool = False) -> None:
    """Abre o arquivo no player padrão do sistema, ou mostra ele na pasta."""
    if sys.platform == "win32":
        if reveal:
            subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')
        else:
            os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path] if reveal else ["open", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path) if reveal else path])


def card(*controls, expand=False, padding=20) -> ft.Container:
    return ft.Container(
        content=ft.Column(list(controls), spacing=10, tight=True),
        bgcolor=SURFACE, border_radius=RADIUS_LG, padding=padding,
        border=ft.border.all(1, BORDER),
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
        bgcolor=bg, color=fg or ft.colors.ON_PRIMARY,
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
