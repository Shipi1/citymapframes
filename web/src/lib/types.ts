// Shared types for the API contract. Mirrors the Pydantic models in
// scripts/server.py — keep them in sync.

export interface Anchor {
  osm_id: number;
  osm_type: string;
  name: string;
  display_name: string;
  level: string;
  lat: number;
  lon: number;
  bbox: [number, number, number, number]; // [south, west, north, east]
  extent_km: number;
  bbox_synthesized: boolean;
}

export interface MatchCondition {
  key: string;
  value?: string;
  value_not?: string;
  present?: boolean;
}

export type LayerKind = 'line' | 'polygon' | 'coastline' | 'point';

export interface LayerStyle {
  stroke?: string;
  fill?: string;
  background?: string;
  width?: number;
  dash?: number[];
  opacity?: number;
}

export interface Layer {
  id: string;
  category: string;
  kind: LayerKind;
  selector: string;
  match: MatchCondition[];
  default_visible: boolean;
  heavy: boolean;
  style: LayerStyle;
}

export interface Category {
  label: string;
}

export interface LayerRegistry {
  version: number;
  categories: Record<string, Category>;
  render_order: string[];
  layers: Layer[];
}

export interface OsmPoint {
  lat: number;
  lon: number;
}

export interface OsmElement {
  type: 'node' | 'way' | 'relation';
  id: number;
  tags?: Record<string, string>;
  geometry?: OsmPoint[];
  // node form
  lat?: number;
  lon?: number;
}

export interface PlaceData {
  anchor: Anchor;
  layers: Record<string, OsmElement[]>;
}

// ---------- shared designs (server-side presets) ----------
//
// Mirrors the Pydantic models in server.py. Schema-versioned for forward
// compatibility — bump `schemaVersion` when the structure changes.

export interface SharedAnchor {
  osm_type: string;
  osm_id: number;
  lat: number;
  lon: number;
}

export interface ShareView {
  centerLat: number;
  centerLon: number;
  zoom: number;
}

export interface ShareStyleOverride {
  stroke?: string;
  fill?: string;
  background?: string;
  width?: number;
}

/** Two share kinds, distinguished by presence of place fields:
 *
 *   "full"  — has query + anchor + radiusKm. Recipient sees the exact
 *             map the sender saw. Triggers a fetch on the recipient.
 *   "style" — only enabledLayers + overrides. Recipient applies it to
 *             whatever map they currently have, no refetch.
 *
 * The on-the-wire schema is the same; the place fields are optional. */
export interface SharedDesign {
  schemaVersion: 1;
  name: string;
  // ---- design (always present) ----
  enabledLayers: string[];
  overrides?: Record<string, ShareStyleOverride>;
  // ---- place + viewport (only present in "full" shares) ----
  query?: string;
  anchor?: SharedAnchor;
  radiusKm?: number;
  view?: ShareView;
}

/** Discriminator for a SharedDesign. A design is "full" iff it carries
 *  enough place data for the recipient to reproduce the exact map. */
export function isFullDesign(d: SharedDesign): boolean {
  return (
    d.anchor !== undefined &&
    d.query !== undefined &&
    d.radiusKm !== undefined
  );
}

export interface ShareCreateResponse {
  id: string;
  name: string;
  parent_id?: string | null;
  created_at: number;
  deduped: boolean;
}

export interface ShareGetResponse {
  id: string;
  name: string;
  design: SharedDesign;
  parent_id?: string | null;
  view_count: number;
  created_at: number;
}
