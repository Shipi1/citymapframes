// Browser canvas renderer. Ported near-verbatim from scripts/render.js
// (the node-canvas API matches the browser API). All styling and draw
// order come from the LayerRegistry — there are no hardcoded styles.

import type {
  Anchor,
  Layer,
  LayerRegistry,
  OsmElement,
  OsmPoint,
  PlaceData,
} from './types';

const DEFAULT_OCEAN = '#1a1a2e';

// ---------- view & projection ----------
//
// The renderer projects from a `View` — a (centerLat, centerLon, zoom)
// triple. zoom = 1 means "the data bbox fills the canvas in cover mode",
// which is the default initial framing. The View is decoupled from the
// data extent (anchor.bbox) so the user can pan and zoom freely without
// disturbing what we fetched from Overpass.

/** How the data bbox is mapped onto the canvas at zoom=1.
 *
 *   contain — fit data inside canvas, letterbox bands. No data lost.
 *   cover   — fill canvas, long axis crops at edge. CSS object-fit: cover.
 */
export type FitMode = 'contain' | 'cover';

/** User-controllable view. Stored in app.view. */
export interface View {
  centerLat: number;
  centerLon: number;
  zoom: number;
}

/** Bounds enforced by clampView. Frontend mirrors these in state.svelte.ts. */
export const MIN_ZOOM = 0.5;
export const MAX_ZOOM = 50;
/** Pan clamp: center stays within ±PAN_CLAMP_FACTOR × half-extent of
 *  anchor.bbox. 1.5 means you can pan a bit past the data into ocean
 *  without losing the data offscreen entirely. */
export const PAN_CLAMP_FACTOR = 1.5;

/** Geometry that's constant per (anchor.bbox, canvas size, fit mode). */
export interface ProjectionContext {
  cosLat: number;        // at anchor center; fixed for the lifetime of the data
  defaultScale: number;  // px per "compensated degree" at zoom=1
  bbox: [number, number, number, number];
}

export function makeProjectionContext(
  bbox: [number, number, number, number],
  canvasW: number,
  canvasH: number,
  fitMode: FitMode = 'cover',
): ProjectionContext {
  const [minLat, minLon, maxLat, maxLon] = bbox;
  const centerLat = (minLat + maxLat) / 2;
  const cosLat = Math.cos((centerLat * Math.PI) / 180);
  const dataWidth = Math.max(1e-9, (maxLon - minLon) * cosLat);
  const dataHeight = Math.max(1e-9, maxLat - minLat);
  const sx = canvasW / dataWidth;
  const sy = canvasH / dataHeight;
  const defaultScale = fitMode === 'cover' ? Math.max(sx, sy) : Math.min(sx, sy);
  return { cosLat, defaultScale, bbox };
}

/** Centered, zoom=1 view that reproduces the original cover-mode framing. */
export function defaultView(bbox: [number, number, number, number]): View {
  const [minLat, minLon, maxLat, maxLon] = bbox;
  return {
    centerLat: (minLat + maxLat) / 2,
    centerLon: (minLon + maxLon) / 2,
    zoom: 1,
  };
}

/** Forward projection: lat/lon → canvas pixel. */
function projectFromView(
  view: View,
  ctx: ProjectionContext,
  canvasW: number,
  canvasH: number,
  lat: number,
  lon: number,
): [number, number] {
  const scale = ctx.defaultScale * view.zoom;
  const x = (lon - view.centerLon) * ctx.cosLat * scale + canvasW / 2;
  const y = (view.centerLat - lat) * scale + canvasH / 2;
  return [x, y];
}

/** Inverse: canvas pixel → lat/lon. Used for zoom-around-cursor. */
export function inverseProjectFromView(
  view: View,
  ctx: ProjectionContext,
  canvasW: number,
  canvasH: number,
  x: number,
  y: number,
): { lat: number; lon: number } {
  const scale = ctx.defaultScale * view.zoom;
  return {
    lat: view.centerLat - (y - canvasH / 2) / scale,
    lon: view.centerLon + (x - canvasW / 2) / (ctx.cosLat * scale),
  };
}

