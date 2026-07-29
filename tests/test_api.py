import importlib

import pytest


@pytest.fixture
def client(monkeypatch):
    import api.index as api_module

    def fake_fetch_sources(query):
        return [
            {"title": "Test Track", "artist": "Test Artist", "price": "$1.99", "url": "https://example.com/beatport"},
        ], [
            {"title": "Test Track", "artist": "Test Artist", "price": "Grátis", "url": "https://example.com/bandcamp"},
        ]

    monkeypatch.setattr(api_module, "fetch_sources", fake_fetch_sources)
    api_module.app.config.update(TESTING=True)
    with api_module.app.test_client() as client:
        yield client


def test_search_requires_query(client):
    response = client.get("/api/search")
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["error"] == "Informe uma query"


def test_search_returns_payload(client):
    response = client.get("/api/search?q=Test%20Track")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["query"] == "Test Track"
    assert payload["beatport"][0]["title"] == "Test Track"
    assert payload["bandcamp"][0]["price"] == "Grátis"
