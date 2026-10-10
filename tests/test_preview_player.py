import threading
from types import SimpleNamespace

import pytest
from ui import aggregator_view as module


class FakeAudio:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
        self.play_calls = 0
        self.pause_calls = 0
        self.released = False

    def play(self):
        self.play_calls += 1

    def pause(self):
        self.pause_calls += 1

    def resume(self):
        self.play_calls += 1

    def release(self):
        self.released = True


@pytest.fixture
def view(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "_data_dir", lambda: tmp_path)
    monkeypatch.setattr(module.ft, "Audio", FakeAudio)
    mounted = threading.Event()
    page = SimpleNamespace(overlay=[], update=mounted.set)
    v = module.AggregatorView(page)
    v._render_results = lambda: None
    v._mounted = mounted
    yield v
    v.shutdown()


def mount(view, key="one"):
    ready = threading.Event()
    original_update = view.page.update
    def update():
        original_update()
        if view.audio_player is not None:
            ready.set()
    view.page.update = update
    view._toggle_preview(key, lambda: "https://example.com/audio.mp3")
    assert ready.wait(2)
    return view.audio_player


def test_starts_quiet_and_waits_for_loaded(view):
    player = mount(view)
    assert player.volume == 0.20
    assert player.autoplay is False
    assert player.play_calls == 0
    player.on_loaded(SimpleNamespace(control=player))
    assert player.play_calls == 1
    assert view._playing_key == "one"
    view._apply_volume(0.07)
    assert player.volume == 0.07
    view._toggle_mute()
    assert player.volume == 0
    view._toggle_mute()
    assert player.volume == 0.07


def test_stop_cancels_pending_fetch(view):
    pending, release = threading.Event(), threading.Event()
    def fetch():
        pending.set()
        release.wait(2)
        return "https://example.com/old.mp3"
    view._toggle_preview("old", fetch)
    assert pending.wait(2)
    view._stop_preview()
    release.set()
    # Start a new request and ensure the old response cannot replace it.
    new = mount(view, "new")
    assert view.audio_player is new
    assert view._playing_key == "loading:new"


def test_replaced_audio_events_cannot_stop_current_track(view):
    old = mount(view, "old")
    new = mount(view, "new")
    assert old.released
    assert old not in view.page.overlay
    old.on_loaded(SimpleNamespace(control=old))
    old.on_state_changed(SimpleNamespace(control=old, data="completed"))
    assert old.play_calls == 0
    new.on_loaded(SimpleNamespace(control=new))
    assert view._playing_key == "new"
    view._toggle_preview("new", lambda: None)
    assert view._paused_key == "new"
    assert new.pause_calls == 1
    view._toggle_preview("new", lambda: None)
    assert view._playing_key == "new"
