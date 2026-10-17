import urllib.error
import urllib.request

import pytest
from core.local_media import LocalMediaServer


@pytest.fixture
def server():
    s = LocalMediaServer()
    yield s
    s.shutdown()


def test_serves_file_with_ranges(server, tmp_path):
    song = tmp_path / "faixa com espaço.flac"
    song.write_bytes(bytes(range(100)))
    url = server.url_for(str(song))
    assert url.startswith("http://127.0.0.1:")

    with urllib.request.urlopen(url) as r:
        assert r.status == 200
        assert r.headers["Content-Type"] == "audio/flac"
        assert r.read() == bytes(range(100))

    req = urllib.request.Request(url, headers={"Range": "bytes=10-19"})
    with urllib.request.urlopen(req) as r:
        assert r.status == 206
        assert r.headers["Content-Range"] == "bytes 10-19/100"
        assert r.read() == bytes(range(10, 20))

    req = urllib.request.Request(url, headers={"Range": "bytes=-5"})
    with urllib.request.urlopen(req) as r:
        assert r.read() == bytes(range(95, 100))


def test_only_registered_files_are_served(server, tmp_path):
    song = tmp_path / "a.mp3"
    song.write_bytes(b"x")
    url = server.url_for(str(song))
    assert server.url_for(str(song)) == url  # mesmo token pro mesmo arquivo
    base = url.rsplit("/", 2)[0]
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(f"{base}/token-inventado/a.mp3")
    assert err.value.code == 404


def test_missing_file_has_no_url(server, tmp_path):
    assert server.url_for(str(tmp_path / "sumiu.wav")) is None
    assert server.url_for("") is None
