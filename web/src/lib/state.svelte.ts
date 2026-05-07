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
