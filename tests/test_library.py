from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from core.soulseek import Candidate, DownloadHistory, DownloadJob
from ui import aggregator_view as module


@pytest.fixture
def view(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "_data_dir", lambda: tmp_path)
    page = SimpleNamespace(overlay=[], update=lambda: None)
    v = module.AggregatorView(page)
    v.engine.history = DownloadHistory(tmp_path / "history.db")
    v.control.content = v._main_layout
    v._go_to(module.NAV_LIBRARY)
    yield v
    v.shutdown()


def _download(view, tmp_path, name="Daft Punk - Around The World · FLAC"):
    audio = tmp_path / "around.flac"
    audio.write_bytes(b"x")
    cand = Candidate(username="dj", remote_path="music\\around.flac", size=30 * 1024 * 1024,
                     extension="flac")
    view.engine.history.add(name, cand, str(audio))
    return cand


def test_filter_and_remove(view, tmp_path):
    _download(view, tmp_path)
    view._reload_library()
    view.library_filter.value = "blue monday"
    view._render_library()
    assert view.library_count.value == "0 de 1"
    view._remove_from_library(view._library[0]["id"])
    assert view._library == []
    assert (tmp_path / "around.flac").exists()  # tirar da lista não apaga o arquivo


def test_completed_download_shows_up_in_library(view, tmp_path):
    assert view._library == []
    cand = _download(view, tmp_path)
    view._on_job_update(DownloadJob(label="Daft Punk - Around The World · FLAC", state="complete"))
    assert len(view._library) == 1
    assert (cand.username, cand.remote_path) in view._downloaded
    assert view.library_count.value == "1 música"
    assert view.page.snack_bar.action == "Ver na biblioteca"


def test_library_survives_restart(monkeypatch, tmp_path):
    db = tmp_path / "history.db"
    DownloadHistory(db).add("Faixa · MP3", Candidate(username="u", remote_path="a.mp3"), "")
    monkeypatch.setattr(module, "_data_dir", lambda: tmp_path)
    v = module.AggregatorView(SimpleNamespace(overlay=[], update=lambda: None))
    v.engine.history = DownloadHistory(db)
    v._reload_library()
    assert [e["label"] for e in v._library] == ["Faixa · MP3"]
    v.shutdown()


def test_recent_searches_are_saved_deduped_and_capped(view, tmp_path):
    for i in range(module.RECENT_SEARCHES_MAX + 3):
        view._remember_search(f"faixa {i}", "tracks")
    view._remember_search("faixa 5", "tracks")
    assert len(view.recent_searches) == module.RECENT_SEARCHES_MAX
    assert view.recent_searches[0] == {"q": "faixa 5", "mode": "tracks"}
    assert [d["q"] for d in view.recent_searches].count("faixa 5") == 1
    assert view._load_recent_searches() == view.recent_searches


def test_relative_time():
    now = datetime.now(timezone.utc)
    fmt = lambda d: d.strftime("%Y-%m-%d %H:%M:%S")
    assert module._relative_time(fmt(now)) == "agora há pouco"
    assert module._relative_time(fmt(now - timedelta(minutes=5))) == "há 5 min"
    assert module._relative_time(fmt(now - timedelta(days=10))).count("/") == 2
    assert module._relative_time("lixo") == ""


def test_library_track_plays_inside_app(view, tmp_path, monkeypatch):
    started = {}
    monkeypatch.setattr(view, "_toggle_preview",
                        lambda key, fetch, title="", subtitle="": started.update(
                            key=key, url=fetch(), title=title, subtitle=subtitle))
    _download(view, tmp_path)
    view._reload_library()
    row = view.library_list.controls[0]
    play = next(c for c in row.content.controls
                if getattr(c, "tooltip", None) == "Ouvir aqui no app")
    play.on_click(None)
    assert started["key"] == f"file:{tmp_path / 'around.flac'}"
    assert started["url"].startswith("http://127.0.0.1:")
    assert started["title"] == "Daft Punk - Around The World"
    assert started["subtitle"] == "Biblioteca · FLAC"