/** Apply a zoom multiplier centered on a canvas pixel. The lat/lon
 *  under the cursor before the zoom stays under the cursor after. */
export function zoomAroundPoint(
  view: View,
  ctx: ProjectionContext,
  canvasW: number,
  canvasH: number,
  cursorX: number,
  cursorY: number,
  factor: number,
): View {
  const newZoom = clamp(view.zoom * factor, MIN_ZOOM, MAX_ZOOM);
  // The lat/lon under the cursor right now:
  const { lat: cLat, lon: cLon } = inverseProjectFromView(
    view, ctx, canvasW, canvasH, cursorX, cursorY,
  );
  // Recompute center so projecting (cLat,cLon) at newZoom hits (cursorX,cursorY).
  const newScale = ctx.defaultScale * newZoom;
  return {
    centerLat: cLat + (cursorY - canvasH / 2) / newScale,
    centerLon: cLon - (cursorX - canvasW / 2) / (ctx.cosLat * newScale),
    zoom: newZoom,
  };
}

/** Pan by a pixel delta. dx/dy are signed canvas pixels; standard
 *  mapping convention (drag right → camera moves west). */
export function panByPixels(
  view: View,
  ctx: ProjectionContext,
  dx: number,
  dy: number,
): View {
  const scale = ctx.defaultScale * view.zoom;
  return {
    centerLat: view.centerLat + dy / scale,            // drag down → north
    centerLon: view.centerLon - dx / (ctx.cosLat * scale), // drag right → west
    zoom: view.zoom,
  };
}

/** Soft-clamp the view's center to within PAN_CLAMP_FACTOR × half-extent
 *  of the anchor's bbox center. Zoom is hard-clamped to MIN/MAX_ZOOM.
 *  Keeps the data from disappearing offscreen entirely. */
export function clampView(view: View, bbox: [number, number, number, number]): View {
  const [minLat, minLon, maxLat, maxLon] = bbox;
  const cLat = (minLat + maxLat) / 2;
  const cLon = (minLon + maxLon) / 2;
  const halfH = (maxLat - minLat) / 2;
  const halfW = (maxLon - minLon) / 2;
  return {
    centerLat: clamp(
      view.centerLat,
      cLat - PAN_CLAMP_FACTOR * halfH,
      cLat + PAN_CLAMP_FACTOR * halfH,
    ),
    centerLon: clamp(
      view.centerLon,
      cLon - PAN_CLAMP_FACTOR * halfW,
      cLon + PAN_CLAMP_FACTOR * halfW,
    ),
    zoom: clamp(view.zoom, MIN_ZOOM, MAX_ZOOM),
  };
}

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

// ---------- compiled (Path2D) layers ----------
//
// Building paths once per data load and re-stroking them every frame
// is several orders of magnitude faster than re-projecting every point
// per frame. The trick:
//
//   1. Store coordinates as (lon × cosLat, lat). cosLat is anchor-fixed.
//      That makes the per-frame transform a simple isotropic affine —
//      line widths stay uniform regardless of latitude.
//   2. At render time, set ctx.setTransform once per frame to map path
//      space → canvas space, then stroke/fill each layer's Path2D.
//
// Coastline is the one wrinkle: its "land polygon" closes by walking
// the canvas corners, which depends on the view. We pre-bake each
// stitched chain as a Path2D and finish the closing per frame using
// inverse-projected canvas corners.

export interface CompiledChain {
  path: Path2D;       // chain in projected coords (no closure)
  firstX: number;     // projected first/last point — for nearest-corner lookup
  firstY: number;
  lastX: number;
  lastY: number;
}

