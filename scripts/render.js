// Renders place_data.json (from fetch.py) into a PNG or SVG.
//
// All styling and ordering comes from layers.json — no hardcoded style
// maps in this file. To add or restyle a layer, edit layers.json.
//
// Usage:
//   node render.js                          # in -> place_data.json, out -> roads.png
//   node render.js --svg                    # SVG output instead
//   node render.js my_data.json -o out.png  # custom input/output
//   node render.js --layers ../layers.json  # override registry path
//   node render.js --only road_motorway,building   # render only some layers

const fs = require("fs");
const { createCanvas } = require("canvas");

// --- Config ---
const MAX_DIM = 2048;
const PADDING = 60;
const DEFAULT_OCEAN = "#1a1a2e";

// --- Help text ---
const HELP = `Usage: node render.js [INPUT] [options]

Render a place_data.json file (from fetch.py) to PNG or SVG using styles
and draw order from layers.json.

Arguments:
  INPUT               Path to place_data.json (default: ./place_data.json)
                      Also reads legacy nodes.json (array form) if given.

Options:
  -o, --output PATH   Output file (default: roads.png, or roads.svg with --svg)
  --svg               Write SVG instead of PNG
  --layers PATH       Custom registry file (default: ./layers.json)
  --only IDS          Comma-separated layer ids to render (e.g. road_motorway,building)
                      Defaults to every layer that has data.
  -h, --help          Show this help and exit

Examples:
  node render.js
  node render.js --svg -o map.svg
  node render.js lisbon.json -o lisbon.png
  node render.js --only road_motorway,road_trunk,road_primary
  node render.js --layers layers_dark.json -o dark.png

See MANUAL.md for the full reference.`;

// --- Parse args ---
const args = process.argv.slice(2);
let inputFile = "place_data.json";
let layersFile = "layers.json";
let output = null;
let svgMode = false;
let onlyLayers = null;

for (let i = 0; i < args.length; i++) {
  const a = args[i];
  if (a === "-h" || a === "--help") {
    console.log(HELP);
    process.exit(0);
  } else if (a === "-o" || a === "--output") output = args[++i];
  else if (a === "--svg") svgMode = true;
  else if (a === "--layers") layersFile = args[++i];
  else if (a === "--only") onlyLayers = new Set(args[++i].split(","));
  else if (!a.startsWith("-")) inputFile = a;
  else {
    console.error(`Unknown option: ${a}\nRun 'node render.js --help' for usage.`);
    process.exit(2);
  }
}
if (!output) output = svgMode ? "roads.svg" : "roads.png";

// --- Load registry + data ---
const registry = JSON.parse(fs.readFileSync(layersFile, "utf-8"));
const layerById = Object.fromEntries(registry.layers.map((l) => [l.id, l]));

const raw = JSON.parse(fs.readFileSync(inputFile, "utf-8"));

// Two formats supported:
//   new: { anchor: {...}, layers: { layer_id: [elements] } }
//   old: array of overpass responses (legacy nodes.json) — bucketed by match rules
let layersData = {};
let anchor = null;

if (Array.isArray(raw)) {
  console.warn(
    "Warning: legacy nodes.json format detected. Switch to fetch.py output (place_data.json).",
  );
  const all = raw.flatMap((r) => r.elements || []);
  for (const layer of registry.layers) {
    layersData[layer.id] = all.filter((el) => elementMatchesLayer(el, layer));
  }
} else {
  layersData = raw.layers || {};
  anchor = raw.anchor || null;
}

function typesFromSelector(selector) {
  const prefix = selector.split("[", 1)[0];
  const table = { n: "node", w: "way", r: "relation" };
  const out = new Set();
  for (const c of prefix) if (table[c]) out.add(table[c]);
  return out;
}

function elementMatchesLayer(el, layer) {
  if (!typesFromSelector(layer.selector).has(el.type)) return false;
  const tags = el.tags || {};
  for (const cond of layer.match) {
    if (!(cond.key in tags)) return false;
    const v = tags[cond.key];
    if ("value" in cond && v !== cond.value) return false;
    if ("value_not" in cond && v === cond.value_not) return false;
  }
  return true;
}

