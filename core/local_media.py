"""Serve músicas já baixadas pro player do app via http://127.0.0.1.

O player do Flet toca URLs http de forma confiável (é como as prévias do
Beatport/Bandcamp já funcionam), mas caminho de arquivo local depende de
plataforma e de como o app foi empacotado. Então cada arquivo liberado ganha
um token aleatório e é servido só na interface de loopback — nada fora da
Biblioteca fica acessível, nem pra outra máquina da rede.
"""

import mimetypes
import os
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import quote

CHUNK = 256 * 1024
RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")

mimetypes.add_type("audio/flac", ".flac")
mimetypes.add_type("audio/wav", ".wav")
mimetypes.add_type("audio/aiff", ".aiff")
mimetypes.add_type("audio/mp4", ".m4a")


class LocalMediaServer:
    def __init__(self):
        self._files: dict[str, str] = {}
        self._tokens: dict[str, str] = {}
        self._lock = threading.Lock()
        self._server: Optional[ThreadingHTTPServer] = None

    def url_for(self, path: str) -> Optional[str]:
        """URL tocável pro arquivo, ou None se ele não existir mais."""
        if not path or not os.path.isfile(path):
            return None
        with self._lock:
            self._ensure_started()
            token = self._tokens.get(path)
            if token is None:
                token = secrets.token_urlsafe(16)
                self._tokens[path] = token
                self._files[token] = path
            port = self._server.server_address[1]
        return f"http://127.0.0.1:{port}/{token}/{quote(os.path.basename(path))}"

    def shutdown(self) -> None:
        with self._lock:
            if self._server is not None:
                self._server.shutdown()
                self._server.server_close()
                self._server = None

    def _ensure_started(self) -> None:
        if self._server is not None:
            return
        files = self._files

        class Handler(BaseHTTPRequestHandler):
            def do_HEAD(self):
                self._serve(send_body=False)

            def do_GET(self):
                self._serve(send_body=True)

            def _serve(self, send_body: bool):
                token = self.path.lstrip("/").split("/", 1)[0]
                path = files.get(token)
                if not path or not os.path.isfile(path):
                    self.send_error(404)
                    return
                size = os.path.getsize(path)
                start, end = 0, size - 1
                match = RANGE_RE.fullmatch(self.headers.get("Range", "").strip())
                if match and (match.group(1) or match.group(2)):
                    if match.group(1):
                        start = int(match.group(1))
                        end = int(match.group(2)) if match.group(2) else size - 1
                    else:  # "bytes=-N": os últimos N bytes
                        start = max(0, size - int(match.group(2)))
                    end = min(end, size - 1)
                    if start > end:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.end_headers()
                        return
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                else:
                    self.send_response(200)
                length = end - start + 1
                self.send_header("Content-Type",
                                 mimetypes.guess_type(path)[0] or "application/octet-stream")
                self.send_header("Content-Length", str(length))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                if not send_body:
                    return
                try:
                    with open(path, "rb") as f:
                        f.seek(start)
                        while length > 0:
                            chunk = f.read(min(CHUNK, length))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            length -= len(chunk)
                except (ConnectionError, OSError):
                    pass  # o player fechou a conexão (seek, troca de faixa)

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
