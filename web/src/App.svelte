<script lang="ts">
  import { onMount } from 'svelte';
  import SearchBar from './components/SearchBar.svelte';
  import LayerSidebar from './components/LayerSidebar.svelte';
  import MapCanvas from './components/MapCanvas.svelte';
  import RadiusSlider from './components/RadiusSlider.svelte';
  import { getLayers, ApiError } from './lib/api';
  import { app, applyDefaultVisibility } from './lib/state.svelte';

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
  });
</script>

<div class="app">
  <header class="topbar">
    <div class="brand">CityMapFrames</div>
    <div class="search-slot"><SearchBar /></div>
    <RadiusSlider />
  </header>

  <main class="body">
    <LayerSidebar />
    <MapCanvas />
  </main>
</div>

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