// --- Compute projection bbox ---
let minLat, maxLat, minLon, maxLon;
if (anchor && anchor.bbox) {
  // anchor.bbox = [south, west, north, east]
  [minLat, minLon, maxLat, maxLon] = anchor.bbox;
} else {
  minLat = Infinity;
  maxLat = -Infinity;
  minLon = Infinity;
  maxLon = -Infinity;
  for (const lid in layersData) {
    for (const el of layersData[lid]) {
      if (!el.geometry) continue;
      for (const pt of el.geometry) {
        if (pt.lat < minLat) minLat = pt.lat;
        if (pt.lat > maxLat) maxLat = pt.lat;
        if (pt.lon < minLon) minLon = pt.lon;
        if (pt.lon > maxLon) maxLon = pt.lon;
      }
    }
  }
}

const centerLat = (minLat + maxLat) / 2;
const cosLat = Math.cos((centerLat * Math.PI) / 180);
const dataWidth = (maxLon - minLon) * cosLat;
const dataHeight = maxLat - minLat;
const aspect = dataWidth / dataHeight;

let WIDTH, HEIGHT;
if (aspect >= 1) {
  WIDTH = MAX_DIM;
  HEIGHT = Math.max(
    512,
    Math.round((MAX_DIM - 2 * PADDING) / aspect + 2 * PADDING),
  );
} else {
  HEIGHT = MAX_DIM;
  WIDTH = Math.max(
    512,
    Math.round((MAX_DIM - 2 * PADDING) * aspect + 2 * PADDING),
  );
}

const drawW = WIDTH - 2 * PADDING;
const drawH = HEIGHT - 2 * PADDING;
const scale = Math.min(drawW / dataWidth, drawH / dataHeight);
const offsetX = PADDING + (drawW - dataWidth * scale) / 2;
const offsetY = PADDING + (drawH - dataHeight * scale) / 2;

function project(lat, lon) {
  const x = (lon - minLon) * cosLat * scale + offsetX;
  const y = (maxLat - lat) * scale + offsetY; // flip
  return [x, y];
}

console.log(
  `Canvas: ${WIDTH}x${HEIGHT}, aspect ${aspect.toFixed(2)}` +
    (anchor ? `, anchor=${anchor.name}` : ""),
);

// --- Coastline stitching ---
function stitchCoastlines(ways) {
  const segments = ways.map((w) =>
    w.geometry.map((pt) => ({ lat: pt.lat, lon: pt.lon })),
  );
  const chains = [];
  const used = new Set();
  const key = (pt) => `${pt.lat.toFixed(7)},${pt.lon.toFixed(7)}`;

  for (let i = 0; i < segments.length; i++) {
    if (used.has(i)) continue;
    used.add(i);
    let chain = [...segments[i]];
    let changed = true;
    while (changed) {
      changed = false;
      for (let j = 0; j < segments.length; j++) {
        if (used.has(j)) continue;
        const seg = segments[j];
        const chainEnd = key(chain[chain.length - 1]);
        const chainStart = key(chain[0]);
        if (key(seg[0]) === chainEnd) {
          chain.push(...seg.slice(1));
          used.add(j);
          changed = true;
        } else if (key(seg[seg.length - 1]) === chainStart) {
          chain = [...seg.slice(0, -1), ...chain];
          used.add(j);
          changed = true;
        }
      }
    }
    chains.push(chain);
  }
  return chains;
}

function nearestCornerIndex(x, y, corners) {
  let best = 0,
    bestDist = Infinity;
  for (let i = 0; i < corners.length; i++) {
    const d = Math.hypot(x - corners[i][0], y - corners[i][1]);
    if (d < bestDist) {
      bestDist = d;
      best = i;
    }
  }
  return best;
}

