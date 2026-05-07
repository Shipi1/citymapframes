# CityMapFrames — Manual

A small toolchain for fetching OpenStreetMap data for a place and
rendering it to PNG or SVG with consistent styling. Built around three
core commands plus a SQLite cache and an HTTP API:

```
geocode.py   →   fetch.py   →   render.js
   place             OSM data         image
   anchor            per layer

         shared SQLite cache: db.py / cache.db
         HTTP wrapper:        server.py
```

All commands read a single registry file ([layers.json](layers.json)) that
defines every map layer — what to fetch, how to identify it, and how to
draw it.

---

## Pipeline at a glance

```
"Cerro Alegre, Valparaíso"
        │
        ▼
┌────────────────────┐
│   geocode.py       │◄──────┐
│ Nominatim lookup   │       │
└──────────┬─────────┘       │   ┌──────────────┐
           ▼                 ├───┤  cache.db    │
   Anchor(osm_id, bbox)      │   │  (SQLite)    │
           │                 │   │ • geocodes   │
           ▼                 │   │ • anchors    │
┌────────────────────┐       │   │ • per-layer  │
│   fetch.py         │◄──────┤   └──────────────┘
│ Overpass + cache   │       │
└──────────┬─────────┘       │
           ▼                 │       ┌──────────────┐
   place_data.json           │       │  server.py   │
   { anchor, layers: ... }   │       │  (FastAPI)   │
           │                 │       │  /api/place  │◄── HTTP
           ▼                 │       │  /api/data   │
┌────────────────────┐       │       │  /api/layers │
│   render.js        │◄──────┘       └──────────────┘
│ canvas/SVG         │  layers.json
└──────────┬─────────┘
           ▼
       roads.png / roads.svg
```

---

## Prerequisites

