import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, Iterable, Tuple

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "codecity.db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS repositories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE,
                label TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                id TEXT PRIMARY KEY,
                repository_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                commit_hash TEXT,
                label TEXT,
                FOREIGN KEY (repository_id) REFERENCES repositories(id)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id TEXT NOT NULL,
                path TEXT NOT NULL,
                name TEXT NOT NULL,
                size INTEGER,
                complexity REAL,
                width REAL,
                depth REAL,
                height REAL,
                extension TEXT,
                is_test_file INTEGER,
                area REAL,
                aspect_ratio REAL,
                risk_score REAL,
                anomaly_score REAL,
                FOREIGN KEY (snapshot_id) REFERENCES snapshots(id)
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def _get_or_create_repository(conn: sqlite3.Connection, url: str, label: str) -> int:
    cur = conn.cursor()
    cur.execute("SELECT id FROM repositories WHERE url = ?", (url,))
    row = cur.fetchone()
    if row:
        return int(row["id"])

    created_at = datetime.utcnow().isoformat()
    cur.execute(
        "INSERT INTO repositories (url, label, created_at) VALUES (?, ?, ?)",
        (url, label, created_at),
    )
    conn.commit()
    return int(cur.lastrowid)


def upsert_snapshot_and_files(
    repo_url: str,
    snapshot_meta: Dict[str, Any],
    files: Iterable[Dict[str, Any]],
    feature_rows: Iterable[Dict[str, Any]],
) -> None:
    """
    Persist snapshot and per-file rows into SQLite.

    - repo_url: original GitHub URL.
    - snapshot_meta: metadata dict produced by the Flask app.
    - files: original city_data entries (must align index-wise with feature_rows).
    - feature_rows: engineered feature dicts for each file.
    """
    init_db()
    conn = get_connection()
    try:
        cur = conn.cursor()
        repo_label = snapshot_meta.get("label") or repo_url
        repo_id = _get_or_create_repository(conn, repo_url, repo_label)

        snapshot_id = str(snapshot_meta["id"])
        created_at = snapshot_meta.get("created_at") or datetime.utcnow().strftime(
            "%Y%m%d-%H%M%S"
        )
        commit_hash = snapshot_meta.get("commit_hash")
        label = snapshot_meta.get("label")

        cur.execute(
            """
            INSERT OR REPLACE INTO snapshots (id, repository_id, created_at, commit_hash, label)
            VALUES (?, ?, ?, ?, ?)
            """,
            (snapshot_id, repo_id, created_at, commit_hash, label),
        )

        cur.execute("DELETE FROM files WHERE snapshot_id = ?", (snapshot_id,))

        file_rows: Iterable[Tuple[Any, ...]] = []
        for file_rec, feats in zip(files, feature_rows):
            name = str(file_rec.get("name") or "")
            path = feats.get("path", name)
            risk = float(file_rec.get("risk_score", 0.0))
            anomaly = float(file_rec.get("anomaly_score", 0.0))
            row = (
                snapshot_id,
                path,
                name,
                int(feats.get("size", 0)),
                float(feats.get("complexity", 0.0)),
                float(feats.get("width", 0.0)),
                float(feats.get("depth", 0.0)),
                float(feats.get("height", 0.0)),
                feats.get("extension"),
                1 if feats.get("is_test_file") else 0,
                float(feats.get("area", 0.0)),
                float(feats.get("aspect_ratio", 0.0)),
                risk,
                anomaly,
            )
            file_rows.append(row)

        cur.executemany(
            """
            INSERT INTO files (
                snapshot_id,
                path,
                name,
                size,
                complexity,
                width,
                depth,
                height,
                extension,
                is_test_file,
                area,
                aspect_ratio,
                risk_score,
                anomaly_score
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            list(file_rows),
        )
        conn.commit()
    finally:
        conn.close()