function geomToPath(geom) {
  const [sx, sy] = project(geom[0].lat, geom[0].lon);
  let d = `M${sx.toFixed(2)},${sy.toFixed(2)}`;
  for (let i = 1; i < geom.length; i++) {
    const [px, py] = project(geom[i].lat, geom[i].lon);
    d += `L${px.toFixed(2)},${py.toFixed(2)}`;
  }
  return d;
}

function buildLandPath(chain) {
  const corners = [
    [0, 0],
    [WIDTH, 0],
    [WIDTH, HEIGHT],
    [0, HEIGHT],
  ];
  let d = geomToPath(chain);
  const lastPt = project(
    chain[chain.length - 1].lat,
    chain[chain.length - 1].lon,
  );
  const firstPt = project(chain[0].lat, chain[0].lon);
  const startCorner = nearestCornerIndex(lastPt[0], lastPt[1], corners);
  const endCorner = nearestCornerIndex(firstPt[0], firstPt[1], corners);
  let ci = startCorner;
  for (let steps = 0; steps < 4; steps++) {
    d += `L${corners[ci][0]},${corners[ci][1]}`;
    if (ci === endCorner) break;
    ci = (ci + 3) % 4;
  }
  d += "Z";
  return d;
}

function isClosed(geom) {
  return (
    geom.length > 2 &&
    geom[0].lat === geom[geom.length - 1].lat &&
    geom[0].lon === geom[geom.length - 1].lon
  );
}

// --- Determine ocean color (from coastline layer style or fallback) ---
const coastlineLayer = layerById["coastline"];
const oceanColor =
  (coastlineLayer && coastlineLayer.style && coastlineLayer.style.background) ||
  DEFAULT_OCEAN;

// =====================================================================
// SVG path
// =====================================================================
function renderLayerSVG(layer, elements, parts) {
  const style = layer.style || {};
  const kind = layer.kind;

  if (kind === "coastline") {
    const chains = stitchCoastlines(elements);
    for (const chain of chains) {
      parts.push(`<path d="${buildLandPath(chain)}" fill="${style.fill}"/>`);
    }
    return;
  }

  for (const el of elements) {
    if (!el.geometry || el.geometry.length < 2) continue;
    const geom = el.geometry;
    const closed = kind === "polygon" && isClosed(geom);
    const d = geomToPath(geom) + (closed ? "Z" : "");
    const attrs = [];

    if (kind === "line") {
      attrs.push(`fill="none"`);
      attrs.push(`stroke="${style.stroke}"`);
      attrs.push(`stroke-width="${style.width || 1}"`);
      attrs.push(`stroke-linecap="round"`);
      attrs.push(`stroke-linejoin="round"`);
      if (style.dash) attrs.push(`stroke-dasharray="${style.dash.join(",")}"`);
      if (style.opacity != null) attrs.push(`stroke-opacity="${style.opacity}"`);
    } else if (kind === "polygon") {
      attrs.push(closed ? `fill="${style.fill}"` : `fill="none"`);
      if (style.opacity != null) attrs.push(`opacity="${style.opacity}"`);
      if (style.stroke) {
        attrs.push(`stroke="${style.stroke}"`);
        attrs.push(`stroke-width="${style.width || 1}"`);
        attrs.push(`stroke-linecap="round"`);
      }
    } else {
      continue; // unknown kind — skip
    }
    parts.push(`<path d="${d}" ${attrs.join(" ")}/>`);
  }
}