- **Python 3.14+** managed via [uv](https://github.com/astral-sh/uv).
  Python deps: `requests`, `fastapi`, `uvicorn[standard]`, `orjson`.
- **Node.js** (18+ recommended) — `canvas` for `render.js` (server-side
  PNG/SVG); the web frontend in `../web/` uses Svelte + Vite (no extra
  global tooling, just `npm install` in that folder).
- An internet connection — the tool calls public Nominatim and Overpass
  servers. Both have rate limits; respect them.

### One-time setup

From the `scripts/` folder:

```bash
uv sync                     # installs Python deps from pyproject.toml
npm install                 # installs the `canvas` package for render.js
uv run python db.py init    # creates cache.db with the schema
```

Optional, for the browser frontend (`../web/`):

```bash
cd ../web
npm install
```

---

## Quick start

```bash
# 1) Resolve a place to a stable anchor (osm_id + bbox)
uv run python geocode.py "Lisbon"

# 2) Fetch every layer in the registry for that place
uv run python fetch.py "Lisbon"
# → writes place_data.json

# 3) Render
node render.js
# → writes roads.png
```

Run the same `fetch.py "Lisbon"` again — second run is instant (cache
hit on every layer). Sub-place inputs share the cache:

```bash
uv run python fetch.py "Alfama, Lisbon"     # hits the same cache cells
```

---

## Commands

### geocode.py — place → anchor

Resolves a free-text place query to a stable parent anchor (a specific
OSM relation/way/node) plus a bbox suitable for Overpass.

```
geocode.py [-h] [--max-extent KM] [--radius KM] [--db FILE] query
```

**`query`** — quoted string. May include parents:
`"Cerro Alegre, Valparaíso"` climbs up to its city/town parent so all
queries inside that city share one cache cell.

**`--max-extent KM`** (default 50) — if the OSM admin polygon's longest
side exceeds this, a tighter fetch bbox is synthesized around the
centroid. Necessary for places like Valparaíso, Chile (the "commune"
includes Easter Island ~3,500 km away).

**`--radius KM`** (default 8) — half-extent of the synthesized bbox.

**`--db FILE`** (default `cache.db`) — alternate SQLite cache database.

**Output:** JSON with `osm_id`, `osm_type`, `bbox` (south, west, north,
east), `lat`/`lon` centroid, `extent_km`, `level`, and `bbox_synthesized`.

**Cache:** SQLite (`cache.db`, table `geocode_cache`). Geocoding is
deterministic for a given query, so cached responses are effectively
permanent. To clear, delete `cache.db` (it'll re-init on next run).

**Examples:**
```bash
uv run python geocode.py "Lisbon"
uv run python geocode.py "Cerro Alegre, Valparaíso"
uv run python geocode.py "Tokyo" --radius 5
uv run python geocode.py "Reykjavík" --max-extent 100
```

---

### fetch.py — anchor → layers

Geocodes the place and pulls every layer in `layers.json` (or a chosen
subset). Non-heavy layers go in a single Overpass union query; each
heavy layer gets its own request so a slow query doesn't block the
others.

```
fetch.py [-h] [--layers IDS] [--force] [--out FILE]
         [--radius KM] [--db FILE] place
```

**`place`** — same syntax as `geocode.py query`.

**`--layers IDS`** — comma-separated layer ids to fetch. Default: every
layer in the registry. Useful for iterating quickly:
`--layers road_motorway,coastline`.

**`--force`** — ignore the cache and re-fetch every layer.

**`--out FILE`** (default `place_data.json`) — output JSON with
`{anchor, layers: {layer_id: [elements]}}`.

**`--radius KM`** (default 15, range 5–50) — half-extent of the
synthesized square fetch bbox. The fetch bbox is always
`2 × radius_km` km on a side, centered on the anchor's lat/lon. The
admin polygon from Nominatim is **not** used as a fetch bbox — it
contributes only the anchor identity and centroid.

**`--db FILE`** (default `cache.db`) — alternate SQLite cache database.

**Cache:** SQLite (`cache.db`, table `place_cache`), keyed by
`(osm_type, osm_id, layer_id, radius_km)`, TTL 14 days. Same place at
a different radius is a different cache cell. Sub-place queries that
resolve to the same anchor at the same radius share the cache.
Per-layer rows are gzipped JSON; orjson is used for the encode/decode,
size is roughly 1/6 of the raw response.
Use `db.py purge` to drop expired rows, or `db.py stats` to inspect.

**Examples:**
```bash
uv run python fetch.py "Lisbon"
uv run python fetch.py "Tokyo, Japan" --out tokyo.json
uv run python fetch.py "Valparaíso, Chile" --layers road_motorway,road_primary
uv run python fetch.py "Reykjavík" --force
uv run python fetch.py "Lisbon" --radius 30          # bigger scope
uv run python fetch.py "Lisbon" --radius 8           # tighter scope
```

**First-run timing** for a mid-size city: roughly 10–60 s. Subsequent
runs against the same anchor + radius: under a second.

---

### render.js — layers → image

Reads `place_data.json`, walks `render_order` from `layers.json`, draws
each layer with the `kind` and `style` from the registry.

```
node render.js [INPUT] [options]
```

**`INPUT`** — path to `place_data.json` (default `./place_data.json`).
Also accepts the legacy `nodes.json` array form via match-rule
bucketing (read-only fallback).

**`-o, --output PATH`** — output file. Default `roads.png`, or
`roads.svg` with `--svg`.

**`--svg`** — write SVG instead of PNG.

**`--layers PATH`** — custom registry file. Useful for swapping color
schemes: keep `layers.json` and `layers_dark.json`, render against
each.

**`--only IDS`** — comma-separated layer ids to render. Defaults to
every layer that has data.

**Examples:**
```bash
node render.js
node render.js --svg -o map.svg
node render.js lisbon.json -o lisbon.png
node render.js --only road_motorway,road_trunk,road_primary
node render.js --layers layers_dark.json -o dark.png
```

---

### discover.py — find unknown tags in a place

Tier-2 tag discovery: queries Overpass for *all* tags in a small bbox
(`out tags N` — no geometry, much cheaper than a full fetch), drops
tags already covered by `layers.json` and obvious metadata, ranks the
rest by frequency. Use this to find city-specific landmarks (funiculars,
ferries, `place=quarter` labels, etc.) you didn't think to add to the
registry.

```
discover.py [-h] [--lat DEG] [--lon DEG] [--radius KM]
            [--limit N] [--top N] [--min-count N]
```

**`--lat`, `--lon`** — bbox center. Defaults to Valparaíso.

**`--radius KM`** (default 2) — half-extent of the discovery bbox.

**`--limit N`** (default 5000) — Overpass element cap (the `out tags N`
parameter).

**`--top N`** (default 30) — show top N ranked candidates.

**`--min-count N`** (default 3) — suppress candidates seen fewer times.

**Workflow:** run this in a city you care about, scan the candidate
list, promote any genuinely useful tags by adding them as new entries
in `layers.json` (then re-fetch with `--force`).

> Note: this script still uses raw `--lat`/`--lon`. It pre-dates
> `geocode.py`. Future versions may swap to a place query.

---

### db.py — SQLite cache utility

Inspect, init, or purge the shared cache database. Every other tool
(`geocode.py`, `fetch.py`, `server.py`) reads and writes through this
module; you only call it directly to manage the DB itself.

```
db.py [-h] [--db FILE] {init,stats,purge,anchors}
```

**Subcommands**

- **`init`** — create the schema if missing (idempotent).
- **`stats`** — JSON dump of row counts, total elements, file size.
- **`purge`** — delete `place_cache` rows older than the 14-day TTL.
  Returns the number of rows deleted.
- **`anchors`** — list every cached anchor, newest first.

**`--db FILE`** (default `cache.db`) — alternate database path.

**Examples:**
```bash
uv run python db.py init                      # one-time schema setup
uv run python db.py stats                     # how big is my cache?
uv run python db.py anchors                   # what places are cached?
uv run python db.py purge                     # drop expired layer data
uv run python db.py --db custom.db stats      # use a side cache
```

**Schema:**

| Table | Key | Holds |
|---|---|---|
| `geocode_cache` | `query` | raw Nominatim search response (gzipped JSON) |
| `anchor` | `(osm_type, osm_id)` | resolved Anchor record (bbox, level, …) |
| `place_cache` | `(osm_type, osm_id, layer_id)` | per-layer Overpass elements (gzipped JSON), `fetched_at` for TTL |

`place_cache` has a `FOREIGN KEY → anchor ON DELETE CASCADE`, so dropping
an anchor wipes its cached layers. WAL mode is enabled for safe
concurrent reads from `server.py`.

---

### server.py — HTTP API

A FastAPI wrapper over `geocode.py` and `fetch.py`. Exposes the pipeline
as JSON endpoints so a frontend (or curl) can drive it. In-process
request coalescing means two simultaneous requests for the same place
share one Overpass call.

```
server.py [-h] [--host HOST] [--port PORT] [--db FILE] [--reload]
```

**`--host HOST`** (default `127.0.0.1`) — interface to bind. Use
`0.0.0.0` to expose on the LAN (be deliberate; CORS is wide open).

**`--port PORT`** (default `8000`).

**`--db FILE`** (default `cache.db`) — alternate cache database.

**`--reload`** — auto-restart on source edits (dev only).

**Endpoints**

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/api/layers` | The full layer registry (`layers.json`). |
| `POST` | `/api/place`  | `{"query": "Lisbon"}` → resolved Anchor JSON. |
| `POST` | `/api/data`   | `{"anchor": {...}, "layers": [...], "radius_km": 15, "force": false}` → `{anchor, layers: {id: [elements]}}`. |
| `GET`  | `/api/health` | `{ok, ...db.stats()}`. |
| `GET`  | `/docs`       | OpenAPI / Swagger UI with interactive examples. |

**`radius_km` on `/api/data`** (default 15, range 5–50). Same scalar
as `fetch.py --radius`. Server synthesizes a `2 × radius_km` km square
around the anchor's centroid and uses that as the fetch bbox; the
incoming `anchor.bbox` is ignored. The response's `anchor.bbox` is the
synthesized square so the renderer projects from the correct extent.

**Request coalescing.** Concurrent identical calls share a single
in-flight `asyncio.Future`:
- `/api/place` is keyed by lowercased query string.
- `/api/data` is keyed by `(osm_type, osm_id, sorted_layer_ids, radius_km, force)`.

Once the underlying call resolves, the future is dropped; subsequent
requests hit the SQLite cache directly. The `/api/data` future stores
the **already-encoded JSON bytes**, so coalesced waiters return the
same response without re-encoding.

**JSON performance.** Heavy layers (`building`, `building_roof`,
`road_residential`) can produce 30+ MB responses. The server uses
[`orjson`](https://github.com/ijl/orjson) — about 5–10× faster than
stdlib `json` on encode and 3× on decode — and returns each `/api/data`
response by explicitly constructing an `ORJSONResponse` so FastAPI's
`jsonable_encoder` doesn't pre-walk the structure (that pre-walk turns
a 200 ms render into a 6 s render on big payloads). Net result: cached
`building` at 30 km radius lands in ~800 ms instead of ~7 s.

**Examples:**
```bash
# start the dev server
uv run python server.py
uv run python server.py --reload --port 5000

# curl smoke tests
curl http://127.0.0.1:8000/api/health
curl -X POST http://127.0.0.1:8000/api/place \
     -H "Content-Type: application/json" \
     -d '{"query":"Lisbon"}'
curl -X POST http://127.0.0.1:8000/api/data \
     -H "Content-Type: application/json" \
     -d '{"anchor": <Anchor JSON>, "layers": ["coastline"]}'
```

Browse `http://127.0.0.1:8000/docs` for the interactive OpenAPI UI —
each `POST` endpoint ships with multiple labelled request examples in a
dropdown ("Simple place name", "Sub-place that climbs to its city
parent", "Force re-fetch", etc.) plus matching response samples.

---

### Web frontend (`../web/`)

Svelte 5 + TypeScript + Vite. Consumes `server.py` over HTTP, renders
into an HTML5 `<canvas>`, supports search, layer toggles, a fetch-radius
slider, and pan/zoom.

```
web/src/
├── App.svelte               # topbar + sidebar + canvas layout
├── lib/
│   ├── api.ts               # typed wrappers around /api/place|data|layers
│   ├── state.svelte.ts      # global $state (anchor, data, view, etc.)
│   ├── render.ts            # browser canvas renderer (ported from render.js)
│   └── types.ts             # mirrors of server.py Pydantic models
└── components/
    ├── SearchBar.svelte
    ├── LayerSidebar.svelte  # built from /api/layers, grouped by category
    ├── RadiusSlider.svelte  # 5-50 km, debounced refetch
    └── MapCanvas.svelte     # wheel zoom, drag pan, double-click, reset
```

**Run it (two terminals):**

```bash
# terminal 1 — API
cd scripts
uv run python server.py

# terminal 2 — frontend
cd web
npm run dev
# open http://localhost:5173
```

Vite proxies `/api/*` → `http://127.0.0.1:8000/api/*` in dev. In
production the same paths hit the same FastAPI process behind a
reverse proxy (see `TODO.md`).

**Pan/zoom interactions:**

| Input | Effect |
|---|---|
| Mouse wheel | Continuous zoom centered on cursor (`1.0015^(-deltaY)`). |
| Click + drag | Pan. Pointer Events with `setPointerCapture` so dragging past the canvas keeps tracking. |
| Double-click | Zoom in 2× centered on click. |
| Reset button (⟲ overlay) | Snap back to default view. |

The canvas uses **cover** projection (`object-fit: cover`) by default —
the data fills the canvas, the long axis crops at the edges. Zoom is
clamped to `[0.5, 50]`, the view center is soft-clamped to within
1.5× the bbox half-extent so the data can't disappear offscreen
entirely. The view auto-resets on new search and on radius change
(both produce a new `anchor.bbox`); layer toggles preserve the view.

> **Perf note.** With heavy layers like `building` enabled, pan/zoom
> currently re-projects every element every frame, which can be choppy
> on big cities. The fix is tracked in `TODO.md` (Path2D + canvas
> transform).

---

## The layer registry — `layers.json`

A single source of truth for fetcher, renderer, and discovery.
[Open it](layers.json) and edit directly; no code changes needed.

### Top-level shape

```jsonc
{
  "version": 1,
  "categories": { "roads": {...}, "water": {...}, ... },
  "render_order": ["coastline", "landuse_forest", ...],
  "layers": [ {...}, {...}, ... ]
}
```

- **`render_order`** — back-to-front draw order. Must list every layer
  id in `layers`. The renderer iterates this list; layers not in it are
  silently dropped from the image.
- **`categories`** — UI grouping for the eventual layer toggle panel.
  Doesn't affect fetch or render.

### Per-layer shape

```jsonc
{
  "id": "road_motorway",          // stable cache key segment
  "category": "roads",            // UI grouping
  "kind": "line",                 // line | polygon | coastline | point
  "selector": "way[\"highway\"=\"motorway\"]",   // Overpass QL fragment
  "match": [
    { "key": "highway", "value": "motorway" }
  ],
  "default_visible": true,        // initial state in eventual UI
  "heavy": false,                 // fetcher isolates heavy layers in own request
  "style": { "stroke": "#e74c3c", "width": 4 }
}
```

#### `kind`

- **`line`** — drawn as stroked path. Style: `stroke`, `width`,
  optional `dash` (array), `opacity`.
- **`polygon`** — drawn as filled path if closed, stroked otherwise.
  Style: `fill`, optional `opacity`, optional `stroke` + `width`.
- **`coastline`** — special. Stitches `way[natural=coastline]` segments
  into chains and builds the land polygon by walking to the nearest
  canvas corner. Style: `fill` (land color), `background` (ocean color
  used as canvas fill).
- **`point`** — reserved. Not yet implemented.

#### `selector` and `match`

`selector` is the Overpass fragment used at fetch time — gets
concatenated into a union query along with other layers' selectors.
Don't include the trailing `;` — the fetcher adds it.

`match` is the structured form used to route Overpass response elements
back into per-layer cache cells. It's an AND-list of conditions:

- `{"key": K, "value": V}` — `tags[K] == V`
- `{"key": K, "present": true}` — `K` is in tags (any value)
- `{"key": K, "value_not": V}` — `tags[K] != V`

You can combine `present` and `value_not` in the same condition (see
the `building` layer — present + not "roof").

#### `heavy`

Set true on layers that are bulky (`building`, `road_residential`,
`road_footway` in the default registry). Heavy layers get their own
Overpass request, so a slow building query doesn't stall roads or
water. They're also retried independently on 504/429.

### Adding a layer

1. Pick an id (snake_case is the convention, e.g. `railway_funicular`).
2. Add an entry to `layers`. Pick `kind`, write `selector` + `match`,
   set `style` from one of the existing layers as a starting point.
3. Insert the id into `render_order` at the right vertical position
   (back-to-front: fills early, lines late, overlays last).
4. Re-run `fetch.py "<place>" --layers railway_funicular` (or `--force`
   to refresh everything), then `render.js`.

### Editing styles

Edit the `style` block of any layer and re-run `render.js` — no fetch
needed since fetcher and renderer share the registry.

---

## Caches

All persistent cache lives in **one SQLite file**, `cache.db`. The
old per-script JSON files (`geocode_cache.json`, `place_cache.json`)
are gone.

| Store | Owner | Key | TTL | How to clear |
|---|---|---|---|---|
| `cache.db` (`geocode_cache` table) | `db.py` ↔ `geocode.py` | full query string | none | delete `cache.db` |
| `cache.db` (`anchor` table) | `db.py` ↔ `geocode.py` | `(osm_type, osm_id)` | none | delete `cache.db` (cascades to `place_cache`) |
| `cache.db` (`place_cache` table) | `db.py` ↔ `fetch.py` | `(osm_type, osm_id, layer_id)` | 14 days | `db.py purge`, or `fetch.py --force` for one anchor |
| `place_data.json` | `fetch.py` output | — | — | overwritten every run |
| `roads.png` / `roads.svg` | `render.js` output | — | — | overwritten every run |

Storage is gzipped JSON in BLOB columns — roughly 1/6 the size of raw
JSON. A single mid-size city's full layer set is ~3–5 MB on disk.

WAL mode + foreign-key cascades + per-row `INSERT … ON CONFLICT DO
UPDATE` make `cache.db` safe for concurrent reads from `server.py`.

Inspect with `db.py stats` and `db.py anchors`.

---

## Architecture (why it's designed this way)

### Place-based caching, not coordinate-based

A naive cache keyed by `(layer, bbox-string)` is brittle: pan the map
5 m and the cache invalidates. Tile-based caching (slippy-map style
`z/x/y`) helps but introduces edge dedup.

This tool uses **named-place anchoring**: every query is geocoded to a
stable OSM `osm_id`. Sub-place inputs (`"Alfama, Lisbon"`) climb up to
their city parent and reuse its cache. Two upsides:

1. Coverage check is one boolean: "do I have this place?" — not "do I
   have all the tiles?".
2. Cache key is human-readable: `relation/110808/road_motorway` rather
   than `15/8732/13241/road_motorway`.

### Synthesized bboxes for awkward administrative polygons

Some OSM admin boundaries are geographically scattered (Valparaíso
commune includes Easter Island; Tokyo Metropolis includes Pacific
islands). Their raw bbox is unfetchable.

Strategy: keep the **anchor identity** (osm_id), but synthesize a
tighter fetch bbox around Nominatim's centroid (which sits in the urban
core). Cache stays stable across sub-place queries; the data we
actually fetch is the part that matters. Triggered when extent
> `--max-extent` (default 50 km).

### Single-source-of-truth registry

All components read `layers.json`:
- fetcher uses `selector` and `heavy` to build Overpass queries
- fetcher uses `match` to route response elements
- renderer uses `kind`, `style`, `render_order` to draw
- discovery uses `selector` to mark "known" tags
- API serves it raw at `GET /api/layers`

Adding a layer = one JSON edit. No Python, no JS.

### One SQLite file, not many JSON files

The cache started as two JSON files (`geocode_cache.json` and
`place_cache.json`) loaded entirely into memory on each script run.
Workable for a single CLI process, but the moment we wrap things in an
HTTP server it falls apart: concurrent writes race, the file balloons
in memory, and the on-disk format isn't gzip-friendly.

Switching to SQLite gives us, in one shot:

- **WAL concurrency** — multiple `server.py` workers can read the same
  DB without blocking each other.
- **Per-row writes** — no "rewrite the whole 50 MB file just to add one
  layer" problem.
- **Gzipped BLOBs** — ~6× compression for free.
- **Foreign-key cascades** — drop an anchor row and its `place_cache`
  rows go with it (with the important caveat that the upsert path
  uses `INSERT … ON CONFLICT DO UPDATE`, *not* `INSERT OR REPLACE`,
  to avoid triggering that cascade on every geocode call).
- **One DB, one CLI (`db.py`)** — `init`, `stats`, `purge`, `anchors`.

---

## Troubleshooting

**Nominatim returns nothing**
The query string is too obscure or misspelled. Try a broader form:
`"Cerro Alegre, Valparaíso, Chile"` instead of just `"Cerro Alegre"`.

**Geocoding picks the wrong place**
Disambiguate by adding parents: `"Springfield, Illinois"` not just
`"Springfield"`.

**Overpass returns 504**
Server-side timeout. The fetcher retries automatically with backoff.
If it still fails, the place might be too large — synthesize a smaller
bbox (`--max-extent 30 --radius 5` on geocode, then re-fetch).

**Overpass returns 429**
Rate limit. Wait a minute. The retry logic handles short bursts; long
bursts mean you're hammering the public server — be polite.

**Overpass returns 406**
Missing or odd User-Agent. The fetcher sets one; if you've modified
`USER_AGENT` in `fetch.py`, restore it.

**Render comes out tiny / wrong aspect**
Old `place_data.json` from before the anchor format. Re-fetch:
`fetch.py "<place>" --force`.

**Layer I added doesn't appear**
Common causes:
- Forgot to add the id to `render_order`.
- `match` rules don't fit any returned element. Check by running with
  `--only your_new_layer` and verifying the count.
- Element type filter mismatch — `selector` says `way[...]` but the
  data is in nodes. Loosen to `nwr[...]`.

**Discovery surfaces noise (`*=yes`, sub-tags)**
Expected for tier-2. Edit `BORING_EXACT` and `BORING_PREFIXES` in
`discover.py` to filter additional cruft.

**Cache feels stale or oversized**
Run `uv run python db.py stats` to see what's in there. `db.py purge`
drops `place_cache` rows older than the 14-day TTL. Nuclear option: just
delete `cache.db` (it'll re-init on next run).

**Server returns 500 on `/api/data`**
Inspect the server console — `fetch.fetch()` prints Overpass HTTP body
on failure. Common causes: 504 (place too large; lower `--max-extent`
on `/api/place`), 429 (rate-limited; wait a minute).

**`db.py purge` deletes more than expected after re-geocoding**
Shouldn't happen — `db.put_anchor()` uses `INSERT … ON CONFLICT DO
UPDATE`, not `INSERT OR REPLACE`. If you see this, your `db.py` is from
before the upsert fix. Re-pull and `db.py init`.

---

## Glossary

- **Anchor** — the `(osm_id, osm_type, bbox)` triple geocode.py returns
  for a place query. Acts as the cache key prefix for all layers.
- **Layer** — one fetch unit and one toggle. Defined by an entry in
  `layers.json`. Has its own selector, match rule, and style.
- **Heavy layer** — a layer flagged `heavy: true` so the fetcher pulls
  it in its own Overpass request rather than the union query. Used for
  bulky data (buildings, residential roads).
- **Place** — a free-text input string the user types. Goes through
  Nominatim and resolves to an anchor.
- **Synthesized bbox** — fetcher bounding box derived from the
  anchor's centroid + a fixed radius, used when the OSM admin polygon
  is too big or geographically scattered to fetch in one shot.
- **Tier 1** — the curated layer registry shipped in `layers.json`.
- **Tier 2** — on-demand tag discovery via `discover.py` to find
  city-specific tags missing from Tier 1.
- **Match rule** — structured form of an Overpass selector used to
  bucket response elements into per-layer cells without re-running
  Overpass.

---

## Files in this folder

| File | Role |
|---|---|
| `geocode.py` | Place → Anchor (Nominatim wrapper) |
| `fetch.py` | Anchor → per-layer OSM data (Overpass) |
| `render.js` | place_data.json → PNG/SVG (Node canvas) |
| `discover.py` | Tier-2 tag discovery |
| `db.py` | SQLite cache backend + CLI (`init`/`stats`/`purge`/`anchors`) |
| `server.py` | FastAPI wrapper exposing the pipeline as HTTP JSON endpoints |
| `layers.json` | Layer registry (single source of truth) |
| `cache.db` | SQLite cache: geocodes, anchors, per-layer Overpass data |
| `place_data.json` | Last fetch output |
| `roads.png` / `roads.svg` | Last render output |
| `MANUAL.md` | This file |
| `TODO.md` | Pending work — renderer perf + deploy plan |
| `pyproject.toml` / `uv.lock` | Python deps |
| `package.json` / `package-lock.json` | Node deps for `render.js` |
| `ref_main.py`, `ref_bbox.py` | Legacy reference (pre-refactor) |

The browser frontend lives in `../web/` (Svelte 5 + Vite + TypeScript).
