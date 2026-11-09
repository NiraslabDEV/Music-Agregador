from pathlib import Path
from types import SimpleNamespace

import pytest

from core import youtube_download as ytd
from core.aggregator import TrackResult
from core.crates import LibraryStore, favorite_status
from core.soulseek import Candidate, DownloadHistory, DownloadJob
from core.youtube import YouTubeVideo
from ui import aggregator_view as module


@pytest.fixture
def view(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "_data_dir", lambda: tmp_path)
    page = SimpleNamespace(overlay=[], update=lambda: None, launch_url=lambda url: None)
    v = module.AggregatorView(page)
    v.engine.history = DownloadHistory(tmp_path / "history.db")
    v.control.content = v._main_layout
    yield v
    v.shutdown()


def _result(query, soulseek=()):
    return TrackResult(query=query, soulseek=list(soulseek), done=True)


FLAC = Candidate(username="dj", remote_path="music\\track.flac", size=30 * 1024 * 1024,
                 extension="flac", bit_depth=16, sample_rate=44100)


# ============================== store ==============================

def test_favorite_store_dedupes_and_keeps_store_links(tmp_path):
    store = LibraryStore(tmp_path / "lib.db")
    fav_id = store.add_favorite("  Moodymann -  Shades of Jae ", beatport_url="https://bp/1")
    assert store.add_favorite("moodymann - shades of jae", beatport_url="https://bp/2") == fav_id
    fav = store.favorite("Moodymann - Shades of Jae")
    assert fav["query"] == "Moodymann - Shades of Jae"
    assert fav["beatport_url"] == "https://bp/1"   # favoritar de novo não sobrescreve
    assert favorite_status(fav) == "never"

    store.record_check(fav_id, 0, "")
    assert favorite_status(store.favorite("Moodymann - Shades of Jae")) == "missing"
    store.record_check(fav_id, 4, "FLAC 44.1kHz 16bit", bandcamp_url="https://bc/1",
                       beatport_url="")
    fav = store.favorite("Moodymann - Shades of Jae")
    assert favorite_status(fav) == "available"
    assert fav["soulseek_best"] == "FLAC 44.1kHz 16bit"
    assert fav["bandcamp_url"] == "https://bc/1"
    assert fav["beatport_url"] == "https://bp/1"   # loja sem resposta não apaga o link

    store.remove_favorite(fav_id)
    assert store.favorites() == []


def test_empty_favorite_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        LibraryStore(tmp_path / "lib.db").add_favorite("   ")


# ============================== tela de favoritas ==============================

def test_star_on_result_card_toggles_favorite(view):
    result = _result("Theo Parrish - Summertime")
    star = view._fav_star_button(result.query, result)
    assert star.icon == module.ft.icons.STAR_BORDER_ROUNDED
    star.on_click(None)
    assert view._favorite_for("theo parrish - summertime") is not None
    assert star.icon == module.ft.icons.STAR_ROUNDED
    star.on_click(None)
    assert view._favorites == []


def test_check_records_soulseek_availability(view):
    view.engine._connected.set()
    view.store.add_favorite("Theo Parrish - Summertime")
    view._reload_favorites()
    view._record_favorite_result("Theo Parrish - Summertime", _result("x", [FLAC]))
    fav = view._favorite_for("Theo Parrish - Summertime")
    assert favorite_status(fav) == "available" and fav["soulseek_count"] == 1

    view._go_to(module.NAV_FAVORITES)
    assert view.fav_count.value == "1 faixa · 1 com fonte"
    view.fav_status_dd.value = "missing"
    view._render_favorites()
    assert view.fav_count.value.startswith("0 de 1")


def test_check_while_disconnected_does_not_mark_missing(view):
    view.store.add_favorite("Rare Edit")
    view._reload_favorites()
    view._record_favorite_result("Rare Edit", _result("Rare Edit"))
    assert favorite_status(view._favorite_for("Rare Edit")) == "never"


