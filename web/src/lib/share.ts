// Helpers for receiving / loading a shared design. Mutates `app` state
// in place so the canvas updates without a page reload.
//
// Two callers today:
//   - App.svelte at boot, when ?share=... is in the URL
//   - ShareButton.svelte's "Load" form, when the user pastes a link

import { app } from './state.svelte';
import { ApiError, getShare, postData } from './api';
import { isFullDesign, type Anchor, type SharedDesign } from './types';

/** Build a working Anchor from a SharedDesign's stored anchor data.
 *  Skips Nominatim — the OSM identity is authoritative. bbox/extent_km
 *  are placeholders that get replaced by /api/data's response. */
function anchorFromShared(d: SharedDesign): Anchor {
  return {
    osm_id: d.anchor!.osm_id,
    osm_type: d.anchor!.osm_type,
    name: d.name,
    display_name: d.name,
    level: 'shared',
    lat: d.anchor!.lat,
    lon: d.anchor!.lon,
    bbox: [0, 0, 0, 0],
    extent_km: 2 * d.radiusKm!,
    bbox_synthesized: true,
  };
}

/** Apply a shared design id to `app` state. Two paths:
 *
 *   Full   — has place + view. Replace anchor / radius / layers /
 *            overrides / view, then refetch geometry for the sender's
 *            place at the sender's radius.
 *
 *   Style  — only design. Apply layers + overrides on top of the
 *            recipient's current map. Fetches *only* the diff of
 *            newly-enabled layers; never re-fetches what's already
 *            in `app.data`. If no map is loaded, the design sits in
 *            state and applies on the user's next search.
 *
 * Throws if the id can't be fetched. Caller should set/clear
 * `app.error` and `app.loading` if user-visible feedback is needed.
 */
export async function loadShareById(id: string): Promise<void> {
  const result = await getShare(id);
  const d = result.design;
  app.parentShareId = id;

  if (isFullDesign(d)) {
    app.searchQuery = d.query!;
    app.radiusKm = d.radiusKm!;
    app.enabledLayers = new Set(d.enabledLayers);
    app.layerOverrides = d.overrides ?? {};
    app.view = d.view ?? null;
    const anchor = anchorFromShared(d);
    app.anchor = anchor;
    const data = await postData(
      anchor,
      [...app.enabledLayers],
      app.radiusKm,
    );
    app.data = data;
    return;
  }

  // Style-only: don't touch anchor / radius / view. Apply layer
  // selection + overrides to whatever the user has loaded. If the
  // shared design enables layers we don't have yet, fetch the diff.
  app.enabledLayers = new Set(d.enabledLayers);
  app.layerOverrides = d.overrides ?? {};

  if (app.anchor && app.data) {
    const have = new Set(Object.keys(app.data.layers));
    const missing = [...app.enabledLayers].filter((id) => !have.has(id));
    if (missing.length > 0) {
      const fresh = await postData(app.anchor, missing, app.radiusKm);
      app.data = {
        anchor: fresh.anchor,
        layers: { ...app.data.layers, ...fresh.layers },
      };
    }
  }
}

/** Same as loadShareById, but wraps loading-state and error reporting
 *  for UI callers. Returns true on success, false on failure. */
export async function loadShareWithFeedback(id: string): Promise<boolean> {
  app.loading = 'data';
  app.error = null;
  try {
    await loadShareById(id);
    // Mirror the share id in the address bar so re-sharing / bookmarking
    // works without a reload. Use replaceState (no history entry).
    const qs = new URLSearchParams(window.location.search);
    qs.set('share', id);
    history.replaceState(
      null,
      '',
      `${window.location.pathname}?${qs}`,
    );
    return true;
  } catch (err) {
    const msg = err instanceof ApiError ? err.message : String(err);
    app.error = `Failed to load shared design: ${msg}`;
    return false;
  } finally {
    app.loading = 'idle';
  }
}

/** Apply ONLY the design layer (enabledLayers + overrides) of a share to
 *  the user's current map — even if the share is "full" (has place /
 *  view / radius). Used by the gallery thumbnail click: the user wants
 *  someone else's *style*, not their place.
 *
 *  No place change. No view change. Refetches only the diff of newly-
 *  enabled layers against the user's current data. */
export async function applyShareStyleWithFeedback(
  id: string,
): Promise<boolean> {
  app.loading = 'data';
  app.error = null;
  try {
    const result = await getShare(id);
    const d = result.design;
    app.parentShareId = id;
    app.enabledLayers = new Set(d.enabledLayers);
    app.layerOverrides = d.overrides ?? {};

    if (app.anchor && app.data) {
      const have = new Set(Object.keys(app.data.layers));
      const missing = [...app.enabledLayers].filter((id) => !have.has(id));
      if (missing.length > 0) {
        const fresh = await postData(app.anchor, missing, app.radiusKm);
        app.data = {
          anchor: fresh.anchor,
          layers: { ...app.data.layers, ...fresh.layers },
        };
      }
    }
    return true;
  } catch (err) {
    const msg = err instanceof ApiError ? err.message : String(err);
    app.error = `Failed to apply shared style: ${msg}`;
    return false;
  } finally {
    app.loading = 'idle';
  }
}

/** Pull a share id out of various input forms a user might paste:
 *   - "abc12345"                                 (bare id)
 *   - "https://shipisnature.com/?share=abc12345" (full URL)
 *   - "/?share=abc12345"                         (relative)
 *   - "?share=abc12345"                          (query string only)
 *
 * Returns the id (stripped, alphanumeric only) or null if none found
 * or the parse looks suspicious. */
export function extractShareId(input: string): string | null {
  const trimmed = input.trim();
  if (!trimmed) return null;

  // Try as a URL/query-string first.
  let id: string | null = null;
  try {
    const url = trimmed.startsWith('?')
      ? new URL('http://x' + trimmed)
      : new URL(trimmed, 'http://x');
    id = url.searchParams.get('share');
  } catch {
    // Not a URL — fall through to "bare id" handling.
  }

  // Bare id fallback (no `?`/`/` characters at all).
  if (!id && /^[A-Za-z0-9]+$/.test(trimmed)) id = trimmed;

  if (!id) return null;
  // Server-side ids are 8-char base62; allow up to 16 to be lenient.
  if (!/^[A-Za-z0-9]{1,16}$/.test(id)) return null;
  return id;
}
