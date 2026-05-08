<script lang="ts">
  import { postPlace, postData, ApiError } from '../lib/api';
  import { app } from '../lib/state.svelte';

  let input = $state('');

  async function search() {
    const query = input.trim();
    if (!query) return;
    app.error = null;
    app.searchQuery = query;
    // New place → fresh framing. The previous view (if any) was for a
    // different bbox, keeping it would put the user looking at nothing.
    app.view = null;
    // The user is starting from scratch — this isn't a remix anymore.
    app.parentShareId = null;

    try {
      app.loading = 'place';
      const anchor = await postPlace(query);
      app.anchor = anchor;

      // Pull only the layers currently enabled — saves time on first
      // search, and any toggle later refetches just what's missing.
      app.loading = 'data';
      const enabled = [...app.enabledLayers];
      const data = await postData(anchor, enabled, app.radiusKm);
      app.data = data;
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      app.error = msg;
    } finally {
      app.loading = 'idle';
    }
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === 'Enter') search();
  }

  let busy = $derived(app.loading !== 'idle');
  let label = $derived(
    app.loading === 'place'
      ? 'Geocoding…'
      : app.loading === 'data'
        ? 'Fetching…'
        : 'Search',
  );
</script>

<div class="search-bar">
  <input
    type="text"
    bind:value={input}
    onkeydown={onKey}
    placeholder="Try “Lisbon”, “Cerro Alegre, Valparaíso”, “Tokyo”…"
    disabled={busy}
  />
  <button onclick={search} disabled={busy || !input.trim()}>
    {label}
  </button>
</div>
{#if app.anchor}
  <div class="anchor-info">
    <strong>{app.anchor.name}</strong>
    <span class="muted">
      ({app.anchor.level}, {app.anchor.extent_km.toFixed(1)} km
      {#if app.anchor.bbox_synthesized}· synthesized{/if})
    </span>
  </div>
{/if}
{#if app.error}
  <div class="error">{app.error}</div>
{/if}

<style>
  .search-bar {
    display: flex;
    gap: 0.5rem;
    align-items: stretch;
  }
  input {
    flex: 1 1 auto;
    min-width: 0;
    padding: 0.55rem 0.75rem;
    font-size: 0.95rem;
    background: var(--surface);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
  }
  input:focus {
    outline: 2px solid var(--accent);
    outline-offset: -1px;
  }
  button {
    padding: 0.55rem 1rem;
    background: var(--accent);
    color: #0b1020;
    border: none;
    border-radius: 6px;
    font-weight: 600;
    cursor: pointer;
  }
  button:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }
  .anchor-info {
    margin-top: 0.5rem;
    font-size: 0.85rem;
  }
  .muted {
    opacity: 0.65;
  }
  .error {
    margin-top: 0.5rem;
    color: #ff7676;
    font-size: 0.85rem;
  }
</style>
