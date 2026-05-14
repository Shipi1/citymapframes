// Export the currently-rendered map to PNG, JPEG, or SVG.
//
// PNG / JPG path
//   Create a detached <canvas>, run renderToCanvas against it with the
//   user's current view, layers, and overrides — but at a higher pixel
//   ratio so line widths scale up proportionally. Then canvas.toBlob().
//
// SVG path
//   Path2D is opaque (you can't extract the `d` attribute), so we walk
//   the raw OSM elements again and emit <path> markup. The projection
//   math mirrors renderToCanvas exactly — same makeProjectionContext,
//   same view, same stitching for coastline.
//
// Layer styling, render order, and the coastline corner-walk are all
// kept in sync with render.ts. If you change rendering rules there,
// mirror them in renderLayerToSvg() below.
//
// File naming includes a UTC timestamp and the anchor's slug.

import {
  defaultView,
  makeProjectionContext,
  nearestCornerIndex,
  renderToCanvas,
  stitchCoastlines,
  type FitMode,
  type RenderOptions,
  type StyleOverride,
  type View,
} from './render';
import type { Layer, OsmElement, OsmPoint, PlaceData } from './types';

const DEFAULT_OCEAN = '#1a1a2e';

// ---------- raster ----------

/** Render the map at `cssW × cssH` CSS pixels and `scale × cssW × cssH`
 *  bitmap pixels (scale=2 → twice as many bitmap pixels per CSS pixel
 *  as the screen). Returns a Blob ready to download. */
export async function exportRaster(
  opts: RenderOptions,
  cssW: number,
  cssH: number,
  scale: number,
  format: 'image/png' | 'image/jpeg',
  quality = 0.92,
): Promise<Blob | null> {
  const bitmapW = Math.round(cssW * scale);
  const bitmapH = Math.round(cssH * scale);
  const megapixels = (bitmapW * bitmapH) / 1_000_000;

  console.group(
    `%cexport ${format.replace('image/', '')} @ ${scale}× ` +
      `(${bitmapW}×${bitmapH} = ${megapixels.toFixed(1)} MP)`,
    'color:#5adba0;font-weight:bold',
  );

  const tAlloc = performance.now();
  const canvas = document.createElement('canvas');
  canvas.width = bitmapW;
  canvas.height = bitmapH;
  console.log(`canvas alloc        ${(performance.now() - tAlloc).toFixed(1)} ms`);

  const tDraw = performance.now();
  const result = renderToCanvas(canvas, {
    ...opts,
    cssW,
    cssH,
    pixelRatio: scale,
  });
  console.log(`renderToCanvas      ${(performance.now() - tDraw).toFixed(1)} ms`);
  if (!result) {
    console.groupEnd();
    return null;
  }

  const tBlob = performance.now();
  const blob = await new Promise<Blob | null>((resolve) => {
    canvas.toBlob(
      (b) => resolve(b),
      format,
      format === 'image/jpeg' ? quality : undefined,
    );
  });
  console.log(`toBlob (${format.replace('image/', '')})       ${(performance.now() - tBlob).toFixed(1)} ms`);
  if (blob) {
    console.log(`blob size           ${(blob.size / 1024 / 1024).toFixed(2)} MB`);
  }
  console.groupEnd();
  return blob;
}

// ---------- SVG ----------

/** Build an SVG string of the same view + layers + overrides. Pure
 *  vector — no rasterization, infinite zoom in any viewer. */
export function exportSvg(opts: RenderOptions, cssW: number, cssH: number): string {
  const {
    data, registry, enabledLayers, overrides = {}, fitMode = 'cover',
  } = opts;
  if (!data.anchor?.bbox) return '';

  const layerById: Record<string, Layer> = Object.fromEntries(
    registry.layers.map((l) => [l.id, l]),
  );

  const projection = makeProjectionContext(data.anchor.bbox, cssW, cssH, fitMode);
  const view = opts.view ?? defaultView(data.anchor.bbox);
  const scale = projection.defaultScale * view.zoom;
  const { cosLat } = projection;

  // lat/lon → SVG pixel (origin top-left, y down — same as canvas).
  const project = (lat: number, lon: number): [number, number] => {
    const x = (lon - view.centerLon) * cosLat * scale + cssW / 2;
    const y = (view.centerLat - lat) * scale + cssH / 2;
    return [x, y];
  };

  // Ocean background
  const coastline = layerById['coastline'];
  const oceanColor =
    overrides['coastline']?.background ??
    coastline?.style?.background ??
    DEFAULT_OCEAN;

  // The four canvas corners projected into SVG pixel space — for the
  // coastline corner-walk. Order matches drawCompiledLayer():
  //   0: top-left   1: top-right   2: bottom-right   3: bottom-left
  const corners: [number, number][] = [
    [0, 0],
    [cssW, 0],
    [cssW, cssH],
    [0, cssH],
  ];

  const out: string[] = [];
  out.push(
    `<?xml version="1.0" encoding="UTF-8" standalone="no"?>`,
    `<svg xmlns="http://www.w3.org/2000/svg" ` +
      `viewBox="0 0 ${fmt(cssW)} ${fmt(cssH)}" ` +
      `width="${fmt(cssW)}" height="${fmt(cssH)}">`,
    // Clip everything to the viewBox so coastline overshoots don't leak.
    `<defs><clipPath id="vb"><rect x="0" y="0" width="${fmt(cssW)}" height="${fmt(cssH)}"/></clipPath></defs>`,
    `<g clip-path="url(#vb)">`,
    `<rect x="0" y="0" width="${fmt(cssW)}" height="${fmt(cssH)}" fill="${escapeAttr(oceanColor)}"/>`,
  );

  for (const layerId of registry.render_order) {
    if (!enabledLayers.has(layerId)) continue;
    const layer = layerById[layerId];
    const elements = data.layers?.[layerId];
    if (!layer || !elements || elements.length === 0) continue;
    out.push(
      renderLayerToSvg(layer, elements, project, corners, overrides[layerId] ?? {}),
    );
  }

  out.push(`</g>`, `</svg>`);
  return out.join('\n');
}

