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