export type CompiledLayer =
  | { kind: 'line'; path: Path2D }
  | { kind: 'polygon'; path: Path2D }
  | { kind: 'coastline'; chains: CompiledChain[] };

/** Pre-compile a single layer to one Path2D (or stitched chains for
 *  coastline). Coordinates are in projected space — `(lon × cosLat, lat)`.
 *  Call once per (layer, data, anchor); reuse on every render. */
export function compileLayer(
  layer: Layer,
  elements: OsmElement[],
  cosLat: number,
): CompiledLayer | null {
  const kind = layer.kind;

  if (kind === 'coastline') {
    const stitched = stitchCoastlines(elements);
    const chains: CompiledChain[] = [];
    for (const chain of stitched) {
      if (chain.length < 2) continue;
      const path = new Path2D();
      const firstX = chain[0].lon * cosLat;
      const firstY = chain[0].lat;
      path.moveTo(firstX, firstY);
      for (let i = 1; i < chain.length; i++) {
        path.lineTo(chain[i].lon * cosLat, chain[i].lat);
      }
      const last = chain[chain.length - 1];
      chains.push({
        path,
        firstX,
        firstY,
        lastX: last.lon * cosLat,
        lastY: last.lat,
      });
    }
    return { kind: 'coastline', chains };
  }

  if (kind === 'line') {
    const path = new Path2D();
    for (const el of elements) {
      const g = el.geometry;
      if (!g || g.length < 2) continue;
      path.moveTo(g[0].lon * cosLat, g[0].lat);
      for (let i = 1; i < g.length; i++) {
        path.lineTo(g[i].lon * cosLat, g[i].lat);
      }
    }
    return { kind: 'line', path };
  }

  if (kind === 'polygon') {
    const path = new Path2D();
    for (const el of elements) {
      const g = el.geometry;
      if (!g || g.length < 2) continue;
      path.moveTo(g[0].lon * cosLat, g[0].lat);
      for (let i = 1; i < g.length; i++) {
        path.lineTo(g[i].lon * cosLat, g[i].lat);
      }
      // closePath() lets the polygon fill cleanly. For an open polygon
      // (rare in OSM building data), this adds a closing edge — visually
      // indistinguishable for our use, simpler than splitting paths.
      if (isClosed(g)) path.closePath();
    }
    return { kind: 'polygon', path };
  }

  return null; // unknown kind (e.g. 'point') — skip
}

// ---------- coastline stitching ----------

