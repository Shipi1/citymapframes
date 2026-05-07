# TODO

Two tracks:

1. [Renderer perf](#renderer-perf-path2d--canvas-transform) — switch the
   browser canvas pipeline to pre-built `Path2D` objects + canvas
   transform so pan/zoom stops re-projecting every element per frame.
2. [Deploy](#deploy-serverpy-at-shipisnaturecomapimap) — wire up
   `server.py` at `shipisnature.com/api/map/` behind nginx.

---

# Renderer perf: Path2D + canvas transform

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

- [ ] Pan + zoom on Lisbon at 30 km with `building` enabled is smooth
      (≤16 ms per frame) on a mid-range laptop.
- [ ] Visual output matches the current renderer (within anti-aliasing
      tolerance) at zoom = 1.
- [ ] Layer toggle still works: enabling a previously-disabled layer
      builds its path lazily.
- [ ] Resize and DPR change still trigger a redraw without a path
      rebuild (paths are canvas-size-independent).
- [ ] Memory stays under ~50 MB for a typical city.

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