def test_favorite_counts_as_owned_after_youtube_download(view, tmp_path):
    view.store.add_favorite("Rare Edit")
    audio = tmp_path / "rare.m4a"
    audio.write_bytes(b"x")
    view.engine.history.add_entry("Rare Edit · YouTube", ytd.YOUTUBE_USER,
                                  "https://www.youtube.com/watch?v=abc", str(audio), 1,
                                  "YouTube · M4A ~129kbps")
    view._reload_library()
    view._reload_favorites()
    view._go_to(module.NAV_FAVORITES)
    view.fav_status_dd.value = "owned"
    view._render_favorites()
    assert len(view.fav_list.controls) == 1

    view._go_to(module.NAV_LIBRARY)
    assert view.library_count.value == "1 música"   # linha com selo do YouTube desenha


def test_add_favorite_manually_rejects_duplicates(view):
    view._add_favorite_manually()
    field = view._dialog.content.controls[0]
    field.value = "Rare Edit"
    field.on_submit(None)
    assert view._favorite_for("Rare Edit")
    view._add_favorite_manually()
    field = view._dialog.content.controls[0]
    error = view._dialog.content.controls[-1]
    field.value = "rare edit"
    field.on_submit(None)
    assert error.visible and len(view._favorites) == 1


# ============================== YouTube ==============================

class FakeYDL:
    """Imita o yt_dlp.YoutubeDL: chama os hooks e grava o arquivo final."""

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        folder = Path(self.opts["outtmpl"]).parent
        path = folder / "Rare Edit [abc].m4a"
        for hook in self.opts["progress_hooks"]:
            hook({"status": "downloading", "downloaded_bytes": 5, "total_bytes": 10,
                  "speed": 1024})
            hook({"status": "finished"})
        path.write_bytes(b"0123456789")
        return {"abr": 129.5, "requested_downloads": [{"filepath": str(path)}]}


def _run_job(monkeypatch, tmp_path, ydl=FakeYDL, cancel=False):
    monkeypatch.setattr(ytd, "yt_dlp", SimpleNamespace(YoutubeDL=ydl))
    history = DownloadHistory(tmp_path / "h.db")
    updates = []
    dl = ytd.YouTubeDownloader(lambda: history, lambda: str(tmp_path / "yt"))
    video = YouTubeVideo(video_id="abc", title="Rare Edit", channel="Someone")
    job = ytd.YouTubeJob(label="Rare Edit · YouTube", video=video,
                         _on_update=lambda j: updates.append(j.state))
    job.cancelled = cancel
    dl._run(job)
    dl.shutdown()
    return job, history, updates


def test_youtube_download_lands_in_its_own_folder_and_library(monkeypatch, tmp_path):
    job, history, updates = _run_job(monkeypatch, tmp_path)
    assert job.state == "complete", job.error
    assert Path(job.local_path).parent == tmp_path / "yt"
    assert "downloading" in updates and "converting" in updates
    [entry] = history.recent()
    assert entry["username"] == ytd.YOUTUBE_USER and ytd.is_youtube_entry(entry)
    assert entry["quality"] == "YouTube · M4A ~130kbps"
    assert entry["remote_path"] == "https://www.youtube.com/watch?v=abc"


def test_youtube_cancel_and_failure(monkeypatch, tmp_path):
    job, history, _ = _run_job(monkeypatch, tmp_path, cancel=True)
    assert job.state == "cancelled"  # nem começa
    assert history.recent() == []

    class Broken(FakeYDL):
        def extract_info(self, url, download=False):
            raise ytd.DownloadError("ERROR: Video unavailable")

    job, history, _ = _run_job(monkeypatch, tmp_path, ydl=Broken)
    assert job.state == "failed" and job.detail == "Video unavailable"
    assert history.recent() == []


def test_youtube_job_shows_in_download_queue(view, monkeypatch):
    monkeypatch.setattr(view.yt._pool, "submit", lambda *a, **k: None)  # não baixa de verdade
    video = YouTubeVideo(video_id="abc", title="Rare Edit")
    view._download_youtube("Rare Edit", video)
    view._render_jobs()
    assert view.jobs_count.value.startswith("1 item")
    [job] = view.yt.jobs()
    assert job.label == "Rare Edit · YouTube"
    view._cancel_job(job)
    assert job.state == "cancelled"
    view._clear_finished()
    assert view.yt.jobs() == []


