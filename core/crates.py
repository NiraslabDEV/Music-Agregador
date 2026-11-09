"""Análises de BPM/tom (cache), crates e favoritas, num SQLite na pasta de dados do app.

Crates guardam caminho + nome da faixa (não o id do histórico): tirar uma
música da lista da Biblioteca não deve desmontar um set já montado.

Favoritas são faixas que a pessoa quer ter, baixadas ou não — guardam a busca
(texto) e o resultado da última verificação, pra ela voltar depois e ver se a
faixa já apareceu no Soulseek.
"""

import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from core.soulseek import _data_dir


class LibraryStore:
    def __init__(self, db_path: Optional[Path] = None):
        self.path = db_path or (_data_dir() / "library.db")
        self._lock = threading.Lock()
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS analysis (
                    path        TEXT PRIMARY KEY,
                    mtime       REAL,
                    size        INTEGER,
                    bpm         REAL,
                    camelot     TEXT,
                    source      TEXT,
                    error       TEXT,
                    analyzed_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS crates (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    name        TEXT NOT NULL,
                    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS crate_tracks (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    crate_id    INTEGER NOT NULL REFERENCES crates(id) ON DELETE CASCADE,
                    path        TEXT NOT NULL,
                    label       TEXT,
                    position    INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_crate_tracks ON crate_tracks(crate_id, position);
                CREATE TABLE IF NOT EXISTS favorites (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    query          TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    cover_url      TEXT DEFAULT '',
                    beatport_url   TEXT DEFAULT '',
                    bandcamp_url   TEXT DEFAULT '',
                    price          TEXT DEFAULT '',
                    added_at       TEXT DEFAULT CURRENT_TIMESTAMP,
                    checked_at     TEXT,
                    soulseek_count INTEGER,
                    soulseek_best  TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS prefs (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                );
                """
            )

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # ---------------- análise ----------------

    @staticmethod
    def _stat(path: str) -> Optional[tuple[float, int]]:
        try:
            st = os.stat(path)
            return st.st_mtime, st.st_size
        except OSError:
            return None

    def needs_analysis(self, path: str) -> bool:
        stat = self._stat(path)
        if stat is None:
            return False
        row = self.analysis_for(path)
        if row is None:
            return True
        if row["source"] == "manual":
            return False  # correção feita à mão sempre vale
        return (row["mtime"], row["size"]) != stat

    def analysis_for(self, path: str) -> Optional[dict]:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM analysis WHERE path = ?", (path,)).fetchone()
            return dict(row) if row else None

    def all_analysis(self) -> dict[str, dict]:
        with self._lock, self._conn() as conn:
            return {r["path"]: dict(r) for r in conn.execute("SELECT * FROM analysis")}

    def save_analysis(self, path: str, bpm: Optional[float], camelot: Optional[str],
                      source: str, error: str = "") -> None:
        mtime, size = self._stat(path) or (0.0, 0)
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO analysis (path, mtime, size, bpm, camelot, source, error)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (path, mtime, size, bpm, camelot, source, error),
            )

    # ---------------- preferências ----------------

    def get_pref(self, key: str) -> Optional[str]:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT value FROM prefs WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else None

    def set_pref(self, key: str, value: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO prefs (key, value) VALUES (?, ?)", (key, value))

    # ---------------- crates ----------------

    def crates(self) -> list[dict]:
        with self._lock, self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT c.id, c.name, COUNT(t.id) AS count FROM crates c"
                " LEFT JOIN crate_tracks t ON t.crate_id = c.id"
                " GROUP BY c.id ORDER BY c.id DESC")]

    def create_crate(self, name: str) -> int:
        with self._lock, self._conn() as conn:
            return conn.execute("INSERT INTO crates (name) VALUES (?)", (name.strip(),)).lastrowid

    def rename_crate(self, crate_id: int, name: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("UPDATE crates SET name = ? WHERE id = ?", (name.strip(), crate_id))

    def delete_crate(self, crate_id: int) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM crates WHERE id = ?", (crate_id,))

    def crate_tracks(self, crate_id: int) -> list[dict]:
        with self._lock, self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM crate_tracks WHERE crate_id = ? ORDER BY position, id", (crate_id,))]

    def add_to_crate(self, crate_id: int, path: str, label: str) -> bool:
        """False se a faixa já estava no crate."""
        with self._lock, self._conn() as conn:
            if conn.execute("SELECT 1 FROM crate_tracks WHERE crate_id = ? AND path = ?",
                            (crate_id, path)).fetchone():
                return False
            pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM crate_tracks"
                               " WHERE crate_id = ?", (crate_id,)).fetchone()[0]
            conn.execute("INSERT INTO crate_tracks (crate_id, path, label, position)"
                         " VALUES (?, ?, ?, ?)", (crate_id, path, label, pos))
            return True

    def remove_from_crate(self, track_id: int) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM crate_tracks WHERE id = ?", (track_id,))

    def set_order(self, crate_id: int, track_ids: list[int]) -> None:
        with self._lock, self._conn() as conn:
            conn.executemany("UPDATE crate_tracks SET position = ? WHERE id = ? AND crate_id = ?",
                             [(i, tid, crate_id) for i, tid in enumerate(track_ids)])

    def move(self, crate_id: int, track_id: int, delta: int) -> None:
        ids = [t["id"] for t in self.crate_tracks(crate_id)]
        if track_id not in ids:
            return
        i = ids.index(track_id)
        j = max(0, min(len(ids) - 1, i + delta))
        ids.insert(j, ids.pop(i))
        self.set_order(crate_id, ids)

    # ---------------- favoritas ----------------

    def favorites(self) -> list[dict]:
        with self._lock, self._conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM favorites ORDER BY id DESC")]

    def favorite(self, query: str) -> Optional[dict]:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM favorites WHERE query = ?",
                               (query.strip(),)).fetchone()
            return dict(row) if row else None

    def add_favorite(self, query: str, **info) -> int:
        """Cria (ou devolve, se já existe) a favorita. `info` só preenche
        campos vazios — favoritar de novo não apaga a última verificação."""
        query = " ".join((query or "").split())
        if not query:
            raise ValueError("busca vazia")
        with self._lock, self._conn() as conn:
            conn.execute("INSERT OR IGNORE INTO favorites (query) VALUES (?)", (query,))
            fav_id = conn.execute("SELECT id FROM favorites WHERE query = ?",
                                  (query,)).fetchone()[0]
            self._fill_store_info(conn, fav_id, info)
            return fav_id

    def remove_favorite(self, fav_id: int) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM favorites WHERE id = ?", (fav_id,))

    def record_check(self, fav_id: int, soulseek_count: int, soulseek_best: str = "",
                     **info) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("UPDATE favorites SET checked_at = CURRENT_TIMESTAMP,"
                         " soulseek_count = ?, soulseek_best = ? WHERE id = ?",
                         (soulseek_count, soulseek_best or "", fav_id))
            self._fill_store_info(conn, fav_id, info, overwrite=True)

    @staticmethod
    def _fill_store_info(conn, fav_id: int, info: dict, overwrite: bool = False) -> None:
        for column in ("cover_url", "beatport_url", "bandcamp_url", "price"):
            value = (info.get(column) or "").strip()
            if not value:
                continue  # não apaga um link bom porque a loja não respondeu agora
            where = "" if overwrite else " AND COALESCE(" + column + ", '') = ''"
            conn.execute(f"UPDATE favorites SET {column} = ? WHERE id = ?{where}",
                         (value, fav_id))


def favorite_status(fav: dict) -> str:
    """'never' (nunca verificada), 'missing' (não estava no Soulseek) ou 'available'."""
    if not fav.get("checked_at") or fav.get("soulseek_count") is None:
        return "never"
    return "available" if fav["soulseek_count"] > 0 else "missing"
