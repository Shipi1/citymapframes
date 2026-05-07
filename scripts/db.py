"""SQLite-backed cache for the geocoding + Overpass pipeline.

Replaces the per-script JSON files (geocode_cache.json, place_cache.json)
with one SQLite database. Concurrent-safe via WAL mode, gzipped JSON
storage for compactness, foreign keys enforced for referential integrity.

Schema:
  geocode_cache  - raw Nominatim search responses, keyed by query string
  anchor         - resolved place identity (osm_type, osm_id, bbox, ...)
  place_cache    - per (anchor, layer) Overpass JSON, gzipped

Public API used by geocode.py and fetch.py:
  set_db_path(path)              - override the default DB location
  init_db()                      - create schema if missing
  get_geocode(query)             - cached Nominatim response or None
  put_geocode(query, response)
  get_anchor(osm_type, osm_id)   - cached anchor row as dict, or None
  put_anchor(anchor)             - upsert from a geocode.Anchor instance
  get_layer_cache(osm_type, osm_id, layer_id, ttl=...)
  put_layer_cache(osm_type, osm_id, layer_id, elements)
  purge_stale(ttl_sec=...)       - DELETE expired place_cache rows
  stats()                        - counts + DB file size
"""

import argparse
import gzip
import json
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

import orjson

DEFAULT_DB_PATH = Path(__file__).parent / "cache.db"
PLACE_CACHE_TTL_SEC = 14 * 86400  # 14 days


SCHEMA = """
CREATE TABLE IF NOT EXISTS geocode_cache (
    query        TEXT PRIMARY KEY,
    response     BLOB NOT NULL,
    fetched_at   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS anchor (
    osm_type           TEXT NOT NULL,
    osm_id             INTEGER NOT NULL,
    name               TEXT,
    display_name       TEXT,
    level              TEXT,
    lat                REAL,
    lon                REAL,
    bbox_s             REAL,
    bbox_w             REAL,
    bbox_n             REAL,
    bbox_e             REAL,
    extent_km          REAL,
    bbox_synthesized   INTEGER,
    last_seen          INTEGER NOT NULL,
    PRIMARY KEY (osm_type, osm_id)
);

CREATE TABLE IF NOT EXISTS place_cache (
    osm_type       TEXT NOT NULL,
    osm_id         INTEGER NOT NULL,
    layer_id       TEXT NOT NULL,
    radius_km      INTEGER NOT NULL,
    data           BLOB NOT NULL,
    element_count  INTEGER NOT NULL,
    fetched_at     INTEGER NOT NULL,
    PRIMARY KEY (osm_type, osm_id, layer_id, radius_km),
    FOREIGN KEY (osm_type, osm_id) REFERENCES anchor(osm_type, osm_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_place_cache_age ON place_cache(fetched_at);
"""


# ---------- module state ----------

_db_path: Path = DEFAULT_DB_PATH
_initialized_paths: set[str] = set()


def set_db_path(path):
    """Override the DB file location. Resets the init flag so the new
    path's schema is checked on next access."""
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


def _ensure_init():
    """Run schema migrations once per (process, db_path)."""
    key = str(_db_path)
    if key in _initialized_paths:
        return
    with _txn() as conn:
        # Migration: place_cache gained `radius_km` (now part of the PK).
        # If the table exists without that column, drop it. Anchor and
        # geocode_cache rows survive — only per-layer data is rebuilt.
        cols = [r[1] for r in conn.execute("PRAGMA table_info(place_cache)").fetchall()]
        if cols and "radius_km" not in cols:
            conn.execute("DROP TABLE place_cache")
        conn.executescript(SCHEMA)
    _initialized_paths.add(key)


def init_db():
    """Public alias for initial schema creation."""
    _ensure_init()


# ---------- compression ----------
#
# orjson is 5–10× faster than stdlib json for both encode and decode.
# Output is byte-compatible JSON (just more compact — fewer separators),
# so old blobs written with stdlib json still load fine. No migration.