def test_completed_youtube_job_refreshes_library(view, tmp_path):
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"x")
    view.engine.history.add_entry("Rare Edit · YouTube", ytd.YOUTUBE_USER, "u", str(audio), 1, "")
    job = ytd.YouTubeJob(label="Rare Edit · YouTube",
                         video=YouTubeVideo(video_id="abc", title="t"), state="complete")
    view._on_job_update(job)
    assert len(view._library) == 1
    assert view.page.snack_bar.action == "Ver na biblioteca"


def test_search_entry_parsing_and_labels():
    video = ytd._video_from_entry({"id": "x1", "title": "T", "uploader": "U", "duration": 3725,
                                   "thumbnails": [{"url": "http://img"}]})
    assert (video.channel, video.thumbnail_url, video.url) == (
        "U", "http://img", "https://www.youtube.com/watch?v=x1")
    assert ytd.format_duration(video.duration) == "1:02:05"
    assert ytd.format_duration(None) == ""
    assert ytd._video_from_entry({"title": "sem id"}) is None
    assert ytd.quality_label({}, "a.webm") == "WEBM"


def test_exact_soulseek_download_keeps_library_label(view, monkeypatch):
    calls = []
    view.engine._connected.set()
    monkeypatch.setattr(view.engine, "enqueue", lambda c, **kw: calls.append(kw))
    view._download_candidate("Rare Edit", _result("Rare Edit", [FLAC]), FLAC, exact=True)
    assert calls[0]["label"] == "Rare Edit · FLAC" and calls[0]["pool"] is None


def test_job_row_renders_both_kinds(view):
    yt_job = ytd.YouTubeJob(label="a · YouTube", video=YouTubeVideo(video_id="v", title="t"),
                            state="converting")
    assert view._job_row(yt_job) is not None
    assert view._job_row(DownloadJob(label="b · FLAC", state="downloading")) is not None


@pytest.mark.skipif(not ytd.ffmpeg_path(), reason="precisa do ffmpeg no PATH")
def test_analysis_reads_m4a_through_ffmpeg(tmp_path):
    import subprocess

    import numpy as np
    import soundfile as sf

    from core import analysis

    wav, m4a = tmp_path / "tone.wav", tmp_path / "tone.m4a"
    t = np.arange(0, 5, 1 / 44100)
    sf.write(wav, 0.3 * np.sin(2 * np.pi * 440 * t), 44100)
    subprocess.run([ytd.ffmpeg_path(), "-v", "error", "-y", "-i", str(wav), str(m4a)], check=True)
    mono = analysis.load_mono(str(m4a))
    assert abs(len(mono) / analysis.SR - 5) < 0.2   # curta: usa do começo, sem pular a intro


def test_youtube_dialog_lists_videos_and_queues_download(view, monkeypatch):
    from ui import youtube_view

    class SyncThread:
        def __init__(self, target, args=(), daemon=None):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(youtube_view.threading, "Thread", SyncThread)
    monkeypatch.setattr(youtube_view, "search_videos", lambda q: [
        YouTubeVideo(video_id="a1", title="Rare Edit (VIP)", channel="Label", duration=402),
        YouTubeVideo(video_id="a2", title="Rare Edit 1 hour loop", thumbnail_url="http://i")])
    monkeypatch.setattr(view.yt._pool, "submit", lambda *a, **k: None)

    view._open_youtube_dialog("Rare Edit")
    rows = view._dialog.content.controls[2].controls
    assert len(rows) == 2
    download_btn = rows[0].content.controls[-1]
    download_btn.on_click(None)
    assert download_btn.disabled and download_btn.text == "Na fila"
    [job] = view.yt.jobs()
    assert job.video.video_id == "a1" and job.label == "Rare Edit · YouTube"


def test_youtube_dialog_falls_back_to_api_key(view, monkeypatch):
    from ui import youtube_view

    def offline(q):
        raise ytd.DownloadError("offline")

    monkeypatch.setattr(youtube_view, "search_videos", offline)
    monkeypatch.setattr(youtube_view.youtube, "search", lambda q, key, limit: ["api"])
    with pytest.raises(ytd.DownloadError):
        view._yt_search("x")
    view.youtube_config.api_key = "k"
    assert view._yt_search("x") == ["api"]
