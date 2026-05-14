<script lang="ts">
  import { onMount } from 'svelte';
  import SearchBar from './components/SearchBar.svelte';
  import LayerSidebar from './components/LayerSidebar.svelte';
  import MapCanvas from './components/MapCanvas.svelte';
  import RadiusSlider from './components/RadiusSlider.svelte';
  import ShareButton from './components/ShareButton.svelte';
  import ExportButton from './components/ExportButton.svelte';
  import Gallery from './components/Gallery.svelte';
  import { getLayers, ApiError } from './lib/api';
  import { app, applyDefaultVisibility } from './lib/state.svelte';
  import { loadShareWithFeedback } from './lib/share';
  import { getReferenceBundle } from './lib/gallery';

  let galleryOpen = $state(false);

  onMount(async () => {
    app.loading = 'layers';
    try {
      const reg = await getLayers();
      app.registry = reg;
      applyDefaultVisibility(reg);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      app.error = `Failed to load layer registry: ${msg}`;
    } finally {
      app.loading = 'idle';
    }

    // Prefetch the gallery's reference geometry in the background. By
    // the time the user clicks Browse, the bundle is cached and the
    // modal opens instantly. Concurrent calls (e.g. user clicks Browse
    // mid-prefetch) attach to the same promise via getReferenceBundle's
    // internal de-dup; no double fetch.
    if (app.registry) {
      getReferenceBundle(app.registry).catch((err) => {
        // Non-fatal: gallery will retry on demand if user opens it.
        console.warn('gallery prefetch failed:', err);
      });
    }

    // After the registry is loaded, check for a shared design in the
    // URL and hydrate from it.
    const params = new URLSearchParams(window.location.search);
    const shareId = params.get('share');
    if (shareId) {
      await loadShareWithFeedback(shareId);
    }
  });
</script>

<div class="app">
  <header class="topbar">
    <div class="brand">CityMapFrames</div>
    <div class="search-slot"><SearchBar /></div>
    <RadiusSlider />
    <button
      class="browse-btn"
      onclick={() => (galleryOpen = true)}
      title="Browse shared designs"
    >Browse</button>
    <ShareButton />
    <ExportButton />
  </header>

  <main class="body">
    <LayerSidebar />
    <MapCanvas />
  </main>
</div>

{#if galleryOpen}
  <Gallery onClose={() => (galleryOpen = false)} />
{/if}

<style>
  :global(:root) {
    --bg: #0b1020;
    --surface: #131831;
    --text: #e7e9ee;
    --border: #2a2f4a;
    --accent: #5adba0;
  }
  :global(html, body, #app) {
    margin: 0;
    height: 100%;
    background: var(--bg);
    color: var(--text);
    font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  }
  :global(*) {
    box-sizing: border-box;
  }
  .app {
    display: flex;
    flex-direction: column;
    height: 100vh;
  }
  .topbar {
    display: flex;
    align-items: center;
    gap: 1rem;
    padding: 0.6rem 1rem;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
    flex-direction: column;
    align-items: stretch;
  }
  .brand {
    font-weight: 700;
    letter-spacing: 0.04em;
    font-size: 0.9rem;
    opacity: 0.85;
  }
  .search-slot {
    flex: 1 1 auto;
    min-width: 0;
  }
  .browse-btn {
    flex: 0 0 auto;
    padding: 0.55rem 0.95rem;
    background: transparent;
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    font-weight: 600;
    font-size: 0.85rem;
    cursor: pointer;
  }
  .browse-btn:hover {
    border-color: var(--accent);
  }
  .body {
    display: flex;
    flex: 1 1 auto;
    min-height: 0;
  }

  @media (min-width: 720px) {
    .topbar {
      flex-direction: row;
      align-items: center;
    }
    .brand {
      flex: 0 0 auto;
    }
  }
  @media (max-width: 720px) {
    .body {
      flex-direction: column;
    }
  }
</style>
