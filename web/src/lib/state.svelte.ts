// Global reactive state for the app. Uses Svelte 5 runes (`$state`).
// One module-level object so any component can read/mutate it; the
// `$state` proxy makes mutations reactive in any component that reads
// `state.foo`.

import type { Anchor, LayerRegistry, PlaceData } from './types';
import type { View } from './render';

// Mirrors fetch.MIN_RADIUS_KM / MAX_RADIUS_KM / DEFAULT_RADIUS_KM in
// scripts/fetch.py. Keep in sync.
export const RADIUS_MIN = 5;
export const RADIUS_MAX = 50;
export const RADIUS_DEFAULT = 15;

// ---------- layer style overrides ----------
//
// Per-layer user overrides on top of the registry style. Only color
// fields for now; width / dash / opacity may join later. Persisted to
// localStorage so customizations survive a reload.

export interface LayerStyleOverride {
  stroke?: string;
  fill?: string;
  background?: string; // coastline only — the ocean color
  width?: number;      // stroke width in CSS pixels
}

const OVERRIDE_STORAGE_KEY = 'citymapframes:layer-overrides';

function loadOverrides(): Record<string, LayerStyleOverride> {
  if (typeof localStorage === 'undefined') return {};
  try {
    const raw = localStorage.getItem(OVERRIDE_STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function saveOverrides(o: Record<string, LayerStyleOverride>) {
  if (typeof localStorage === 'undefined') return;
  try {
    localStorage.setItem(OVERRIDE_STORAGE_KEY, JSON.stringify(o));
  } catch {
    // quota / disabled storage — silently degrade
  }
}

interface AppState {
  // The layer registry, fetched once at startup.
  registry: LayerRegistry | null;

  // Current resolved place + its data. null until the user searches.
  anchor: Anchor | null;
  data: PlaceData | null;

  // Set of layer ids currently enabled (drawn on the canvas).
  enabledLayers: Set<string>;

  // Half-extent of the synthesized fetch square in km. Anything in
  // [RADIUS_MIN, RADIUS_MAX]; the slider defaults to RADIUS_DEFAULT.
  radiusKm: number;

  // User-controlled view (pan/zoom). null = default framing of the
  // current data.anchor.bbox at zoom 1. Reset on new search and on
  // radius change so a fresh fetch gives a fresh framing.
  view: View | null;

  // Per-layer style overrides. Empty object means "use registry default".
  // Mutations should go through setLayerColor / resetLayerOverride so
  // localStorage stays in sync.
  layerOverrides: Record<string, LayerStyleOverride>;

  // If the current state was loaded from a shared design, this holds
  // that share's id. When the user shares again, it becomes the new
  // share's `parent_id` (lineage / remix tree). Reset on new search.
  parentShareId: string | null;

  // UI state.
  searchQuery: string;
  loading: 'idle' | 'place' | 'data' | 'layers';
  error: string | null;
}

export const app: AppState = $state({
  registry: null,
  anchor: null,
  data: null,
  enabledLayers: new Set<string>(),
  radiusKm: RADIUS_DEFAULT,
  view: null,
  layerOverrides: loadOverrides(),
  parentShareId: null,
  searchQuery: '',
  loading: 'idle',
  error: null,
});

/** Initialize enabledLayers from the registry's `default_visible` flags. */
export function applyDefaultVisibility(registry: LayerRegistry) {
  app.enabledLayers = new Set(
    registry.layers.filter((l) => l.default_visible).map((l) => l.id),
  );
}

export function toggleLayer(id: string) {
  // Reassign so Svelte sees the change (Set mutations alone aren't tracked).
  const next = new Set(app.enabledLayers);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  app.enabledLayers = next;
}

export function setLayersVisibility(ids: string[], visible: boolean) {
  const next = new Set(app.enabledLayers);
  for (const id of ids) {
    if (visible) next.add(id);
    else next.delete(id);
  }
  app.enabledLayers = next;
}

/** Set a single override field on a layer. Generic so the value type
 *  is inferred from the field type (string for colors, number for
 *  width, etc.). Reassigns app.layerOverrides so reactive consumers
 *  re-render, and persists to localStorage. */
export function setLayerStyle<K extends keyof LayerStyleOverride>(
  id: string,
  key: K,
  value: NonNullable<LayerStyleOverride[K]>,
) {
  const next: Record<string, LayerStyleOverride> = {
    ...app.layerOverrides,
    [id]: { ...app.layerOverrides[id], [key]: value },
  };
  app.layerOverrides = next;
  saveOverrides(next);
}

/** Convenience wrapper for color fields (stroke / fill / background). */
export function setLayerColor(
  id: string,
  key: 'stroke' | 'fill' | 'background',
  value: string,
) {
  setLayerStyle(id, key, value);
}

/** Convenience wrapper for width. */
export function setLayerWidth(id: string, value: number) {
  setLayerStyle(id, 'width', value);
}

/** Drop the override for one layer (restore registry defaults). */
export function resetLayerOverride(id: string) {
  if (!app.layerOverrides[id]) return;
  const next = { ...app.layerOverrides };
  delete next[id];
  app.layerOverrides = next;
  saveOverrides(next);
}

/** Drop all overrides. */
export function resetAllOverrides() {
  app.layerOverrides = {};
  saveOverrides({});
}
