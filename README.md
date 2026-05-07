# CityMapFrames

A small toolchain for fetching and drawing OpenStreetMap city data.
A Python service geocodes a place, pulls OSM features from Overpass,
caches everything to SQLite, and exposes the pipeline over HTTP. A
Svelte/TypeScript frontend draws the result onto a canvas with pan,
zoom, and per-layer toggles.

```
"Cerro Alegre, Valparaíso"
        │
        ▼
┌────────────────────┐                   ┌──────────────┐
│   geocode.py       │◄─── Nominatim     │   cache.db   │
│   place → anchor   │                   │   (SQLite)   │
└──────────┬─────────┘                   │ • geocodes   │
           │                             │ • anchors    │
           ▼                             │ • per-layer  │
┌────────────────────┐                   │   data       │
│   fetch.py         │◄─── Overpass      └──────┬───────┘
│   anchor → layers  │◄────────────────────────┘
└──────────┬─────────┘
           │
           ▼
┌────────────────────┐                   ┌────────────────────┐
│   server.py        │ ── HTTP/JSON ───► │   web/ (Svelte)    │
│   FastAPI          │                   │   render.ts        │
│   /api/place       │                   │   pan / zoom /     │
│   /api/data        │                   │   layer toggles    │
│   /api/layers      │                   └─────────┬──────────┘
└────────────────────┘                             ▼
                                                <canvas>
```

## Run it

```bash
# 1. backend (FastAPI on :8000)
cd scripts
uv sync                          # install Python deps
uv run python db.py init         # create cache.db schema
uv run python server.py

# 2. frontend (Vite dev server on :5173)
cd web
npm install
npm run dev
```

Open `http://localhost:5173`. Search a place ("Lisbon", "Cerro Alegre,
Valparaíso", "Tokyo"), toggle layers, drag and wheel-zoom on the canvas.

## Layout

```
.
├── scripts/         # Python pipeline + FastAPI server (see scripts/MANUAL.md)
│   ├── geocode.py
│   ├── fetch.py
│   ├── render.js          # Node.js PNG/SVG renderer (CLI alternative)
│   ├── discover.py        # tag-discovery helper
│   ├── db.py
│   ├── server.py          # FastAPI HTTP wrapper
│   ├── layers.json        # the layer registry
│   ├── MANUAL.md          # full reference for the scripts + API
│   └── TODO.md            # pending work
└── web/             # Svelte 5 + TypeScript + Vite frontend
    └── src/
        ├── App.svelte
        ├── lib/{api,render,state,types}.ts
        └── components/{SearchBar,LayerSidebar,RadiusSlider,MapCanvas}.svelte
```

For a deep dive on any of the scripts, the cache schema, or the layer
registry format, see [`scripts/MANUAL.md`](scripts/MANUAL.md).
