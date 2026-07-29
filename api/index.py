from __future__ import annotations

import os
import sys
from pathlib import Path

from flask import Flask, jsonify, request

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.beatport import search as search_beatport
from core.bandcamp import search as search_bandcamp

app = Flask(__name__)


HTML_PAGE = """
<!doctype html>
<html lang=\"pt-BR\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Music Aggregator</title>
  <style>
    :root { color-scheme: light dark; }
    body { font-family: Arial, sans-serif; margin: 0; background: #f5f7ff; color: #1f2340; }
    main { max-width: 980px; margin: 0 auto; padding: 32px 20px 60px; }
    h1 { margin-bottom: 6px; }
    .card { background: white; border-radius: 18px; padding: 20px; box-shadow: 0 12px 30px rgba(0,0,0,0.08); }
    form { display: flex; gap: 10px; margin-top: 18px; flex-wrap: wrap; }
    input { flex: 1; min-width: 220px; padding: 12px 14px; border: 1px solid #d7dcff; border-radius: 12px; font-size: 16px; }
    button { border: 0; background: #5046e5; color: white; padding: 12px 16px; border-radius: 12px; cursor: pointer; font-size: 15px; }
    .results { display: grid; gap: 14px; margin-top: 18px; }
    .result { border: 1px solid #ececff; border-radius: 14px; padding: 14px; background: #fcfbff; }
    .pill { display: inline-block; padding: 6px 10px; border-radius: 999px; background: #eef0ff; margin-right: 8px; font-size: 13px; }
    .muted { color: #6f7390; }
  </style>
</head>
<body>
  <main>
    <h1>Music Aggregator</h1>
    <p class=\"muted\">Busque uma música e veja resultados do Beatport e Bandcamp em tempo real.</p>
    <div class=\"card\">
      <form id=\"searchForm\">
        <input id=\"query\" name=\"q\" placeholder=\"Ex.: Blue Monday\" required />
        <button type=\"submit\">Buscar</button>
      </form>
      <div id=\"status\" class=\"muted\" style=\"margin-top: 10px;\"></div>
      <div id=\"results\" class=\"results\"></div>
    </div>
  </main>
  <script>
    const form = document.getElementById('searchForm');
    const query = document.getElementById('query');
    const status = document.getElementById('status');
    const results = document.getElementById('results');

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const value = query.value.trim();
      if (!value) return;
      status.textContent = 'Buscando...';
      results.innerHTML = '';
      const response = await fetch(`/api/search?q=${encodeURIComponent(value)}`);
      const data = await response.json();
      if (data.error) {
        status.textContent = data.error;
        return;
      }
      status.textContent = `Mostrando ${data.beatport.length} resultado(s) no Beatport e ${data.bandcamp.length} no Bandcamp.`;
      const items = [];
      data.beatport.forEach((item) => items.push(`<div class=\"result\"><strong>${item.title}</strong><div class=\"muted\">${item.artist}</div><div style=\"margin-top:8px\"><span class=\"pill\">Beatport</span><span>${item.price}</span></div><div style=\"margin-top:6px\"><a href=\"${item.url}\" target=\"_blank\">Abrir</a></div></div>`));
      data.bandcamp.forEach((item) => items.push(`<div class=\"result\"><strong>${item.title}</strong><div class=\"muted\">${item.artist}</div><div style=\"margin-top:8px\"><span class=\"pill\">Bandcamp</span><span>${item.price}</span></div><div style=\"margin-top:6px\"><a href=\"${item.url}\" target=\"_blank\">Abrir</a></div></div>`));
      results.innerHTML = items.join('');
    });
  </script>
</body>
</html>
"""


def fetch_sources(query: str):
    beatport_results = search_beatport(query, limit=4)
    bandcamp_results = search_bandcamp(query, limit=4)

    beatport_payload = [
        {
            "title": item.full_title,
            "artist": item.artist_label,
            "price": item.price_display or "—",
            "url": item.url,
            "cover": item.cover_url,
            "label": item.label,
            "bpm": item.bpm,
            "key": item.key,
        }
        for item in beatport_results
    ]

    bandcamp_payload = [
        {
            "title": item.title,
            "artist": item.artist,
            "price": item.price_label,
            "url": f"https://bandcamp.com{item.url}" if not item.url.startswith("http") else item.url,
            "cover": item.cover_url,
            "type": item.item_type,
        }
        for item in bandcamp_results
    ]

    return beatport_payload, bandcamp_payload


@app.get("/")
def home():
    return HTML_PAGE


@app.get("/api/search")
def search():
    query = (request.args.get("q") or "").strip()
    if not query:
        return jsonify({"error": "Informe uma query"}), 400

    beatport, bandcamp = fetch_sources(query)
    return jsonify({
        "query": query,
        "beatport": beatport,
        "bandcamp": bandcamp,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)
