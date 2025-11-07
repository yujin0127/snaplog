import sqlite3
from typing import Iterable, Optional


class Database:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS photos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    original_filename TEXT NOT NULL,
                    stored_path TEXT NOT NULL,
                    captured_at TEXT,
                    latitude REAL,
                    longitude REAL,
                    created_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            conn.commit()

    def insert_photo(
        self,
        *,
        original_filename: str,
        stored_path: str,
        captured_at_iso: Optional[str],
        latitude: Optional[float],
        longitude: Optional[float],
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO photos (original_filename, stored_path, captured_at, latitude, longitude)
                VALUES (?, ?, ?, ?, ?)
                """,
                (original_filename, stored_path, captured_at_iso, latitude, longitude),
            )
            conn.commit()
            return cur.lastrowid

    def query_photos_by_date(self, start_iso: Optional[str], end_iso: Optional[str]) -> Iterable[sqlite3.Row]:
        sql = "SELECT * FROM photos"
        params = []  # type: ignore[var-annotated]

        if start_iso and end_iso:
            sql += " WHERE (captured_at IS NOT NULL AND captured_at >= ? AND captured_at <= ?)"
            params.extend([start_iso, end_iso])
        elif start_iso:
            sql += " WHERE (captured_at IS NOT NULL AND captured_at >= ?)"
            params.append(start_iso)
        elif end_iso:
            sql += " WHERE (captured_at IS NOT NULL AND captured_at <= ?)"
            params.append(end_iso)

        sql += " ORDER BY captured_at ASC NULLS LAST, id ASC"

        with self._connect() as conn:
            cur = conn.execute(sql, params)
            for row in cur.fetchall():
                yield row


