<script lang="ts">
  import { app, RADIUS_MIN, RADIUS_MAX } from '../lib/state.svelte';
  import { postData, ApiError } from '../lib/api';

  // Debounce config: don't fire a refetch on every pixel of slider drag.
  // 350 ms feels right — long enough that the user has stopped dragging,
  // short enough that the response feels reactive.
  const DEBOUNCE_MS = 350;

  let pending: ReturnType<typeof setTimeout> | null = null;

  /** Refetch every currently enabled layer at the new radius. We discard
   *  the old `data` because every layer's bbox just changed — keeping
   *  partial old data would mix scopes. */
  async function refetchAtNewRadius() {
    if (!app.anchor) return;
    const enabled = [...app.enabledLayers];
    if (enabled.length === 0) {
      // Nothing to draw — just clear stale data so the canvas reflects it.
      app.data = null;
      return;
    }
    try {
      app.loading = 'data';
      app.error = null;
      // Radius change resets the framing — anchor.bbox just changed, so
      // any user-set zoom/pan would point at the wrong region.
      app.view = null;
      const fresh = await postData(app.anchor, enabled, app.radiusKm);
      app.data = fresh;
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      app.error = msg;
    } finally {
      app.loading = 'idle';
    }
  }

  function onInput(e: Event) {
    const value = Number((e.target as HTMLInputElement).value);
    app.radiusKm = value;
    if (pending) clearTimeout(pending);
    pending = setTimeout(() => {
      pending = null;
      refetchAtNewRadius();
    }, DEBOUNCE_MS);
  }

  let busy = $derived(app.loading !== 'idle');
</script>

{#if app.anchor}
  <div class="wrap" title="Half-extent of the fetched OSM square. Most cities only need 30km.">
    <label for="radius">Radius</label>
    <input
      id="radius"
      type="range"
      min={RADIUS_MIN}
      max={RADIUS_MAX}
      step="1"
      value={app.radiusKm}
      oninput={onInput}
      disabled={busy && app.loading !== 'data'}
    />
    <span class="value">{app.radiusKm}<span class="unit">km</span></span>
  </div>
{/if}

<style>
  .wrap {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.82rem;
    flex: 0 0 auto;
  }
  label {
    opacity: 0.7;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-size: 0.7rem;
  }
  input[type='range'] {
    width: 130px;
    accent-color: var(--accent);
    cursor: pointer;
  }
  input[type='range']:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .value {
    min-width: 3.2em;
    font-variant-numeric: tabular-nums;
    font-weight: 600;
  }
  .unit {
    opacity: 0.55;
    margin-left: 0.15em;
    font-weight: 400;
  }
</style>