function renderLayerToSvg(
  layer: Layer,
  elements: OsmElement[],
  project: (lat: number, lon: number) => [number, number],
  corners: [number, number][],
  override: StyleOverride,
): string {
  const style = { ...(layer.style ?? {}), ...override };
  const opacity = style.opacity ?? 1;

  if (layer.kind === 'coastline') {
    const fill = style.fill ?? '#0a3d62';
    const chains = stitchCoastlines(elements);
    const paths: string[] = [];
    for (const chain of chains) {
      if (chain.length < 2) continue;
      const projected: [number, number][] = chain.map((p) => project(p.lat, p.lon));
      const first = projected[0];
      const last = projected[projected.length - 1];
      const startCorner = nearestCornerIndex(last[0], last[1], corners);
      const endCorner = nearestCornerIndex(first[0], first[1], corners);

      const d: string[] = [];
      d.push(`M${fmt(first[0])} ${fmt(first[1])}`);
      for (let i = 1; i < projected.length; i++) {
        d.push(`L${fmt(projected[i][0])} ${fmt(projected[i][1])}`);
      }
      // Walk the canvas corners (counterclockwise in canvas pixels =
      // (i+3) % 4 in the corner array) until we reach the chain's start.
      let ci = startCorner;
      for (let steps = 0; steps < 4; steps++) {
        d.push(`L${fmt(corners[ci][0])} ${fmt(corners[ci][1])}`);
        if (ci === endCorner) break;
        ci = (ci + 3) % 4;
      }
      d.push('Z');
      paths.push(
        `<path d="${d.join(' ')}" fill="${escapeAttr(fill)}" fill-rule="nonzero" ` +
          (opacity !== 1 ? `opacity="${fmt(opacity)}" ` : '') +
          `/>`,
      );
    }
    return paths.join('');
  }

  // line / polygon — one big path per layer, like compileLayer().
  const stroke = style.stroke;
  const fill = style.fill;
  const width = style.width ?? 1;
  const dash = style.dash && style.dash.length > 0 ? style.dash.join(',') : null;

  const d: string[] = [];
  for (const el of elements) {
    const g = el.geometry;
    if (!g || g.length < 2) continue;
    const [x0, y0] = project(g[0].lat, g[0].lon);
    d.push(`M${fmt(x0)} ${fmt(y0)}`);
    for (let i = 1; i < g.length; i++) {
      const [x, y] = project(g[i].lat, g[i].lon);
      d.push(`L${fmt(x)} ${fmt(y)}`);
    }
    if (layer.kind === 'polygon' && isClosed(g)) d.push('Z');
  }
  if (d.length === 0) return '';

  const attrs: string[] = [
    `d="${d.join(' ')}"`,
    `fill="${escapeAttr(fill ?? 'none')}"`,
  ];
  if (stroke) {
    attrs.push(`stroke="${escapeAttr(stroke)}"`);
    attrs.push(`stroke-width="${fmt(width)}"`);
    attrs.push(`stroke-linecap="round"`);
    attrs.push(`stroke-linejoin="round"`);
    if (dash) attrs.push(`stroke-dasharray="${dash}"`);
  }
  if (opacity !== 1) attrs.push(`opacity="${fmt(opacity)}"`);
  return `<path ${attrs.join(' ')}/>`;
}

function isClosed(geom: OsmPoint[]): boolean {
  return (
    geom.length > 2 &&
    geom[0].lat === geom[geom.length - 1].lat &&
    geom[0].lon === geom[geom.length - 1].lon
  );
}

// ---------- file naming + download ----------

/** Trigger a browser download. Hides the createObjectURL plumbing. */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoke async — Firefox occasionally aborts the download if we revoke
  // synchronously while it's still latching onto the URL.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/** Build a safe filename from a place name + radius. */
export function exportFilename(data: PlaceData, ext: string): string {
  const name = (data.anchor?.name ?? 'map')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 40) || 'map';
  const ts = new Date()
    .toISOString()
    .replace(/[:T]/g, '-')
    .replace(/\..+$/, '');
  return `citymapframes-${name}-${ts}.${ext}`;
}

// ---------- helpers ----------

/** Round to 2 decimals — enough precision for sub-pixel rendering, way
 *  shorter than full doubles in the SVG output. A 30 km city at 4× export
 *  fits in ~5–10 MB of SVG instead of ~25 MB with raw doubles. */
function fmt(n: number): string {
  return Number.isInteger(n) ? n.toString() : n.toFixed(2);
}

function escapeAttr(s: string): string {
  return s.replace(/[<>&"']/g, (ch) =>
    ch === '<' ? '&lt;'
    : ch === '>' ? '&gt;'
    : ch === '&' ? '&amp;'
    : ch === '"' ? '&quot;'
    : '&#39;',
  );
}

// Re-exports for convenience
export type { FitMode, View };
