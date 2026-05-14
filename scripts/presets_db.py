"""SQLite-backed sharable design store.

Separate from `cache.db` because presets are durable user content
(`rm cache.db` shouldn't take user shares with it). Same WAL mode +
foreign-keys + gzipped orjson packing as `db.py`.

Identity model: anonymous and immutable. Posters get an 8-char base62
id back; nobody can edit or delete a preset in v1. A new edit = a new
share (with the previous share recorded as `parent_id`).

Public API:
  set_db_path(path)
  init_db()
  create_preset(name, design, parent_id=None, creator_ip=None) -> dict
  get_preset(preset_id, increment_views=True) -> dict | None
  stats() -> dict
"""

import gzip
import hashlib
import os
import secrets
import sqlite3
import string
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

import orjson

# Allow the Docker volume path (or any custom location) to be set via env.
# Falls back to the script directory so local dev still works unchanged.
DEFAULT_DB_PATH = Path(
    os.environ.get("CITYMAPFRAMES_PRESETS_DB", str(Path(__file__).parent / "presets.db"))
)

# 8 chars × 62 alphabet ≈ 2 × 10¹⁴ ids. Collision-resistant for any
# foreseeable scale; the INSERT has retry-on-collision anyway.
ID_LENGTH = 8
ID_ALPHABET = string.ascii_letters + string.digits
ID_INSERT_RETRIES = 5

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS preset (
    id              TEXT PRIMARY KEY,
    schema_ver      INTEGER NOT NULL,
    name            TEXT NOT NULL,
    design          BLOB NOT NULL,
    content_hash    TEXT NOT NULL,
    parent_id       TEXT,
    creator_ip_hash TEXT,
    view_count      INTEGER NOT NULL DEFAULT 0,
    created_at      INTEGER NOT NULL,
    FOREIGN KEY (parent_id) REFERENCES preset(id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_preset_content_hash
    ON preset(content_hash);
CREATE INDEX IF NOT EXISTS idx_preset_created
    ON preset(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_preset_parent
    ON preset(parent_id);
"""


# ---------- module state ----------

_db_path: Path = DEFAULT_DB_PATH
_initialized_paths: set[str] = set()


def set_db_path(path) -> None:
    """Override the default DB location. Resets the init flag for the
    new path so its schema is checked on next access."""
    global _db_path
    _db_path = Path(path)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path))
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def _txn():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_init() -> None:
    key = str(_db_path)
    if key in _initialized_paths:
        return
    with _txn() as conn:
        conn.executescript(SCHEMA)
    _initialized_paths.add(key)


def init_db() -> None:
    """Public alias for initial schema creation."""
    _ensure_init()


# ---------- helpers ----------

def _generate_id() -> str:
    return "".join(secrets.choice(ID_ALPHABET) for _ in range(ID_LENGTH))


def _hash_content(design_bytes: bytes) -> str:
    return hashlib.sha256(design_bytes).hexdigest()


def _hash_ip(ip: Optional[str]) -> Optional[str]:
    if not ip:
        return None
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()


# ---------- CRUD ----------

def create_preset(
    name: str,
    design: dict,
    parent_id: Optional[str] = None,
    creator_ip: Optional[str] = None,
) -> dict:
    """Persist a SharedDesign. Returns metadata as a dict.

    Dedup: if `sha256(design)` matches an existing preset, returns that
    one (with `deduped=True`) instead of inserting a duplicate.

    `parent_id` is silently dropped if it doesn't reference an existing
    preset — the share still succeeds, just without lineage.
    """
    _ensure_init()
    design_json = orjson.dumps(design)
    content_hash = _hash_content(design_json)
    creator_hash = _hash_ip(creator_ip)
    now = int(time.time())

    with _txn() as conn:
        # Dedup: same content already shared once.
        existing = conn.execute(
            "SELECT id, name, parent_id, created_at FROM preset "
            "WHERE content_hash = ?",
            (content_hash,),
        ).fetchone()
        if existing:
            return {
                "id": existing[0],
                "name": existing[1],
                "parent_id": existing[2],
                "created_at": existing[3],
                "deduped": True,
            }

        # Validate parent_id refers to a real preset; drop if not.
        if parent_id is not None:
            row = conn.execute(
                "SELECT 1 FROM preset WHERE id = ?", (parent_id,)
            ).fetchone()
            if not row:
                parent_id = None

        # Generate id; retry on the (extremely rare) collision.
        last_err: Optional[Exception] = None
        for _ in range(ID_INSERT_RETRIES):
            new_id = _generate_id()
            try:
                conn.execute(
                    """INSERT INTO preset
                       (id, schema_ver, name, design, content_hash,
                        parent_id, creator_ip_hash, view_count, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)""",
                    (
                        new_id,
                        SCHEMA_VERSION,
                        name,
                        gzip.compress(design_json),
                        content_hash,
                        parent_id,
                        creator_hash,
                        now,
                    ),
                )
                break
            except sqlite3.IntegrityError as e:
                # PK collision (id) or unique on content_hash — the
                # latter shouldn't happen since we checked above, but
                # race-safe anyway.
                last_err = e
                continue
        else:
            raise RuntimeError(
                "failed to generate unique preset id"
            ) from last_err

        return {
            "id": new_id,
            "name": name,
            "parent_id": parent_id,
            "created_at": now,
            "deduped": False,
        }


def get_preset(
    preset_id: str, increment_views: bool = True
) -> Optional[dict]:
    """Fetch a preset by id, parsing its design JSON. Returns None if
    not found. Bumps view_count by 1 unless `increment_views=False`."""
    _ensure_init()
    with _txn() as conn:
        row = conn.execute(
            """SELECT id, name, design, parent_id, view_count, created_at
               FROM preset WHERE id = ?""",
            (preset_id,),
        ).fetchone()
        if not row:
            return None
        if increment_views:
            conn.execute(
                "UPDATE preset SET view_count = view_count + 1 "
                "WHERE id = ?",
                (preset_id,),
            )
    return {
        "id": row[0],
        "name": row[1],
        "design": orjson.loads(gzip.decompress(row[2])),
        "parent_id": row[3],
        "view_count": row[4] + (1 if increment_views else 0),
        "created_at": row[5],
    }


def list_recent(limit: int = 20, offset: int = 0) -> list[dict]:
    """Return the N most recently created presets, newest first.
    Decompresses each `design` blob; the per-row size is small (~1-2 KB
    JSON) so this is cheap up to a few hundred items.

    Used by the gallery — every share is publicly listed in v1.
    """
    _ensure_init()
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    with _txn() as conn:
        rows = conn.execute(
            """SELECT id, name, design, parent_id, view_count, created_at
               FROM preset ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
    return [
        {
            "id": r[0],
            "name": r[1],
            "design": orjson.loads(gzip.decompress(r[2])),
            "parent_id": r[3],
            "view_count": r[4],
            "created_at": r[5],
        }
        for r in rows
    ]


# ---------- housekeeping ----------

def stats() -> dict:
    _ensure_init()
    with _txn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM preset").fetchone()[0]
        views = conn.execute(
            "SELECT COALESCE(SUM(view_count), 0) FROM preset"
        ).fetchone()[0]
    size = _db_path.stat().st_size if _db_path.exists() else 0
    return {
        "db_path": str(_db_path),
        "presets": n,
        "total_views": views,
        "db_size_bytes": size,
    }