// =====================================================================
// Canvas path
// =====================================================================
function renderLayerCanvas(ctx, layer, elements) {
  const style = layer.style || {};
  const kind = layer.kind;

  if (kind === "coastline") {
    const chains = stitchCoastlines(elements);
    for (const chain of chains) {
      ctx.fillStyle = style.fill;
      ctx.beginPath();
      const [sx, sy] = project(chain[0].lat, chain[0].lon);
      ctx.moveTo(sx, sy);
      for (let i = 1; i < chain.length; i++) {
        const [px, py] = project(chain[i].lat, chain[i].lon);
        ctx.lineTo(px, py);
      }
      const corners = [
        [0, 0],
        [WIDTH, 0],
        [WIDTH, HEIGHT],
        [0, HEIGHT],
      ];
      const lastPt = project(
        chain[chain.length - 1].lat,
        chain[chain.length - 1].lon,
      );
      const firstPt = project(chain[0].lat, chain[0].lon);
      const startCorner = nearestCornerIndex(lastPt[0], lastPt[1], corners);
      const endCorner = nearestCornerIndex(firstPt[0], firstPt[1], corners);
      let ci = startCorner;
      for (let steps = 0; steps < 4; steps++) {
        ctx.lineTo(corners[ci][0], corners[ci][1]);
        if (ci === endCorner) break;
        ci = (ci + 3) % 4;
      }
      ctx.closePath();
      ctx.fill();
    }
    return;
  }

  ctx.lineCap = "round";
  ctx.lineJoin = "round";

  for (const el of elements) {
    if (!el.geometry || el.geometry.length < 2) continue;
    const geom = el.geometry;
    const closed = kind === "polygon" && isClosed(geom);

    ctx.globalAlpha = style.opacity != null ? style.opacity : 1;

    ctx.beginPath();
    const [sx, sy] = project(geom[0].lat, geom[0].lon);
    ctx.moveTo(sx, sy);
    for (let i = 1; i < geom.length; i++) {
      const [px, py] = project(geom[i].lat, geom[i].lon);
      ctx.lineTo(px, py);
    }

    if (kind === "line") {
      ctx.strokeStyle = style.stroke;
      ctx.lineWidth = style.width || 1;
      ctx.setLineDash(style.dash || []);
      ctx.stroke();
    } else if (kind === "polygon") {
      if (closed) {
        ctx.closePath();
        ctx.fillStyle = style.fill;
        ctx.fill();
      }
      if (style.stroke) {
        ctx.strokeStyle = style.stroke;
        ctx.lineWidth = style.width || 1;
        ctx.stroke();
      }
    }
  }
  ctx.setLineDash([]);
  ctx.globalAlpha = 1;
}

// =====================================================================
// Main
// =====================================================================
function shouldRender(layerId) {
  if (onlyLayers && !onlyLayers.has(layerId)) return false;
  const elements = layersData[layerId];
  if (!elements || elements.length === 0) return false;
  if (!layerById[layerId]) return false;
  return true;
}

let totalDrawn = 0;
const drawnLayers = [];

if (svgMode) {
  const svgParts = [];
  svgParts.push(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${WIDTH}" height="${HEIGHT}" viewBox="0 0 ${WIDTH} ${HEIGHT}">`,
  );
  svgParts.push(
    `<rect width="${WIDTH}" height="${HEIGHT}" fill="${oceanColor}"/>`,
  );
  for (const layerId of registry.render_order) {
    if (!shouldRender(layerId)) continue;
    const elements = layersData[layerId];
    renderLayerSVG(layerById[layerId], elements, svgParts);
    totalDrawn += elements.length;
    drawnLayers.push(`${layerId}:${elements.length}`);
  }
  svgParts.push("</svg>");
  fs.writeFileSync(output, svgParts.join("\n"));
} else {
  const canvas = createCanvas(WIDTH, HEIGHT);
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = oceanColor;
  ctx.fillRect(0, 0, WIDTH, HEIGHT);
  for (const layerId of registry.render_order) {
    if (!shouldRender(layerId)) continue;
    const elements = layersData[layerId];
    renderLayerCanvas(ctx, layerById[layerId], elements);
    totalDrawn += elements.length;
    drawnLayers.push(`${layerId}:${elements.length}`);
  }
  fs.writeFileSync(output, canvas.toBuffer("image/png"));
}

console.log(
  `Rendered ${output} (${WIDTH}x${HEIGHT}) — ${drawnLayers.length} layers, ${totalDrawn} elements`,
);