def _pack(obj: Any) -> bytes:
    # orjson.dumps already returns utf-8 bytes; no .encode() needed.
    return gzip.compress(orjson.dumps(obj))


def _unpack(blob: bytes) -> Any:
    # orjson.loads accepts bytes directly.
    return orjson.loads(gzip.decompress(blob))


# ---------- geocode_cache ----------

def get_geocode(query: str) -> Optional[list[dict]]:
    _ensure_init()
    with _txn() as conn:
        row = conn.execute(
            "SELECT response FROM geocode_cache WHERE query = ?", (query,)
        ).fetchone()
    return _unpack(row[0]) if row else None


def put_geocode(query: str, response: list[dict]) -> None:
    _ensure_init()
    with _txn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO geocode_cache (query, response, fetched_at)
               VALUES (?, ?, ?)""",
            (query, _pack(response), int(time.time())),
        )


# ---------- anchor ----------

def get_anchor(osm_type: str, osm_id: int) -> Optional[dict]:
    _ensure_init()
    with _txn() as conn:
        row = conn.execute(
            """SELECT osm_type, osm_id, name, display_name, level,
                      lat, lon, bbox_s, bbox_w, bbox_n, bbox_e,
                      extent_km, bbox_synthesized
               FROM anchor WHERE osm_type = ? AND osm_id = ?""",
            (osm_type, int(osm_id)),
        ).fetchone()
    if not row:
        return None
    return {
        "osm_type": row[0],
        "osm_id": row[1],
        "name": row[2],
        "display_name": row[3],
        "level": row[4],
        "lat": row[5],
        "lon": row[6],
        "bbox": (row[7], row[8], row[9], row[10]),
        "extent_km": row[11],
        "bbox_synthesized": bool(row[12]),
    }


def put_anchor(anchor) -> None:
    """Upsert from a geocode.Anchor dataclass instance.

    IMPORTANT: uses INSERT...ON CONFLICT DO UPDATE (real UPSERT), NOT
    INSERT OR REPLACE. The latter would DELETE+INSERT, triggering the
    place_cache foreign-key CASCADE and wiping every cached layer for
    this anchor.
    """
    _ensure_init()
    s, w, n, e = anchor.bbox
    with _txn() as conn:
        conn.execute(
            """INSERT INTO anchor
               (osm_type, osm_id, name, display_name, level,
                lat, lon, bbox_s, bbox_w, bbox_n, bbox_e,
                extent_km, bbox_synthesized, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(osm_type, osm_id) DO UPDATE SET
                 name = excluded.name,
                 display_name = excluded.display_name,
                 level = excluded.level,
                 lat = excluded.lat,
                 lon = excluded.lon,
                 bbox_s = excluded.bbox_s,
                 bbox_w = excluded.bbox_w,
                 bbox_n = excluded.bbox_n,
                 bbox_e = excluded.bbox_e,
                 extent_km = excluded.extent_km,
                 bbox_synthesized = excluded.bbox_synthesized,
                 last_seen = excluded.last_seen
            """,
            (
                anchor.osm_type, int(anchor.osm_id),
                anchor.name, anchor.display_name, anchor.level,
                anchor.lat, anchor.lon,
                s, w, n, e,
                anchor.extent_km,
                1 if anchor.bbox_synthesized else 0,
                int(time.time()),
            ),
        )


# ---------- place_cache ----------

def get_layer_cache(
    osm_type: str,
    osm_id: int,
    layer_id: str,
    radius_km: int,
    ttl_sec: int = PLACE_CACHE_TTL_SEC,
) -> Optional[list[dict]]:
    _ensure_init()
    cutoff = int(time.time()) - ttl_sec
    with _txn() as conn:
        row = conn.execute(
            """SELECT data FROM place_cache
               WHERE osm_type = ? AND osm_id = ? AND layer_id = ?
                 AND radius_km = ?
                 AND fetched_at > ?""",
            (osm_type, int(osm_id), layer_id, int(radius_km), cutoff),
        ).fetchone()
    return _unpack(row[0]) if row else None


def put_layer_cache(
    osm_type: str,
    osm_id: int,
    layer_id: str,
    radius_km: int,
    elements: list[dict],
) -> None:
    """Caller is responsible for having put_anchor()'d the anchor row first
    (FK requires it). geocode.geocode() does this, so any normal flow is fine.
    """
    _ensure_init()
    with _txn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO place_cache
               (osm_type, osm_id, layer_id, radius_km, data, element_count, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                osm_type, int(osm_id), layer_id, int(radius_km),
                _pack(elements), len(elements), int(time.time()),
            ),
        )


# ---------- housekeeping ----------

def purge_stale(ttl_sec: int = PLACE_CACHE_TTL_SEC) -> int:
    """Delete place_cache rows older than ttl. Returns number deleted."""
    _ensure_init()
    cutoff = int(time.time()) - ttl_sec
    with _txn() as conn:
        cur = conn.execute("DELETE FROM place_cache WHERE fetched_at < ?", (cutoff,))
        return cur.rowcount


def stats() -> dict:
    _ensure_init()
    with _txn() as conn:
        anchors = conn.execute("SELECT COUNT(*) FROM anchor").fetchone()[0]
        geocodes = conn.execute("SELECT COUNT(*) FROM geocode_cache").fetchone()[0]
        layer_rows = conn.execute("SELECT COUNT(*) FROM place_cache").fetchone()[0]
        elems = conn.execute(
            "SELECT COALESCE(SUM(element_count), 0) FROM place_cache"
        ).fetchone()[0]
    size = _db_path.stat().st_size if _db_path.exists() else 0
    return {
        "db_path": str(_db_path),
        "anchors": anchors,
        "geocoded_queries": geocodes,
        "cached_layers": layer_rows,
        "total_elements": elems,
        "db_size_bytes": size,
        "db_size_mb": round(size / (1024 * 1024), 2),
    }


def list_anchors() -> list[dict]:
    _ensure_init()
    with _txn() as conn:
        rows = conn.execute(
            """SELECT osm_type, osm_id, name, level, extent_km,
                      bbox_synthesized, last_seen
               FROM anchor ORDER BY last_seen DESC"""
        ).fetchall()
    return [
        {
            "osm_type": r[0],
            "osm_id": r[1],
            "name": r[2],
            "level": r[3],
            "extent_km": r[4],
            "bbox_synthesized": bool(r[5]),
            "last_seen": r[6],
        }
        for r in rows
    ]


# ---------- CLI ----------

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(
        prog="db.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="SQLite cache utility — inspect, init, or purge the cache DB.",
        epilog=(
            "Examples:\n"
            "  db.py init                      # create schema (idempotent)\n"
            "  db.py stats                     # row counts + file size\n"
            "  db.py anchors                   # list every cached place\n"
            "  db.py purge                     # drop stale place_cache rows\n"
            "  db.py --db custom.db stats      # use a non-default DB file\n"
        ),
    )
    ap.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        metavar="FILE",
        help="Path to the SQLite database (default %(default)s).",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="Create schema if missing.")
    sub.add_parser("stats", help="Show cache statistics.")
    sub.add_parser("purge", help="Delete place_cache rows older than TTL.")
    sub.add_parser("anchors", help="List cached anchors, newest first.")
    args = ap.parse_args()

    set_db_path(args.db)

    if args.cmd == "init":
        init_db()
        print(f"Initialized schema at {args.db}")
    elif args.cmd == "stats":
        print(json.dumps(stats(), indent=2, ensure_ascii=False))
    elif args.cmd == "purge":
        n = purge_stale()
        print(f"Deleted {n} stale row(s)")
    elif args.cmd == "anchors":
        rows = list_anchors()
        if not rows:
            print("(no anchors cached)")
            return
        for r in rows:
            flag = " *" if r["bbox_synthesized"] else "  "
            print(
                f"{r['osm_type']}/{r['osm_id']:<10}  "
                f"{r['level']:<14} {r['extent_km']:>6.1f}km{flag}  {r['name']}"
            )


if __name__ == "__main__":
    main()
