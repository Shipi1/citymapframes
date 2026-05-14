// Shared cache of compiled Path2D layers, keyed by the data bbox.
//
// Before this module existed, MapCanvas held a local `compiled` Map and
// ExportButton recompiled from scratch on every export — which on a
// 30k-element place could cost 4+ seconds per click. Now both read from
// here, and the export is gated by the same per-bbox invalidation rule
// MapCanvas already enforced.
//
// Invalidation: keyed by stringified `anchor.bbox`. Whenever the bbox
// changes (new place, new radius, new fetch — anything that synthesizes
// a fresh square around the centroid), the cache is dropped and rebuilt
// lazily on the next ensureCompiled() call.

import { compileLayer, type CompiledLayer } from './render';
import type { LayerRegistry, PlaceData } from './types';

let compiled: Map<string, CompiledLayer> = new Map();
let compiledKey: string | null = null;

function bboxKey(bbox: [number, number, number, number]): string {
  return bbox.join(',');
}

/** Make sure `compiled` covers every layer present in `data.layers` for
 *  the current bbox. Idempotent — cheap when nothing has changed. Returns
 *  the number of layers freshly built this call (0 if everything was hot).
 *
 *  Per-layer compile is incremental: if MapCanvas already built
 *  road_motorway, ExportButton calling ensureCompiled again does
 *  nothing for that layer. New layers (user toggled one on, sidebar
 *  fetched it) get compiled on first appearance.
 */
export function ensureCompiled(
  data: PlaceData,
  registry: LayerRegistry,
): number {
  if (!data.anchor?.bbox) return 0;
  const key = bboxKey(data.anchor.bbox);
  if (key !== compiledKey) {
    compiled = new Map();
    compiledKey = key;
  }
  const cosLat = Math.cos(((data.anchor.bbox[0] + data.anchor.bbox[2]) / 2) * (Math.PI / 180));
  const layerById = new Map(registry.layers.map((l) => [l.id, l]));
  let built = 0;
  for (const [lid, elements] of Object.entries(data.layers)) {
    if (compiled.has(lid)) continue;
    const layer = layerById.get(lid);
    if (!layer || !elements || elements.length === 0) continue;
    const result = compileLayer(layer, elements, cosLat);
    if (result) {
      compiled.set(lid, result);
      built++;
    }
  }
  return built;
}

/** Direct accessor for the shared map. Callers should call
 *  ensureCompiled(...) first to make sure it reflects current data. */
export function getCompiled(): Map<string, CompiledLayer> {
  return compiled;
}

/** Manual reset — wipes the cache. Useful for tests; production code
 *  doesn't need to call this because ensureCompiled() detects bbox
 *  changes automatically. */
export function resetCompiledCache(): void {
  compiled = new Map();
  compiledKey = null;
}
