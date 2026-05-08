<script lang="ts">
  import {
    app,
    toggleLayer,
    setLayersVisibility,
    setLayerColor,
    setLayerWidth,
    resetLayerOverride,
  } from '../lib/state.svelte';
  import { postData, ApiError } from '../lib/api';
  import type { Layer } from '../lib/types';

  /** Group layers by category, preserving render_order within. */
  function groupByCategory(layers: Layer[]): Record<string, Layer[]> {
    const out: Record<string, Layer[]> = {};
    for (const l of layers) {
      (out[l.category] ??= []).push(l);
    }
    return out;
  }

  let groups = $derived(
    app.registry ? groupByCategory(app.registry.layers) : {},
  );
  let categoryLabels = $derived(app.registry?.categories ?? {});

  // ---------- color override helpers ----------

  /** The override fields that are colors (string-typed). LayerStyleOverride
   *  also has `width: number` now, which is handled separately. */
  type ColorKey = 'stroke' | 'fill' | 'background';

  /** Which style field is the layer's "primary" color, depending on
   *  kind. Lines edit stroke; polygons prefer fill but fall back to
   *  stroke. Coastline edits land fill (its ocean/background color is
   *  shown as a second picker below). */
  function primaryColorKey(layer: Layer): ColorKey {
    if (layer.kind === 'line') return 'stroke';
    if (layer.style?.fill) return 'fill';
    return 'stroke';
  }

  /** Get the effective color for a layer's primary key — override
   *  first, registry fallback. Always returns a #rrggbb string suitable
   *  for `<input type="color">` (which doesn't accept named colors). */
  function effectiveColor(layer: Layer, key: ColorKey): string {
    const override = app.layerOverrides[layer.id]?.[key];
    if (override) return toHex(override);
    const fallback = layer.style?.[key];
    return toHex(fallback ?? '#888888');
  }

  /** `<input type="color">` only accepts 7-char #rrggbb. Coerce common
   *  alternative forms into that. */
  function toHex(c: string): string {
    if (c.startsWith('#')) {
      if (c.length === 7) return c.toLowerCase();
      if (c.length === 4) {
        // expand #rgb → #rrggbb
        return ('#' + c[1] + c[1] + c[2] + c[2] + c[3] + c[3]).toLowerCase();
      }
    }
    // give up and return a neutral grey — the canvas keeps using the
    // original (named or rgba) color, only the picker swatch is wrong.
    return '#888888';
  }

  function onColorChange(layer: Layer, key: ColorKey, e: Event) {
    const value = (e.target as HTMLInputElement).value;
    setLayerColor(layer.id, key, value);
  }

  function hasOverride(layer: Layer): boolean {
    return !!app.layerOverrides[layer.id];
  }

  // ---------- width override helpers ----------

  /** Width is only meaningful for layers that actually stroke a path:
   *  every line layer, plus polygon layers whose effective style has a
   *  stroke color. Coastline doesn't stroke. */
  function usesWidth(layer: Layer): boolean {
    if (layer.kind === 'coastline') return false;
    if (layer.kind === 'line') return true;
    // polygon: stroke must be set (registry or override) for width to matter.
    const o = app.layerOverrides[layer.id];
    return !!(o?.stroke ?? layer.style?.stroke);
  }

  function effectiveWidth(layer: Layer): number {
    return (
      app.layerOverrides[layer.id]?.width ??
      layer.style?.width ??
      1
    );
  }

  function onWidthChange(layer: Layer, e: Event) {
    const v = Number((e.target as HTMLInputElement).value);
    if (!Number.isFinite(v) || v <= 0) return;
    setLayerWidth(layer.id, v);
  }

  /** Compact display: integer when whole, one decimal otherwise. */
  function formatWidth(w: number): string {
    return Number.isInteger(w) ? String(w) : w.toFixed(1);
  }

  // ---------- width popup state ----------
  //
  // One popup at a time. `widthPopup` carries the layer it edits plus
  // the viewport coords to render at; null means closed. We manage
  // open/close ourselves rather than using the native popover API,
  // because the API's interaction with our absolutely-positioned CSS
  // produced multiple-stay-open glitches in testing.

  let widthPopup: { layer: Layer; top: number; left: number } | null = $state(null);

  function toggleWidthPopup(layer: Layer, btn: HTMLElement) {
    if (widthPopup?.layer.id === layer.id) {
      widthPopup = null;
      return;
    }
    const rect = btn.getBoundingClientRect();
    widthPopup = {
      layer,
      top: rect.bottom + 4,
      left: rect.left,
    };
  }

  // Close on click-outside and Escape, re-attached only while a popup
  // is open. queueMicrotask ensures the click that opened it doesn't
  // immediately re-close it.
  $effect(() => {
    if (widthPopup === null) return;
    const onClick = (e: MouseEvent) => {
      const t = e.target as Element | null;
      if (!t || !t.closest('.width-popup, .width-btn')) widthPopup = null;
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') widthPopup = null;
    };
    let attached = false;
    queueMicrotask(() => {
      window.addEventListener('mousedown', onClick);
      window.addEventListener('keydown', onKey);
      attached = true;
    });
    return () => {
      if (attached) {
        window.removeEventListener('mousedown', onClick);
        window.removeEventListener('keydown', onKey);
      }
    };
  });

  /** When a layer is enabled but its data isn't loaded yet, fetch it. */
  async function ensureDataForEnabled() {
    if (!app.anchor) return;
    const have = new Set(Object.keys(app.data?.layers ?? {}));
    const missing = [...app.enabledLayers].filter((id) => !have.has(id));
    if (missing.length === 0) return;
    try {
      app.loading = 'data';
      app.error = null;
      const fresh = await postData(app.anchor, missing, app.radiusKm);
      // Merge into existing data — keep already-loaded layers.
      app.data = {
        anchor: fresh.anchor,
        layers: { ...(app.data?.layers ?? {}), ...fresh.layers },
      };
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      app.error = msg;
    } finally {
      app.loading = 'idle';
    }
  }

  function onToggle(id: string) {
    toggleLayer(id);
    if (app.enabledLayers.has(id)) {
      ensureDataForEnabled();
    }
  }

  function toggleCategory(cat: string, enable: boolean) {
    const ids = (groups[cat] ?? []).map((l) => l.id);
    setLayersVisibility(ids, enable);
    if (enable) ensureDataForEnabled();
  }

  function categoryAllOn(cat: string): boolean {
    return (groups[cat] ?? []).every((l) => app.enabledLayers.has(l.id));
  }
