"""Baixar o áudio do YouTube: escolher o vídeo certo e mandar pra fila.

Mixin do AggregatorView — usa os diálogos, snacks e a fila de downloads dele.
A pessoa sempre escolhe o vídeo (o 1º resultado às vezes é um clipe com
intro, um cover ou um "1 hora em loop"); nada é baixado sozinho.
"""

import os
import threading

import flet as ft

from core import youtube
from core.youtube import YouTubeVideo
from core.youtube_download import (
    YouTubeDownloader, default_download_dir, ffmpeg_path, format_duration, search_videos,
)
from ui.theme import (
    BORDER, INK, MUTED, RADIUS_SM, SUBTLE, YOUTUBE_COLOR, _open_path, outline_style,
    pstyle,
)


class YouTubeMixin:
    def _yt_init(self) -> None:
        self.yt = YouTubeDownloader(lambda: self.engine.history, self._yt_dir, log=self.log)
        self._yt_folder_text = None
        self.yt_folder_picker = ft.FilePicker(on_result=self._on_yt_folder_picked)
        self.page.overlay.append(self.yt_folder_picker)

    def _yt_dir(self) -> str:
        return self.youtube_config.download_dir or default_download_dir()

    def _pick_yt_folder(self) -> None:
        self.yt_folder_picker.get_directory_path(
            dialog_title="Onde salvar as músicas baixadas do YouTube")

    def _on_yt_folder_picked(self, e: ft.FilePickerResultEvent) -> None:
        if not e.path:
            return
        self.youtube_config.download_dir = e.path
        self.youtube_config.save()
        if self._yt_folder_text is not None:
            self._yt_folder_text.value = e.path
        self._snack(f"Downloads do YouTube vão para: {e.path}")

    def _open_yt_dir(self) -> None:
        folder = self._yt_dir()
        try:
            os.makedirs(folder, exist_ok=True)
            _open_path(folder)
        except Exception as ex:
            self.log(f"Não foi possível abrir a pasta {folder}: {ex}")

    # ================= diálogo =================

    def _yt_search(self, query: str) -> list[YouTubeVideo]:
        try:
            return search_videos(query)
        except Exception as ex:
            self.log(f"Busca no YouTube (sem chave) falhou: {ex}")
            if self.youtube_config.api_key:
                return youtube.search(query, self.youtube_config.api_key, limit=6)
            raise

    def _open_youtube_dialog(self, query: str) -> None:
        if not self.yt.available:
            self._snack("Falta o yt-dlp: rode  pip install -r requirements-desktop.txt",
                        ft.colors.ORANGE_700)
            return
        status = ft.Row([
            ft.ProgressRing(width=16, height=16, stroke_width=2, color=YOUTUBE_COLOR),
            ft.Text("Procurando no YouTube…", size=12, color=MUTED),
        ], spacing=8)
        rows = ft.ListView(spacing=6, height=360)
        self._yt_folder_text = ft.Text(self._yt_dir(), size=11, color=MUTED, expand=True,
                                       no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS)
        quality_note = ("Sai em M4A (AAC) — a qualidade é a do YouTube, ~128 kbps."
                        if ffmpeg_path() else
                        "Sai no M4A que o YouTube oferece (~128 kbps). Instale o ffmpeg "
                        "pra converter os vídeos que só têm Opus.")
        dialog = ft.AlertDialog(
            title=ft.Row([
                ft.Icon(ft.icons.SMART_DISPLAY_ROUNDED, color=YOUTUBE_COLOR),
                ft.Text(query, size=16, weight=ft.FontWeight.BOLD, color=INK, expand=True,
                        max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
            ], spacing=10),
            content=ft.Column([
                ft.Text("Escolha o vídeo certo: dá pra abrir no navegador pra ouvir antes. "
                        + quality_note, size=12, color=MUTED),
                status,
                rows,
                ft.Divider(height=1, color=BORDER),
                ft.Row([
                    ft.Icon(ft.icons.FOLDER_OUTLINED, size=16, color=MUTED),
                    self._yt_folder_text,
                    ft.TextButton("Trocar pasta", on_click=lambda e: self._pick_yt_folder()),
                ], spacing=8),
            ], spacing=10, tight=True, width=620),
            actions=[ft.TextButton("Fechar", on_click=lambda e: self._close_dialog())],
        )
        self._show_dialog(dialog)

        def worker():
            try:
                videos = self._yt_search(query)
                error = "" if videos else "Nada encontrado no YouTube para essa busca."
            except Exception:
                videos, error = [], "Não foi possível buscar no YouTube agora (sem internet?)."
            if self._dialog is not dialog:
                return  # fechou antes de a busca voltar
            status.visible = bool(error)
            status.controls = [ft.Icon(ft.icons.INFO_OUTLINE, size=16, color=MUTED),
                               ft.Text(error, size=12, color=MUTED)]
            rows.controls = [self._yt_video_row(query, v) for v in videos]
            self._safe_update()

        threading.Thread(target=worker, daemon=True).start()

    def _yt_video_row(self, query: str, video: YouTubeVideo) -> ft.Container:
        meta = " · ".join(x for x in (video.channel, format_duration(video.duration)) if x)
        thumb = (ft.Image(src=video.thumbnail_url, width=96, height=54, fit=ft.ImageFit.COVER,
                          border_radius=6,
                          error_content=ft.Icon(ft.icons.SMART_DISPLAY_OUTLINED, color=MUTED))
                 if video.thumbnail_url else
                 ft.Container(ft.Icon(ft.icons.SMART_DISPLAY_OUTLINED, color=MUTED),
                              width=96, height=54, alignment=ft.alignment.center))
        download_btn = ft.ElevatedButton("Baixar áudio", icon=ft.icons.DOWNLOAD_ROUNDED,
                                         style=pstyle(YOUTUBE_COLOR, ft.colors.WHITE))

        def download(e):
            self._download_youtube(query, video)
            download_btn.text, download_btn.icon = "Na fila", ft.icons.SCHEDULE_ROUNDED
            download_btn.disabled = True
            self._update_controls(download_btn)

        download_btn.on_click = download
        return ft.Container(
            content=ft.Row([
                thumb,
                ft.Column([
                    ft.Text(video.title, size=12, weight=ft.FontWeight.W_600, color=INK,
                            max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(meta, size=11, color=MUTED, no_wrap=True,
                            overflow=ft.TextOverflow.ELLIPSIS),
                ], spacing=2, tight=True, expand=True),
                ft.IconButton(ft.icons.OPEN_IN_NEW_ROUNDED, icon_color=MUTED, icon_size=18,
                              tooltip="Abrir no YouTube pra ouvir",
                              url=video.url, url_target=ft.UrlTarget.BLANK),
                download_btn,
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=8, bgcolor=SUBTLE, border_radius=RADIUS_SM,
        )

    def _download_youtube(self, query: str, video: YouTubeVideo) -> None:
        # "· YouTube" no fim: a Biblioteca tira o título da faixa do rótulo
        # (tudo antes do último " · "), igual aos downloads do Soulseek.
        label = f"{query} · YouTube"
        self.yt.enqueue(video, label, on_update=self._on_job_update)
        self.log(f"YouTube na fila: {video.title} ({video.url})")
        self._render_jobs()
        self._snack(f"Baixando do YouTube: {query}", action="Ver downloads",
                    on_action=lambda e: self._go_to(self.NAV_DOWNLOADS))

    def _yt_folder_button(self) -> ft.OutlinedButton:
        return ft.OutlinedButton("Pasta do YouTube", icon=ft.icons.FOLDER_OPEN,
                                 style=outline_style(YOUTUBE_COLOR),
                                 tooltip="Os áudios baixados do YouTube ficam numa pasta à parte",
                                 on_click=lambda e: self._open_yt_dir())
