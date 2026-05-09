<script lang="ts">
  import { onMount } from 'svelte';
  import { app } from '../lib/state.svelte';
  import { listShares, ApiError } from '../lib/api';
  import {
    applyShareStyleWithFeedback,
    loadShareWithFeedback,
  } from '../lib/share';
  import {
    GALLERY_REFERENCE_ANCHOR,
    getReferenceBundle,
    type ReferenceBundle,
  } from '../lib/gallery';
  import { isFullDesign } from '../lib/types';
  import type { ShareGetResponse } from '../lib/types';
  import Thumbnail from './Thumbnail.svelte';

  interface Props {
    onClose: () => void;
  }
  let { onClose }: Props = $props();

  let items: ShareGetResponse[] = $state([]);
  let bundle: ReferenceBundle | null = $state(null);
  let loading = $state(true);
  let error: string | null = $state(null);

  onMount(async () => {
    if (!app.registry) {
      error = 'Layer registry not loaded yet.';
      loading = false;
      return;
    }
    try {
      // Kick off both in parallel — list is tiny (~10 KB), reference
      // data is the heavy one (~MBs the first time).
      const [listed, ref] = await Promise.all([
        listShares(30),
        getReferenceBundle(app.registry),
      ]);
      items = listed.items;
      bundle = ref;
    } catch (err) {
      error = err instanceof ApiError ? err.message : String(err);
    } finally {
      loading = false;
    }
  });

  // ESC closes the modal; click on the backdrop (not on the panel) too.
  $effect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  function onBackdropClick(e: MouseEvent) {
    if (e.target === e.currentTarget) onClose();
  }

  async function applyStyle(item: ShareGetResponse) {
    onClose();
    await applyShareStyleWithFeedback(item.id);
  }

  async function openOriginal(item: ShareGetResponse, e: Event) {
    e.stopPropagation();
    onClose();
    await loadShareWithFeedback(item.id);
  }
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<!-- Esc-to-dismiss is handled at the window level; the click handler
     is the standard "click outside to close" pattern. -->
<div class="backdrop" onclick={onBackdropClick}>
  <div
    class="panel"
    role="dialog"
    aria-modal="true"
    aria-label="Browse shared designs"
    tabindex="-1"
  >
    <header>
      <div>
        <h2>Browse designs</h2>
        <span class="ref">
          shown on <strong>{GALLERY_REFERENCE_ANCHOR.display_name}</strong>
        </span>
      </div>
      <button class="x" onclick={onClose} aria-label="Close">×</button>
    </header>

    <div class="body">
      {#if loading}
        <div class="status">Loading reference geometry…</div>
      {:else if error}
        <div class="status error">Couldn't load gallery: {error}</div>
      {:else if items.length === 0}
        <div class="status">No designs shared yet.</div>
      {:else if bundle}
        <div class="grid">
          {#each items as item (item.id)}
            <div class="card-wrap">
              <button
                class="card"
                onclick={() => applyStyle(item)}
                title="Apply this style to your map"
              >
                <Thumbnail
                  design={item.design}
                  bundle={bundle}
                />
                <div class="meta">
                  <span class="name" title={item.name}>{item.name}</span>
                  <span class="kind" class:full={isFullDesign(item.design)}>
                    {isFullDesign(item.design) ? 'full' : 'style'}
                  </span>
                </div>
              </button>
              {#if isFullDesign(item.design)}
                <button
                  class="open-original"
                  onclick={(e) => openOriginal(item, e)}
                  title="Load this design's original place + view"
                >
                  Open original
                </button>
              {/if}
            </div>
          {/each}
        </div>
      {/if}
    </div>
  </div>
</div>

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    z-index: 100;
    background: rgba(8, 10, 22, 0.78);
    backdrop-filter: blur(2px);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 2rem 1rem;
  }
  .panel {
    width: min(1100px, 96vw);
    max-height: 92vh;
    display: flex;
    flex-direction: column;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.6);
    overflow: hidden;
  }
  header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    padding: 0.85rem 1rem;
    border-bottom: 1px solid var(--border);
  }
  header h2 {
    margin: 0;
    font-size: 1rem;
    letter-spacing: 0.02em;
  }
  .ref {
    display: block;
    font-size: 0.72rem;
    opacity: 0.65;
    margin-top: 0.15rem;
  }
  .x {
    background: transparent;
    border: none;
    color: var(--text);
    font-size: 1.4rem;
    line-height: 1;
    cursor: pointer;
    opacity: 0.7;
    padding: 0 0.25rem;
  }
  .x:hover {
    opacity: 1;
  }

  .body {
    flex: 1 1 auto;
    overflow-y: auto;
    padding: 1rem;
  }
  .status {
    padding: 2rem;
    text-align: center;
    font-size: 0.9rem;
    opacity: 0.75;
  }
  .status.error {
    color: #ff7676;
  }

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 0.85rem;
  }
  .card-wrap {
    position: relative;
  }
  .card {
    display: flex;
    flex-direction: column;
    width: 100%;
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0.5rem;
    cursor: pointer;
    transition: border-color 100ms;
    font: inherit;
    text-align: left;
  }
  .card:hover,
  .card:focus-visible {
    border-color: var(--accent);
    outline: none;
  }
  .meta {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 0.5rem;
    margin-top: 0.4rem;
  }
  .name {
    font-size: 0.82rem;
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .kind {
    flex: 0 0 auto;
    font-size: 0.65rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 0.05rem 0.35rem;
    border-radius: 3px;
    background: var(--border);
    color: var(--text);
    opacity: 0.75;
  }
  .kind.full {
    background: rgba(90, 219, 160, 0.18);
    color: var(--accent);
    opacity: 1;
  }
  .open-original {
    position: absolute;
    top: 0.5rem;
    right: 0.5rem;
    padding: 0.2rem 0.5rem;
    background: rgba(11, 16, 32, 0.85);
    color: var(--text);
    border: 1px solid var(--accent);
    border-radius: 4px;
    font-size: 0.68rem;
    font-weight: 600;
    cursor: pointer;
    opacity: 0;
    transition: opacity 120ms;
    backdrop-filter: blur(2px);
  }
  .card-wrap:hover .open-original,
  .card-wrap:focus-within .open-original {
    opacity: 1;
  }
</style>
