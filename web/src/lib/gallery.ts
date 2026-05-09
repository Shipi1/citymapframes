// Reference geometry for gallery thumbnails. Every thumbnail draws the
// same place at the same scope, so designs are compared apples-to-apples
// (only their styling differs).
//
// Pick something photogenic with mixed terrain — coast, urban grid,
// hills, parks. Viña del Mar's downtown checks all four. The osm_id is
// the relation that geocodes from "Viña del Mar"; we pin lat/lon to the
// city's centroid (the central plaza area).

import { compileLayer, type CompiledLayer } from './render';
import { postData } from './api';
import type { Anchor, LayerRegistry, PlaceData } from './types';

// Tight enough to keep the prefetch payload small (downtown +
// coastline, ~3 km half-extent → 6×6 km square), wide enough to make
// thumbnails recognizable. Bumping this multiplies building counts ~4×.
export const GALLERY_REFERENCE_RADIUS_KM = 3;

export const GALLERY_REFERENCE_ANCHOR: Anchor = {
  osm_id: 110804,
  osm_type: 'relation',
  name: 'Viña del Mar',
  display_name: 'Viña del Mar (downtown)',
  level: 'city',
  lat: -33.0245,
  lon: -71.5518,
  bbox: [0, 0, 0, 0], // replaced by /api/data response
  extent_km: 2 * GALLERY_REFERENCE_RADIUS_KM,
  bbox_synthesized: true,
};

// Module-level singletons. The reference data is identical for every
// session; once fetched it's reused for every thumbnail in every gallery
// open. Compiled paths cost ~5-50 ms at this scope and we want them
// shared across all thumbnails.
let referenceData: PlaceData | null = null;
let referenceCompiled: Map<string, CompiledLayer> | null = null;
let referenceFetch: Promise<void> | null = null;

export interface ReferenceBundle {
  data: PlaceData;
  compiled: Map<string, CompiledLayer>;
}

/** Fetch + compile the reference geometry once per session.
 *  Subsequent calls return the cached bundle immediately.
 *
 *  Uses every layer the registry has so any design's enabledLayers can
 *  be drawn without a second fetch. ~30km square in km — small but
 *  enough to show coast + roads + buildings. */
export async function getReferenceBundle(
  registry: LayerRegistry,
): Promise<ReferenceBundle> {
  if (referenceData && referenceCompiled) {
    return { data: referenceData, compiled: referenceCompiled };
  }
  // De-dupe concurrent calls — only one fetch at a time.
  if (referenceFetch) {
    await referenceFetch;
    return { data: referenceData!, compiled: referenceCompiled! };
  }

  referenceFetch = (async () => {
    const allLayerIds = registry.layers.map((l) => l.id);
    const data = await postData(
      GALLERY_REFERENCE_ANCHOR,
      allLayerIds,
      GALLERY_REFERENCE_RADIUS_KM,
    );

    const bbox = data.anchor.bbox;
    const cosLat = Math.cos(((bbox[0] + bbox[2]) / 2) * (Math.PI / 180));
    const compiled = new Map<string, CompiledLayer>();
    for (const [lid, elements] of Object.entries(data.layers)) {
      const layer = registry.layers.find((l) => l.id === lid);
      if (!layer || !elements || elements.length === 0) continue;
      const c = compileLayer(layer, elements, cosLat);
      if (c) compiled.set(lid, c);
    }

    referenceData = data;
    referenceCompiled = compiled;
  })();

  try {
    await referenceFetch;
  } finally {
    referenceFetch = null;
  }
  return { data: referenceData!, compiled: referenceCompiled! };
}
