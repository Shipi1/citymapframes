<script lang="ts">
  import { app, toggleLayer, setLayersVisibility } from '../lib/state.svelte';
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
              <label>
                <input
                  type="checkbox"
                  checked={app.enabledLayers.has(layer.id)}
                  onchange={() => onToggle(layer.id)}
                />
                <span
                  class="swatch"
                  style:background={layer.style?.stroke ?? layer.style?.fill ?? 'transparent'}
                ></span>
                <span class="lid">{layer.id}</span>
                {#if layer.heavy}<span class="heavy" title="heavy: separate Overpass request">⚙</span>{/if}
              </label>
            </li>
          {/each}
        </ul>
      </section>
    {/each}
  </aside>
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
    padding: 0.15rem 0;
  }
  label {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    font-size: 0.82rem;
    cursor: pointer;
  }
  .swatch {
    display: inline-block;
    width: 0.7rem;
    height: 0.7rem;
    border-radius: 2px;
    border: 1px solid rgba(255, 255, 255, 0.15);
    flex-shrink: 0;
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