function stitchCoastlines(ways: OsmElement[]): OsmPoint[][] {
  const segments = ways
    .filter((w) => w.geometry && w.geometry.length > 1)
    .map((w) => w.geometry!.map((pt) => ({ lat: pt.lat, lon: pt.lon })));

  const chains: OsmPoint[][] = [];
  const used = new Set<number>();
  const key = (pt: OsmPoint) => `${pt.lat.toFixed(7)},${pt.lon.toFixed(7)}`;

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

function nearestCornerIndex(
  x: number,
  y: number,
  corners: [number, number][],
): number {
  let best = 0;
  let bestDist = Infinity;
  for (let i = 0; i < corners.length; i++) {
    const d = Math.hypot(x - corners[i][0], y - corners[i][1]);
    if (d < bestDist) {
      bestDist = d;
      best = i;
    }
  }
  return best;
}

function isClosed(geom: OsmPoint[]): boolean {
  return (
    geom.length > 2 &&
    geom[0].lat === geom[geom.length - 1].lat &&
    geom[0].lon === geom[geom.length - 1].lon
  );
}

// ---------- per-frame drawing of compiled layers ----------
//
// Caller has already set the canvas transform so path-units (lon×cosLat,
// lat) map straight to canvas pixels. We undo that scale for line widths
// and dashes so they remain pixel-accurate at any zoom.

function drawCompiledLayer(
  ctx: CanvasRenderingContext2D,
  layer: Layer,
  compiled: CompiledLayer,
  scale: number,
  cornersInProj: [number, number][],
) {
  const style = layer.style ?? {};
  ctx.globalAlpha = style.opacity ?? 1;

  if (compiled.kind === 'coastline') {
    ctx.fillStyle = style.fill ?? '#0a3d62';
    for (const chain of compiled.chains) {
      const startCorner = nearestCornerIndex(chain.lastX, chain.lastY, cornersInProj);
      const endCorner = nearestCornerIndex(chain.firstX, chain.firstY, cornersInProj);
      // Build a fresh closing path each frame — the chain itself comes
      // pre-baked, we just splice in 0–4 canvas-corner segments.
      const land = new Path2D();
      land.addPath(chain.path);
      let ci = startCorner;
      for (let steps = 0; steps < 4; steps++) {
        land.lineTo(cornersInProj[ci][0], cornersInProj[ci][1]);
        if (ci === endCorner) break;
        ci = (ci + 3) % 4;
      }
      land.closePath();
      ctx.fill(land);
    }
    ctx.globalAlpha = 1;
    return;
  }

  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';

  if (compiled.kind === 'line') {
    ctx.strokeStyle = style.stroke ?? '#000';
    // ctx.lineWidth is in path units (post-transform). Divide by scale
    // so a `width: 4` in the registry renders as 4 CSS pixels regardless
    // of zoom level.
    ctx.lineWidth = (style.width ?? 1) / scale;
    if (style.dash && style.dash.length > 0) {
      ctx.setLineDash(style.dash.map((d) => d / scale));
    } else {
      ctx.setLineDash([]);
    }
    ctx.stroke(compiled.path);
    ctx.globalAlpha = 1;
    return;
  }

  if (compiled.kind === 'polygon') {
    if (style.fill) {
      ctx.fillStyle = style.fill;
      ctx.fill(compiled.path);
    }
    if (style.stroke) {
      ctx.strokeStyle = style.stroke;
      ctx.lineWidth = (style.width ?? 1) / scale;
      ctx.setLineDash([]);
      ctx.stroke(compiled.path);
    }
    ctx.globalAlpha = 1;
    return;
  }
}

// ---------- public entry ----------

export interface RenderOptions {
  data: PlaceData;
  registry: LayerRegistry;
  enabledLayers: Set<string>;
  /** Per-layer pre-compiled paths. The caller is responsible for
   *  invalidating this map when `data.anchor` (specifically its bbox or
   *  centroid, which is what cosLat is derived from) changes — paths
   *  built against an old cosLat won't transform correctly. */
  compiled: Map<string, CompiledLayer>;
  /** User view; null/undefined uses defaultView(bbox). */
  view?: View | null;
  fitMode?: FitMode; // default 'cover'
}

/** Result returned to the caller — useful for hit-testing or for the
 *  pan/zoom handlers that need to inverse-project mouse coords. */
export interface RenderResult {
  view: View;                    // the effective view actually drawn
  projection: ProjectionContext; // for inverseProjectFromView etc.
  cssW: number;
  cssH: number;
}

/** Draw the place into the given canvas. Sizes the bitmap to the
 *  canvas's CSS dimensions × devicePixelRatio. Per-frame work is
 *  O(layers) — pan/zoom redraws don't iterate elements. */
export function renderToCanvas(
  canvas: HTMLCanvasElement,
  opts: RenderOptions,
): RenderResult | null {
  const { data, registry, enabledLayers, compiled, fitMode = 'cover' } = opts;
  const layerById: Record<string, Layer> = Object.fromEntries(
    registry.layers.map((l) => [l.id, l]),
  );

  const cssW = canvas.clientWidth || canvas.width || 800;
  const cssH = canvas.clientHeight || canvas.height || 600;
  const dpr = window.devicePixelRatio || 1;

  const bitmapW = Math.max(1, Math.round(cssW * dpr));
  const bitmapH = Math.max(1, Math.round(cssH * dpr));
  if (canvas.width !== bitmapW) canvas.width = bitmapW;
  if (canvas.height !== bitmapH) canvas.height = bitmapH;

  const ctx2d = canvas.getContext('2d');
  if (!ctx2d) return null;

  // Phase 1 — paint ocean and set up the canvas-pixel clip in identity
  // (DPR-only) space.
  ctx2d.setTransform(dpr, 0, 0, dpr, 0, 0);
  const coastline = layerById['coastline'];
  const oceanColor = coastline?.style?.background ?? DEFAULT_OCEAN;
  ctx2d.fillStyle = oceanColor;
  ctx2d.fillRect(0, 0, cssW, cssH);

  if (!data.anchor?.bbox) return null;

  // ctx.clip() persists in device space across setTransform changes.
  // Set it here once, then switch to view-transform for the layer draws.
  ctx2d.save();
  ctx2d.beginPath();
  ctx2d.rect(0, 0, cssW, cssH);
  ctx2d.clip();

  const projection = makeProjectionContext(data.anchor.bbox, cssW, cssH, fitMode);
  const view = opts.view ?? defaultView(data.anchor.bbox);
  const scale = projection.defaultScale * view.zoom;
  const cosLat = projection.cosLat;

  // Phase 2 — set the view transform: path units (lon×cosLat, lat) →
  // bitmap pixels. We bake DPR in here so any subsequent draw call hits
  // the right resolution without us juggling two transforms.
  //
  //   bitmap_x = dpr * scale * path_x + dpr * (cssW/2 - centerLon*cosLat*scale)
  //   bitmap_y = -dpr * scale * path_y + dpr * (cssH/2 + centerLat*scale)
  ctx2d.setTransform(
    dpr * scale,                                            // a
    0,                                                      // b
    0,                                                      // c
    -dpr * scale,                                           // d (flip y)
    dpr * (cssW / 2 - view.centerLon * cosLat * scale),     // e
    dpr * (cssH / 2 + view.centerLat * scale),              // f
  );

  // Pre-compute the four canvas corners in projected (path) space so
  // the coastline corner-walk knows where to draw to. Same transform
  // inverse, applied to each canvas corner:
  //   path_x = (canvas_x - txX) / scale     → halfW = cssW / (2 * scale)
  //   path_y = (txY  - canvas_y) / scale    → halfH = cssH / (2 * scale)
  const halfWProj = cssW / (2 * scale);
  const halfHProj = cssH / (2 * scale);
  const cxProj = view.centerLon * cosLat;
  const cyProj = view.centerLat;
  const cornersInProj: [number, number][] = [
    [cxProj - halfWProj, cyProj + halfHProj], // top-left in canvas
    [cxProj + halfWProj, cyProj + halfHProj], // top-right
    [cxProj + halfWProj, cyProj - halfHProj], // bottom-right
    [cxProj - halfWProj, cyProj - halfHProj], // bottom-left
  ];

  // Phase 3 — walk render_order, draw each compiled layer.
  for (const layerId of registry.render_order) {
    if (!enabledLayers.has(layerId)) continue;
    const layer = layerById[layerId];
    if (!layer) continue;
    const c = compiled.get(layerId);
    if (!c) continue; // not yet compiled (caller should compile lazily)
    drawCompiledLayer(ctx2d, layer, c, scale, cornersInProj);
  }

  ctx2d.restore();
  return { view, projection, cssW, cssH };
}

/** Simple debounce helper for resize-driven redraws. */
export function debounce<T extends (...args: any[]) => void>(
  fn: T,
  ms = 100,
): (...args: Parameters<T>) => void {
  let h: ReturnType<typeof setTimeout> | null = null;
  return (...args: Parameters<T>) => {
    if (h) clearTimeout(h);
    h = setTimeout(() => fn(...args), ms);
  };
}

// Re-export so consumers can use the bare anchor for projection if needed.
export type { Anchor };