</script>

{#if app.registry}
  <aside class="sidebar">
    <h2>Layers</h2>
    {#each Object.keys(groups) as cat (cat)}
      <section>
        <header>
          <h3>{categoryLabels[cat]?.label ?? cat}</h3>
          <button
            class="bulk"
            onclick={() => toggleCategory(cat, !categoryAllOn(cat))}
          >
            {categoryAllOn(cat) ? 'none' : 'all'}
          </button>
        </header>
        <ul>
          {#each groups[cat] as layer (layer.id)}
            <li>
              <label class="row">
                <input
                  type="checkbox"
                  checked={app.enabledLayers.has(layer.id)}
                  onchange={() => onToggle(layer.id)}
                />
                <span class="lid">{layer.id}</span>
                {#if layer.heavy}<span class="heavy" title="heavy: separate Overpass request">⚙</span>{/if}
              </label>
              {#if usesWidth(layer)}
                <button
                  class="width-btn"
                  class:open={widthPopup?.layer.id === layer.id}
                  onclick={(e) => toggleWidthPopup(layer, e.currentTarget)}
                  title="Stroke width: {formatWidth(effectiveWidth(layer))} px (click to edit)"
                  aria-label="Edit stroke width for {layer.id}"
                >{formatWidth(effectiveWidth(layer))}</button>
              {/if}
              <input
                type="color"
                class="picker"
                value={effectiveColor(layer, primaryColorKey(layer))}
                oninput={(e) => onColorChange(layer, primaryColorKey(layer), e)}
                title="Edit {primaryColorKey(layer)} color"
                aria-label="Edit {layer.id} {primaryColorKey(layer)} color"
              />
              {#if layer.kind === 'coastline'}
                <input
                  type="color"
                  class="picker"
                  value={effectiveColor(layer, 'background')}
                  oninput={(e) => onColorChange(layer, 'background', e)}
                  title="Edit ocean color"
                  aria-label="Edit ocean color"
                />
              {/if}
              {#if hasOverride(layer)}
                <button
                  class="reset"
                  onclick={() => resetLayerOverride(layer.id)}
                  title="Reset to default"
                  aria-label="Reset {layer.id} to default"
                >↺</button>
              {/if}
            </li>
          {/each}
        </ul>
      </section>
    {/each}
  </aside>
{/if}

{#if widthPopup}
  <div
    class="width-popup"
    role="dialog"
    aria-label="Stroke width for {widthPopup.layer.id}"
    style:top={`${widthPopup.top}px`}
    style:left={`${widthPopup.left}px`}
  >
    <input
      type="range"
      class="width-slider"
      min="0.25"
      max="20"
      step="0.25"
      value={effectiveWidth(widthPopup.layer)}
      oninput={(e) => onWidthChange(widthPopup!.layer, e)}
      aria-label="Width in pixels"
    />
    <span class="width-value">
      {formatWidth(effectiveWidth(widthPopup.layer))} px
    </span>
  </div>
{/if}

<style>
  .sidebar {
    width: 240px;
    flex: 0 0 240px;
    overflow-y: auto;
    border-right: 1px solid var(--border);
    padding: 1rem;
    background: var(--surface);
  }
  h2 {
    margin: 0 0 0.75rem;
    font-size: 0.95rem;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    opacity: 0.7;
  }
  section {
    margin-bottom: 0.85rem;
  }
  section header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 0.25rem;
  }
  h3 {
    margin: 0;
    font-size: 0.85rem;
    text-transform: capitalize;
  }
  ul {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  li {
    display: flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.15rem 0;
  }
  .row {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    font-size: 0.82rem;
    cursor: pointer;
    flex: 1 1 auto;
    min-width: 0; /* let .lid ellipsis kick in */
  }
  .lid {
    flex: 1 1 auto;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .heavy {
    opacity: 0.6;
    font-size: 0.7rem;
  }
  /* Compact native color picker. Removes most browser chrome so it
   * looks like the small color square it replaces. */
  .picker {
    flex: 0 0 auto;
    width: 1.05rem;
    height: 1.05rem;
    padding: 0;
    border: 1px solid rgba(255, 255, 255, 0.18);
    border-radius: 3px;
    background: transparent;
    cursor: pointer;
    -webkit-appearance: none;
    appearance: none;
    overflow: hidden;
  }
  .picker::-webkit-color-swatch-wrapper {
    padding: 0;
  }
  .picker::-webkit-color-swatch {
    border: none;
    border-radius: 2px;
  }
  .picker::-moz-color-swatch {
    border: none;
    border-radius: 2px;
  }
  /* Compact width button — sits to the left of the color picker. Shows
   * the current width number; clicking opens the popup slider. */
  .width-btn {
    flex: 0 0 auto;
    min-width: 1.4rem;
    height: 1.05rem;
    padding: 0 0.3rem;
    font-size: 0.7rem;
    font-variant-numeric: tabular-nums;
    background: transparent;
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 3px;
    cursor: pointer;
    line-height: 1;
  }
  .width-btn:hover {
    background: rgba(255, 255, 255, 0.05);
  }
  .width-btn.open {
    background: var(--accent);
    color: #0b1020;
    border-color: var(--accent);
  }

  /* Floating popup. Rendered at the component's top level (outside the
   * <aside>) so the overflow container doesn't clip it; positioned via
   * inline style (top/left) computed from the trigger button's bounding
   * rect. Only one is ever mounted at a time — there's nothing for an
   * old one to linger as. */
  .width-popup {
    position: fixed;
    z-index: 50;
    margin: 0;
    padding: 0.5rem 0.6rem;
    background: var(--surface);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    box-shadow: 0 8px 20px rgba(0, 0, 0, 0.45);
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    min-width: 12rem;
  }
  .width-slider {
    width: 100%;
    accent-color: var(--accent);
    cursor: pointer;
  }
  .width-value {
    font-size: 0.72rem;
    font-variant-numeric: tabular-nums;
    opacity: 0.75;
    text-align: right;
  }
  .reset {
    flex: 0 0 auto;
    width: 1.05rem;
    height: 1.05rem;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text);
    border-radius: 3px;
    font-size: 0.75rem;
    line-height: 1;
    cursor: pointer;
    opacity: 0.6;
    padding: 0;
  }
  .reset:hover {
    opacity: 1;
  }
  .bulk {
    background: transparent;
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 4px;
    padding: 0.05rem 0.4rem;
    font-size: 0.7rem;
    cursor: pointer;
    opacity: 0.7;
  }
  .bulk:hover {
    opacity: 1;
  }

  @media (max-width: 720px) {
    .sidebar {
      width: 100%;
      flex: 0 0 auto;
      max-height: 35vh;
      border-right: none;
      border-bottom: 1px solid var(--border);
    }
  }
</style>
