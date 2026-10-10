from types import SimpleNamespace

from core import producer, beatport, bandcamp
from core.soulseek import Candidate


def test_credit_requires_complete_name():
    assert producer.credited_to("Luigi Tozzi & Claudio PRC", "Claudio PRC")
    assert producer.credited_to("Cláudio PRC", "claudio prc")
    assert not producer.credited_to("Solomun", "Sol")
    assert not producer.credited_to("DJ Sol Tribute", "DJ Sol")


def test_p2p_rejects_folder_mentions_and_wrong_remixes():
    assert producer.file_matches(Candidate("user", "Claudio PRC - Nadir.flac"), "Claudio PRC", "Nadir")
    assert not producer.file_matches(Candidate("user", "Claudio PRC set/Other - Nadir.flac"), "Claudio PRC", "Nadir")
    assert not producer.file_matches(Candidate("user", "Claudio PRC - Nadir (Other Remix).flac"), "Claudio PRC", "Nadir")


def test_catalog_omits_mentions_and_keeps_collaborations(monkeypatch):
    monkeypatch.setattr(beatport, "search", lambda *a, **k: [
        beatport.BeatportTrack(1, "One", artists=["DJ Sol"], artist_ids=[3]),
        beatport.BeatportTrack(2, "DJ Sol Tribute", artists=["Other"], artist_ids=[4]),
    ])
    monkeypatch.setattr(bandcamp, "search", lambda *a, **k: [
        bandcamp.BandcampTrack("Two", "Other & DJ Sol", "https://example.com/two"),
        bandcamp.BandcampTrack("DJ Sol Mix", "Other", "https://example.com/wrong"),
    ])
    result = producer.search_producer("DJ Sol", SimpleNamespace(is_connected=False))
    assert set(result.tracks) == {"DJ Sol - One", "DJ Sol - Two"}
    assert all(t.done for t in result.tracks.values())


def test_homonyms_are_not_combined(monkeypatch):
    monkeypatch.setattr(beatport, "search", lambda *a, **k: [
        beatport.BeatportTrack(1, "One", artists=["Sol"], artist_ids=[3]),
        beatport.BeatportTrack(2, "Two", artists=["Sol"], artist_ids=[4]),
    ])
    monkeypatch.setattr(bandcamp, "search", lambda *a, **k: [])
    result = producer.search_producer("Sol", SimpleNamespace(is_connected=False))
    assert not result.tracks
    assert any("homônimos" in warning for warning in result.warnings)


def test_album_expansion_uses_track_credit(monkeypatch):
    import json
    import html
    payload = {"artist": "DJ Sol", "trackinfo": [
        {"title": "One", "title_link": "/track/one"},
        {"title": "Other", "artist": "Someone Else", "title_link": "/track/other"},
    ]}
    monkeypatch.setattr(bandcamp, "_curl_get", lambda *a: 'data-tralbum="' + html.escape(json.dumps(payload), quote=True) + '"')
    album = bandcamp.BandcampTrack("Album", "DJ Sol", "https://sol.bandcamp.com/album/test", item_type="album")
    tracks = producer.album_tracks(album, "DJ Sol")
    assert [t.title for t in tracks] == ["One"]
    assert tracks[0].url == "https://sol.bandcamp.com/track/one"


def test_store_failure_does_not_discard_other_source(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("offline")
    monkeypatch.setattr(beatport, "search", fail)
    monkeypatch.setattr(bandcamp, "search", lambda *a, **k: [bandcamp.BandcampTrack("One", "Sol", "https://example.com")])
    result = producer.search_producer("Sol", SimpleNamespace(is_connected=False))
    assert "Sol - One" in result.tracks
    assert "Beatport indisponível nesta busca." in result.warnings
