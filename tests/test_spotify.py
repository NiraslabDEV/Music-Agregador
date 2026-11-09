import json
from types import SimpleNamespace

import pytest

from core import spotify
from core.soulseek import DownloadHistory
from ui import aggregator_view as module


def _embed(entity) -> str:
    data = {"props": {"pageProps": {"state": {"data": {"entity": entity}}}}}
    return ('<html><script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(data) + "</script></html>")


TRACK_ID = "4uLU6hMCjMI75M1A2tKUQC"
PLAYLIST = {
    "type": "playlist", "name": "Warm up", "trackList": [
        {"uri": "spotify:track:3h5T5JypYU7huFiVYhv1dr", "title": "BbY WOW",
         "subtitle": "KAROL G,\xa0Judeline,\xa0rusowsky", "duration": 225834,
         "audioPreview": {"url": "https://p.scdn.co/mp3-preview/x"}, "entityType": "track"},
        {"uri": "spotify:episode:0000000000000000000000", "title": "Podcast",
         "subtitle": "Show", "entityType": "episode"},
        {"uri": "spotify:track:2sFepQNyl1WjpROruP8bJ0", "title": "Housewife",
         "subtitle": "Tate McRae", "entityType": "track"},
    ],
}


@pytest.mark.parametrize("text, expected", [
    (f"https://open.spotify.com/track/{TRACK_ID}?si=abc123", [("track", TRACK_ID)]),
    (f"open.spotify.com/intl-pt/album/{TRACK_ID}", [("album", TRACK_ID)]),
    (f"https://open.spotify.com/embed/playlist/{TRACK_ID}", [("playlist", TRACK_ID)]),
    (f"spotify:track:{TRACK_ID}", [("track", TRACK_ID)]),
    (f"spotify:track:{TRACK_ID}, spotify:track:{TRACK_ID}", [("track", TRACK_ID)]),
    ("https://open.spotify.com/artist/" + TRACK_ID, []),   # artista não é faixa
    ("New Order - Blue Monday", []),
])
def test_find_links(text, expected):
    assert spotify.find_links(text) == expected


def test_strip_links_keeps_other_tracks():
    text = f"New Order - Blue Monday\nhttps://open.spotify.com/track/{TRACK_ID}?si=x\nTime"
    assert spotify.strip_links(text).split() == ["New", "Order", "-", "Blue", "Monday", "Time"]


def test_parse_track_embed():
    item = spotify.parse_embed("track", TRACK_ID, _embed({
        "type": "track", "id": TRACK_ID, "name": "Never Gonna Give You Up",
        "artists": [{"name": "Rick Astley"}], "duration": 213573,
        "audioPreview": {"url": "https://p.scdn.co/mp3-preview/abc"}}))
    [track] = item.tracks
    assert track.query == "Rick Astley - Never Gonna Give You Up"
    assert track.preview_url.endswith("/abc")
    assert track.url == f"https://open.spotify.com/track/{TRACK_ID}"


def test_parse_playlist_embed_skips_episodes_and_cleans_artists():
    item = spotify.parse_embed("playlist", "p", _embed(PLAYLIST))
    assert item.name == "Warm up"
    assert [t.query for t in item.tracks] == [
        "KAROL G, Judeline, rusowsky - BbY WOW", "Tate McRae - Housewife"]


def test_parse_embed_error_page():
    html = ('<script id="__NEXT_DATA__" type="application/json">'
            '{"props": {"pageProps": {"status": 404}}}</script>')
    assert spotify.parse_embed("track", TRACK_ID, html) is None
    assert spotify.parse_embed("track", TRACK_ID, "sem json") is None
    assert spotify.fetch("artist", TRACK_ID) is None


# ============================== na tela ==============================

class SyncThread:
    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


@pytest.fixture
def view(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "_data_dir", lambda: tmp_path)
    monkeypatch.setattr(module.threading, "Thread", SyncThread)
    v = module.AggregatorView(SimpleNamespace(overlay=[], update=lambda: None))
    v.engine.history = DownloadHistory(tmp_path / "history.db")
    v.control.content = v._main_layout
    yield v
    v.shutdown()


def test_spotify_link_becomes_searches(view, monkeypatch):
    searched = []
    monkeypatch.setattr(module.spotify, "fetch",
                        lambda kind, item_id: spotify.parse_embed(kind, item_id, _embed(PLAYLIST)))
    monkeypatch.setattr(view.aggregator, "search_many",
                        lambda queries, **kw: searched.extend(queries) or {})
    view.query_field.value = (f"https://open.spotify.com/playlist/{TRACK_ID}?si=1\n"
                              "New Order - Blue Monday")
    view._start_search()
    rest = module.parse_tracklist_to_queries("New Order - Blue Monday")  # parser de sempre
    assert searched == ["KAROL G, Judeline, rusowsky - BbY WOW", "Tate McRae - Housewife", *rest]
    assert view.searching["value"] is False and not view.search_btn.disabled
    assert len(view._spotify_buttons("Tate McRae - Housewife")) == 1      # sem prévia: só o link
    assert len(view._spotify_buttons("KAROL G, Judeline, rusowsky - BbY WOW")) == 2
    assert view._spotify_buttons("New Order - Blue Monday") == []
    card = view._track_card("Tate McRae - Housewife",
                            module.TrackResult(query="Tate McRae - Housewife", done=True))
    assert card is not None


def test_unreadable_spotify_link_warns(view, monkeypatch):
    monkeypatch.setattr(module.spotify, "fetch", lambda kind, item_id: None)
    monkeypatch.setattr(view.aggregator, "search_many",
                        lambda *a, **k: pytest.fail("não devia buscar"))
    view.query_field.value = f"https://open.spotify.com/track/{TRACK_ID}"
    view._start_search()
    assert "Spotify" in view.page.snack_bar.content.value
    assert view.searching["value"] is False
