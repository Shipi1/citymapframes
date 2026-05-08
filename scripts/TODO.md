# TODO

Open tracks:

1. [Sharable designs](#sharable-designs-server-side-presets) — let users
   POST a current design to a new `presets.db` and share it via a
   short-id URL. **In progress.**
2. [Export SVG/PNG](#export-svgpng-from-the-frontend) — let the user
   download the current canvas as an image from a button overlay.
3. [Customize layer styles](#customize-layer-styles-colors-widths) —
   continue past colors+widths: opacity, dash, "reset all" button.
4. [Deploy](#deploy-serverpy-at-shipisnaturecomapimap) — wire up
   `server.py` at `shipisnature.com/api/map/` behind nginx.

Done:

- ✅ **Renderer perf: Path2D + canvas transform**. Layers compile once
  to a `Path2D` per data load; per-frame draw is one `setTransform` plus
  one `stroke`/`fill` per layer. Pan/zoom is 60 fps even with `building`
  enabled. (See last section of this file for the original spec.)
- ✅ **Layer color overrides + width slider with popout**.
  `app.layerOverrides` is keyed by layer id, persisted to localStorage,
  merged on top of registry style at draw time. Sidebar has a color
  picker (two for coastline) and a click-to-open width slider per
  applicable layer. No path rebuild on style change.

---

# Sharable designs (server-side presets)

Owner: `scripts/presets_db.py` (new), endpoints in `scripts/server.py`,
`web/src/components/ShareButton.svelte` (new), URL-hydration in
`web/src/App.svelte`.

## Goal

Multi-user platform: Alice designs a styled view of Lisbon, copies a
short URL, sends it to Bob. Bob clicks it and sees Alice's exact map.
Bob can tweak and re-share — the new share keeps a `parent_id` link
back to Alice's original (no UI for the lineage in v1, but the data is
stored for later).

## Design (v1) — anonymous + immutable

| Decision | Choice |
|---|---|
| Identity | none — anonymous, write-once |
| Storage | new file `presets.db` (separate from `cache.db`) |
| URL | `?share={id}` (8-char base62) |
| Dedup | `UNIQUE` index on `sha256(design)` — same design twice → same id |
| Edits | not in v1 — re-post creates a new id |
| Listing/explore | not in v1 — only `POST` and `GET {id}` |
| Rate limit | placeholder (IP hash stored); enforce before deploy |

## Schema (`presets.db`)

```sql
CREATE TABLE preset (
    id              TEXT PRIMARY KEY,        -- 8-char base62
    schema_ver      INTEGER NOT NULL,
    name            TEXT NOT NULL,
    design          BLOB NOT NULL,           -- gzipped orjson of SharedDesign
    content_hash    TEXT NOT NULL,           -- sha256 — for dedup
    parent_id       TEXT,                    -- the share this was remixed from
    creator_ip_hash TEXT,                    -- for future rate-limit, never returned
    view_count      INTEGER NOT NULL DEFAULT 0,
    created_at      INTEGER NOT NULL,
    FOREIGN KEY (parent_id) REFERENCES preset(id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX idx_preset_content_hash ON preset(content_hash);
CREATE INDEX idx_preset_created ON preset(created_at DESC);
CREATE INDEX idx_preset_parent  ON preset(parent_id);
```

## SharedDesign

```ts
interface SharedDesign {
  schemaVersion: 1;
  name: string;
  query: string;                       // "Lisbon" — display + fallback
  anchor: {                            // hardens against re-geocoding drift
    osm_type: string;
    osm_id: number;
    lat: number;
    lon: number;
  };
  radiusKm: number;                    // 5..50
  enabledLayers: string[];
  overrides?: Record<string, LayerStyleOverride>;
  view?: View;                         // optional pan/zoom snapshot
}
```

Recipient skips Nominatim — the stored anchor is enough to rebuild
without it. Falls back to `query` only if the anchor's `osm_id` no
longer resolves.

## Endpoints

```
POST /api/share
  body: { design: SharedDesign, parent_id?: string }
  → 201 { id, name, parent_id?, created_at }     ← new
  → 200 { id, name, parent_id?, created_at }     ← deduped (same content_hash)
  → 400 invalid schema, missing name
  → 413 design > 8 KB

GET /api/share/{id}
  → 200 { id, name, design, parent_id?, view_count, created_at }
  → 404
  (server increments view_count out-of-band)
```

Caps:

| Limit | Value |
|---|---|
| Max body | 8 KB |
| Max name | 80 chars |
| ID length | 8 chars base62 (62^8 ≈ 2 × 10¹⁴) |
| POST rate | placeholder — enforce slowapi `10/hour/ip` before deploy |

## Frontend

- **ShareButton** in topbar: builds a `SharedDesign` from current state,
  POSTs, copies `${origin}/?share=${id}` to clipboard, shows brief toast.
- **Boot hydration** in `App.svelte`: read `?share=...` from URL,
  `GET /api/share/{id}`, hydrate state (radius / layers / overrides /
  view / anchor), then `POST /api/data` to fetch geometry. **Skips
  `/api/place`** because the stored anchor is authoritative.
- **Remix lineage**: if the user loaded from `?share=X` and posts a new
  share, send `parent_id=X`. After a successful share, replace
  `?share=X` with `?share=newId` via `history.replaceState`.
- **URL hygiene**: clearing the query param on a brand-new search keeps
  the URL honest about "this is no longer that share."

## Acceptance criteria

- [ ] `POST /api/share` with a valid SharedDesign returns a fresh id;
      same payload posted again returns the same id.
- [ ] `GET /api/share/{id}` returns the full design and increments
      view_count.
- [ ] Frontend "Share" button copies a working URL to clipboard.
- [ ] Pasting that URL in another browser tab loads the same design
      without geocoding.
- [ ] Sharing again from a loaded design posts a new preset whose
      `parent_id` points to the original.
- [ ] Body > 8 KB returns 413; bogus id returns 404; missing name → 400.

## Out of scope (v1)

- Edit/delete (no edit tokens)
- "My designs" list (no accounts, no per-IP listing)
- Public explore page / recent shares
- Lineage UI ("remixed from {parent.name}")
- Server-side rate limit enforcement (data is captured; enforcement is
  on the deploy track)
- Accounts and OAuth

---

# Export SVG/PNG from the frontend

Owner: `web/src/lib/export.ts` (new), button overlay in `MapCanvas.svelte`.

## Why

`scripts/render.js` already does this server-side from `place_data.json`,
but the browser has all the same data in memory plus the user's current
view (pan/zoom/enabled layers). Exporting straight from the canvas is
the natural finishing move after framing a city — and it keeps any
custom layer styling (see next track) "in" the export for free.

## Plan

### PNG — easy

- The canvas is already rendered. Just `canvas.toBlob('image/png')` and
  trigger a download via `<a download>`.
- Optionally: render to an **offscreen canvas at higher resolution**
  (e.g. 2× or 4× DPR) for crisp poster-size exports. Same pipeline,
  different bitmap size.

### SVG — port the `render.js` SVG path

`render.js`'s SVG path emits `<path d="...">` per element. We can do the
same in the browser:

```ts
function exportSvg(opts: RenderOptions): string {
  const parts: string[] = [];
  parts.push(`<svg xmlns="..." width="${cssW}" height="${cssH}">`);
  parts.push(`<rect width="${cssW}" height="${cssH}" fill="${oceanColor}"/>`);
  for (const layerId of registry.render_order) {
    if (!enabledLayers.has(layerId)) continue;
    const elements = data.layers[layerId];
    // ... walk elements, project each point, build d=... string ...
  }
  parts.push('</svg>');
  return parts.join('\n');
}
```

Don't try to read coordinates back out of `Path2D` — it isn't
introspectable. Walk the raw `data.layers[layerId]` arrays again with
the same projection the canvas uses.

Reuse the existing logic in `scripts/render.js` (the SVG branch) almost
verbatim — it's already TypeScript-friendly.

### UI

Two buttons in the canvas overlay (top-right, next to the reset
button):

```
[ ⟲ ]   [ PNG ]   [ SVG ]   1.20×
```

Click → triggers a download. Filename:
`{anchor.name}-{radiusKm}km-{ts}.{ext}` with the place name slugified.

### Considerations

- **Current view vs full data**: export reflects the current canvas —
  same view, same layer toggles, same custom styles. Most natural.
- **Long-running export**: SVG of buildings at 30 km is ~10–30 MB. Build
  the string in a Web Worker if it blocks the main thread > 100 ms.
- **PNG resolution**: maybe a small dropdown — "1×, 2×, 4×". Defaults
  to native DPR.

## Acceptance criteria

- [ ] PNG button downloads the current canvas as a `.png` file with a
      sensible filename.
- [ ] SVG button downloads a valid `.svg` that opens in a browser /
      Inkscape and visually matches the canvas.
- [ ] Both honor enabled layers and the current view (pan/zoom).
- [ ] Both honor any custom layer styles (once that track lands).
- [ ] No measurable hit to interactive performance — exports run on
      demand, never per frame.

## Out of scope

- PDF export (use the SVG → external converter).
- Print-styled exports (page size, margins).
- Background-thread streaming export for huge cities — only worth it if
  users actually report blocking.

---

# Customize layer styles (colors, widths)

Owner: `web/src/components/LayerSidebar.svelte` (UI),
`web/src/lib/state.svelte.ts` (state), `web/src/lib/render.ts` (apply).

## Why

The layer registry ships with one canonical color/width/dash per layer.
Users will want to tweak these — make roads thicker, change the ocean
color, dim buildings, etc. Today they'd have to edit `layers.json` and
restart the dev server, which is heavy.

Live-editing in the browser keeps the registry as the **default** while
letting users override per layer for their session (or saved to
localStorage).

## Plan

### 1. Override store

```ts
// state.svelte.ts
type StyleOverride = Partial<LayerStyle>; // stroke?, fill?, width?, dash?, opacity?
app.layerOverrides: Record<string, StyleOverride>; // keyed by layer_id
```

Helpers: `setOverride(id, patch)`, `resetOverride(id)`, `resetAll()`.
Persist to `localStorage` so customizations survive a reload.

### 2. Effective-style merge in the renderer

Build the merged style at draw time, **not** at compile time, so
re-styling needs zero `Path2D` rebuilds:

```ts
function effectiveStyle(layer: Layer, overrides: StyleOverride): LayerStyle {
  return { ...layer.style, ...overrides };
}
```

`drawCompiledLayer` reads `effectiveStyle(layer, app.layerOverrides[id] ?? {})`
instead of `layer.style ?? {}`.

### 3. Sidebar UI — expandable per-layer panel

Click a layer's row in `LayerSidebar` → expands into:

```
☑ road_motorway          [reset]
   stroke  ▢ #e74c3c
   width   ──●─────────  4 px
   opacity ────────●───  1.00
   dash    [_____ _____]   (text input, comma-separated)
```

- `<input type="color">` for stroke/fill (ocean color = coastline.background)
- `<input type="range">` for width (1–20) and opacity (0–1)
- Text input for dash array
- "Reset" button per-layer wipes that layer's override

Compact: only one expanded panel at a time, click another to swap.

### 4. Coastline gets two color pickers

`coastline.style` has both `fill` (land) and `background` (ocean). Both
should be customizable. The ocean color also drives the canvas
background fill, so a change there ripples to non-coastline areas
naturally.

### 5. No path rebuild

Geometry is unchanged. Only `ctx.strokeStyle`, `ctx.fillStyle`,
`ctx.lineWidth`, etc., differ. The compile cache stays warm. Re-render
is a single RAF frame.

### 6. Reset / share

- "Reset all" button in the sidebar header.
- Stretch goal: serialize `app.layerOverrides` to a URL hash so users
  can share a styled view (`?style=eyJyb2FkX21vdG9yd2F5...`).

## Acceptance criteria

- [ ] Color picker on `road_motorway` instantly recolors all motorways
      without a network call or path rebuild.
- [ ] Width slider on any line layer changes thickness with no
      perceptible lag (sub-frame).
- [ ] Reset button restores the registry default for that layer.
- [ ] "Reset all" wipes every override.
- [ ] Customizations persist across page reload (localStorage).
- [ ] Export PNG/SVG (other track) honors the current overrides.

## Out of scope

- Server-side persistence of styles (would need auth).
- Theme presets / community themes.
- Style transitions/animations on change.
- Editing the registry layer list itself (adding/removing layers from
  the UI).

---

# Renderer perf: Path2D + canvas transform

> ✅ **Done.** Implemented in `web/src/lib/render.ts` and
> `web/src/components/MapCanvas.svelte`. The notes below remain for
> historical reference and to document the design decisions.

Owner: `web/src/lib/render.ts` (and `web/src/components/MapCanvas.svelte`).

## Why

Today the renderer treats data as input geometry on **every** pan/zoom
frame. For Lisbon at 30 km radius with `building` enabled that's roughly
600 000 `project()` calls + 600 000 canvas API calls per frame, each
allocating a fresh `[x, y]` pair. The garbage collector runs the show
and frames take 100–200 ms. Pan/zoom feels choppy whenever a heavy
layer is on.

Nothing about the elements changes when the user pans — only the view.
The fix is to do the geometry work **once**, at data-load time, and let
each frame do only the view transform.

## Plan

### 1. Build one `Path2D` per layer at data-load time

Triggered when `app.data` changes (new search, radius change, layer
toggle that adds new layer data).

```ts
// pseudocode
function buildLayerPath(elements, cosLat, kind): Path2D {
  const path = new Path2D();
  for (const el of elements) {
    const g = el.geometry;
    if (!g || g.length < 2) continue;
    path.moveTo(g[0].lon * cosLat, g[0].lat);
    for (let i = 1; i < g.length; i++) {
      path.lineTo(g[i].lon * cosLat, g[i].lat);
    }
    if (kind === 'polygon' && isClosed(g)) path.closePath();
  }
  return path;
}
```

Coordinates are stored in **(lon × cosLat, lat)** so the per-frame
transform is isotropic — uniform line widths regardless of latitude.

Cache key: `(layer_id, anchor.osm_id, anchor.lat, anchor.lon)`. When
`app.data.anchor.bbox` changes, rebuild.

### 2. Per-frame: `setTransform` + `ctx.stroke(path)` / `ctx.fill(path)`

```ts
const cosLat = ctx.cosLat;            // anchor center, cached
const scale  = ctx.defaultScale * view.zoom;

ctx.setTransform(
  scale,  0,                          // x-axis
  0,     -scale,                      // y-axis (flipped)
  canvasW/2 - view.centerLon * cosLat * scale,
  canvasH/2 + view.centerLat * scale,
);

for (const layer of enabledLayers) {
  ctx.lineWidth   = layer.style.width / scale;   // un-scale so widths stay pixel-correct
  ctx.strokeStyle = layer.style.stroke;
  ctx.stroke(layer.path);                        // GPU rasterizes
}
```

Per-frame work goes from O(elements) to O(layers). Pan/zoom should
hit 60 fps even with `building` on.

### 3. Coastline special case

Coastline currently walks the canvas corners to fill land, which depends
on canvas size. It can't be pre-baked the same way. Two options:

- Pre-build the **chains** in (lon × cosLat, lat), then at render time
  build the closing path in canvas-pixel space — the corner walk only
  touches a handful of points so it's still cheap.
- OR pre-render the coastline alone to an offscreen canvas, blit on
  each frame.

Start with the first option — it stays in the `Path2D` framework.

### 4. Line-width unscaling

`ctx.stroke(path)` uses the current transform's scale, so a path with
`lineWidth: 2` would render at `2 * scale` pixels. Always set
`ctx.lineWidth = layer.style.width / scale` per layer per frame. Same
trick for `setLineDash` if any layer uses dashes.

### 5. Memory budget

Path2D for `building` at 30 km radius is roughly comparable to the raw
geometry — 5–20 MB extra in browser memory. Acceptable. We can release
old paths when `app.data` changes (let GC handle it; `app.data = ...`
drops references).

## Acceptance criteria

- [x] Pan + zoom on Lisbon at 30 km with `building` enabled is smooth
      (≤16 ms per frame) on a mid-range laptop.
- [x] Visual output matches the current renderer (within anti-aliasing
      tolerance) at zoom = 1.
- [x] Layer toggle still works: enabling a previously-disabled layer
      builds its path lazily.
- [x] Resize and DPR change still trigger a redraw without a path
      rebuild (paths are canvas-size-independent).
- [x] Memory stays under ~50 MB for a typical city.

## Out of scope

- WebGL renderer — overkill, big rewrite.
- Per-element click/hover hit-testing — would need a separate
  hit-detection structure.
- Tile-based caching / drawImage tricks — Path2D path is enough.

---

# Deploy: `server.py` at `shipisnature.com/api/map/`

Path-prefix strategy: **Option A** — reverse proxy strips `/api/map`,
FastAPI routes have no `/api/` prefix, `root_path` is set so the docs
page generates correct public URLs.

```
shipisnature.com/api/map/layers
        │
        ▼  nginx: proxy_pass http://127.0.0.1:8000/;   (strips /api/map)
        │
        ▼  FastAPI sees:  /layers
        │
        ▼  matched by:    @app.get("/layers")
```

Local dev keeps working at `127.0.0.1:8000/layers`.

---

## Must do before deploy

### 1. Drop `/api/` from route decorators
In `server.py`, rename:

- [ ] `@app.get("/api/layers")` → `@app.get("/layers")`
- [ ] `@app.post("/api/place")`  → `@app.post("/place")`
- [ ] `@app.post("/api/data")`   → `@app.post("/data")`
- [ ] `@app.get("/api/health")`  → `@app.get("/health")`

(Update any internal references, MANUAL.md, and the curl examples in the
endpoint docstrings to match.)

### 2. Make `root_path` configurable
In `server.py`:

- [ ] Add `--root-path PATH` CLI flag (default empty `""`).
- [ ] Pass it to `FastAPI(root_path=args.root_path, ...)`.
- [ ] Or, equivalently, pass it through to `uvicorn.run(..., root_path=...)`.

Run locally: `server.py` (no flag) — docs at `127.0.0.1:8000/docs`.
Run in prod: `server.py --root-path /api/map` — docs link to `shipisnature.com/api/map/docs`.

### 3. Lock down CORS
- [ ] Replace `allow_origins=["*"]` with an explicit list, sourced from
      env or CLI: `["https://shipisnature.com"]` (plus any subdomains
      that host the frontend).
- [ ] Keep `*` only when running under a localhost/dev mode flag.

### 4. Trust proxy headers
- [ ] Run uvicorn with `--proxy-headers --forwarded-allow-ips="<proxy IP or *>"`.
- [ ] Without this, all client IPs in logs and rate-limit logic look like the proxy.

### 5. Persistent DB path
- [ ] Pass `--db /var/lib/citymapframes/cache.db` (or wherever the
      deploy mounts persistent storage).
- [ ] Make sure the process user can read+write that directory.
- [ ] Schema is created on first run via `lifespan` → `db.init_db()`.

### 6. Real contact in `User-Agent`
- [ ] Update `USER_AGENT` in `fetch.py` and `geocode.py` to include a
      real email or contact URL.
      Nominatim and Overpass both reserve the right to block requests
      without a real contact.

### 7. nginx config
Add to the `shipisnature.com` server block:

```nginx
location /api/map/ {
    proxy_pass http://127.0.0.1:8000/;     # trailing slash = strip /api/map
    proxy_set_header Host              $host;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host  $host;

    # /api/data can take 30+ s on cache miss
    proxy_read_timeout 120s;
    proxy_send_timeout 120s;
}
```

- [ ] Apply nginx config and reload.
- [ ] Smoke-test:
  ```
  curl https://shipisnature.com/api/map/health
  curl -X POST https://shipisnature.com/api/map/place \
       -H "Content-Type: application/json" \
       -d '{"query":"Lisbon"}'
  ```

### 8. Process supervision
- [ ] systemd unit (or whatever the host uses) that runs:
  ```
  uvicorn server:app \
    --host 127.0.0.1 --port 8000 \
    --root-path /api/map \
    --proxy-headers --forwarded-allow-ips="*" \
    --workers 1
  ```
- [ ] Restart-on-failure enabled.
- [ ] Log capture wired up (uvicorn → journald or file).

---

## Defer until traffic justifies it

### 9. Rate limiting
Right now any caller can spam `/place` and `/data`. One bad actor →
your egress IP gets blocklisted by Nominatim.

- [ ] Add `slowapi` (or similar) middleware.
- [ ] Conservative defaults: e.g. 10 req/min per IP on `/place`,
      30 req/min per IP on `/data`.
- [ ] Consider gating `force=true` on `/data` behind an API key, or
      remove it from the public surface entirely.

### 10. Multi-worker coalescing
`_geocode_inflight` and `_fetch_inflight` are in-process dicts. If you
ever scale to `--workers N > 1`, concurrent requests across workers
will duplicate Overpass calls.

- [ ] Either keep `--workers 1` (fine for low traffic), **or**
- [ ] Move the inflight registry to Redis (`SETNX` + pub/sub for the
      result). Don't bother until you measurably need it.

### 11. Observability
- [ ] Structured logging (request id, IP, anchor, layers, duration).
- [ ] `/health` extension: include version, uptime, last successful
      Overpass timestamp.
- [ ] Prom metrics endpoint if/when there's a dashboard to point at.

### 12. Auth (if the API ever leaves "public read-only" territory)
- [ ] API key header for `force=true` and any future write endpoints.

---

## Smoke-test checklist for first deploy

After all "must do" items land:

- [ ] `curl https://shipisnature.com/api/map/health` → 200, JSON with cache stats
- [ ] `curl https://shipisnature.com/api/map/layers` → 200, layer registry
- [ ] `POST /api/map/place {"query":"Lisbon"}` → 200, anchor JSON
- [ ] `POST /api/map/data` with that anchor + a small layer list → 200
- [ ] `https://shipisnature.com/api/map/docs` loads, "Try it out" buttons hit the public URL (not 127.0.0.1)
- [ ] CORS preflight from `shipisnature.com` succeeds
- [ ] CORS preflight from a random origin is rejected
- [ ] Server logs show real client IPs (not the proxy IP)
- [ ] Restart the service — `cache.db` survives, anchors still cached
