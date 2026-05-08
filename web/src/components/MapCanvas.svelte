<script lang="ts">
  import { onMount } from 'svelte';
  import { app } from '../lib/state.svelte';
  import {
    renderToCanvas,
    debounce,
    panByPixels,
    zoomAroundPoint,
    clampView,
    defaultView,
    makeProjectionContext,
    compileLayer,
    type RenderResult,
    type CompiledLayer,
  } from '../lib/render';

  let canvas: HTMLCanvasElement | undefined = $state(undefined);

  // The last successful render's projection context. We need it to map
  // mouse pixels back to lat/lon for zoom-around-cursor — without it
  // we'd have to recompute it every event.
  let lastResult: RenderResult | null = null;

  // ---------- compiled-path cache ----------
  //
  // Per-layer Path2D objects, built once when data arrives. Coordinates
  // are baked against the anchor's cosLat, so the cache is invalidated
  // whenever data.anchor.bbox changes. Within the same anchor, layers
  // can be added incrementally as the user toggles them on (LayerSidebar
  // fetches missing layers and merges them into app.data.layers).
  let compiled: Map<string, CompiledLayer> = new Map();
  let compiledKey: string | null = null;

  function bboxKey(bbox: [number, number, number, number]): string {
    return bbox.join(',');
  }

  /** Drop the cache (anchor changed) and/or compile any newly-present
   *  layers. Cheap per-call when nothing has changed. */
  function ensureCompiled(): number {
    if (!app.data || !app.registry) return 0;
    const bbox = app.data.anchor.bbox;
    const key = bboxKey(bbox);
    if (key !== compiledKey) {
      compiled = new Map();
      compiledKey = key;
    }
    const cosLat = Math.cos(((bbox[0] + bbox[2]) / 2) * (Math.PI / 180));
    const layerById = new Map(app.registry.layers.map((l) => [l.id, l]));
    let built = 0;
    for (const [lid, elements] of Object.entries(app.data.layers)) {
      if (compiled.has(lid)) continue;
      const layer = layerById.get(lid);
      if (!layer || !elements || elements.length === 0) continue;
      const result = compileLayer(layer, elements, cosLat);
      if (result) {
        compiled.set(lid, result);
        built++;
      }
    }
    return built;
  }

  // RAF coalescing: many pointermove / wheel events may fire per frame.
  // We want at most one redraw per frame.
  let rafPending = false;
  function scheduleRedraw() {
    if (rafPending) return;
    rafPending = true;
    requestAnimationFrame(() => {
      rafPending = false;
      redraw();
    });
  }

  function redraw() {
    if (!canvas) return;
    if (!app.data || !app.registry) return;
    ensureCompiled();
    lastResult = renderToCanvas(canvas, {
      data: app.data,
      registry: app.registry,
      enabledLayers: app.enabledLayers,
      compiled,
      overrides: app.layerOverrides,
      view: app.view,
    });
  }

  const redrawDebounced = debounce(scheduleRedraw, 80);

  onMount(() => {
    const ro = new ResizeObserver(redrawDebounced);
    if (canvas) ro.observe(canvas);
    return () => ro.disconnect();
  });

  // Reactively redraw on any state change that affects the picture.
  $effect(() => {
    app.data;
    app.registry;
    app.enabledLayers;
    app.view;
    app.layerOverrides;
    scheduleRedraw();
  });

  // ---------- input handlers ----------

  function canvasCoords(e: { clientX: number; clientY: number }): [number, number] {
    if (!canvas) return [0, 0];
    const rect = canvas.getBoundingClientRect();
    return [e.clientX - rect.left, e.clientY - rect.top];
  }

  /** Re-derive a projection context if we don't have one cached yet
   *  (e.g. user fired a wheel event before the first render landed).
   *  Cheap to recompute — uses the canvas size + anchor.bbox. */
  function ensureContext() {
    if (lastResult) return lastResult;
    if (!canvas || !app.data?.anchor?.bbox) return null;
    const cssW = canvas.clientWidth;
    const cssH = canvas.clientHeight;
    const projection = makeProjectionContext(app.data.anchor.bbox, cssW, cssH);
    return {
      view: app.view ?? defaultView(app.data.anchor.bbox),
      projection,
      cssW,
      cssH,
    };
  }

  function onWheel(e: WheelEvent) {
    if (!app.data?.anchor?.bbox) return;
    e.preventDefault();
    const ctx = ensureContext();
    if (!ctx) return;
    const [cx, cy] = canvasCoords(e);
    // Continuous zoom for trackpads; e.deltaY can be small (pixels) or
    // large (lines). Normalize so a typical mouse-wheel tick (~100 px)
    // gives a ~1.15× zoom.
    const factor = Math.pow(1.0015, -e.deltaY);
    const next = zoomAroundPoint(
      ctx.view, ctx.projection, ctx.cssW, ctx.cssH, cx, cy, factor,
    );
    app.view = clampView(next, app.data.anchor.bbox);
  }

  let dragging = $state(false);
  let lastPointerX = 0;
  let lastPointerY = 0;
  let pointerId: number | null = null;

  function onPointerDown(e: PointerEvent) {
    if (!app.data?.anchor?.bbox) return;
    if (e.button !== 0) return; // left-click only
    if (!canvas) return;
    canvas.setPointerCapture(e.pointerId);
    pointerId = e.pointerId;
    dragging = true;
    [lastPointerX, lastPointerY] = canvasCoords(e);
  }

  function onPointerMove(e: PointerEvent) {
    if (!dragging || !app.data?.anchor?.bbox) return;
    const [x, y] = canvasCoords(e);
    const dx = x - lastPointerX;
    const dy = y - lastPointerY;
    lastPointerX = x;
    lastPointerY = y;
    if (dx === 0 && dy === 0) return;

    const ctx = ensureContext();
    if (!ctx) return;
    const next = panByPixels(ctx.view, ctx.projection, dx, dy);
    app.view = clampView(next, app.data.anchor.bbox);
  }

  function onPointerUp(e: PointerEvent) {
    if (!dragging) return;
    if (canvas && pointerId !== null && canvas.hasPointerCapture(pointerId)) {
      canvas.releasePointerCapture(pointerId);
    }
    dragging = false;
    pointerId = null;
  }

  function onDoubleClick(e: MouseEvent) {
    if (!app.data?.anchor?.bbox) return;
    const ctx = ensureContext();
    if (!ctx) return;
    const [cx, cy] = canvasCoords(e);
    const next = zoomAroundPoint(
      ctx.view, ctx.projection, ctx.cssW, ctx.cssH, cx, cy, 2,
    );
    app.view = clampView(next, app.data.anchor.bbox);
  }

  function resetView() {
    app.view = null;
  }

  let zoomLabel = $derived(
    app.view ? `${app.view.zoom.toFixed(2)}×` : '1.00×',
  );
  let canResetView = $derived(app.view !== null);
</script>

<div class="canvas-wrap">
  <canvas
    bind:this={canvas}
    onwheel={onWheel}
    onpointerdown={onPointerDown}
    onpointermove={onPointerMove}
    onpointerup={onPointerUp}
    onpointercancel={onPointerUp}
    ondblclick={onDoubleClick}
    class:dragging
  ></canvas>

  {#if app.data}
    <div class="overlay">
      <button
        class="btn"
        onclick={resetView}
        disabled={!canResetView}
        title="Reset view (zoom & pan)"
        aria-label="Reset view"
      >⟲</button>
      <span class="zoom" title="Current zoom level">{zoomLabel}</span>
    </div>
  {/if}

  {#if !app.data && !app.error}
    <div class="placeholder">
      {#if app.loading === 'place'}
        Resolving place…
      {:else if app.loading === 'data'}
        Fetching map data…
      {:else}
        Search for a place to render
      {/if}
    </div>
  {/if}
</div>

<style>
  .canvas-wrap {
    position: relative;
    flex: 1 1 auto;
    background: #0b1020;
    overflow: hidden;
    /* Disable browser's default touch handling so we can implement our own. */
    touch-action: none;
  }
  canvas {
    display: block;
    width: 100%;
    height: 100%;
    cursor: grab;
  }
  canvas.dragging {
    cursor: grabbing;
  }

  .overlay {
    position: absolute;
    top: 0.6rem;
    right: 0.6rem;
    display: flex;
    align-items: center;
    gap: 0.5rem;
    pointer-events: none; /* let children re-enable */
  }
  .overlay > * {
    pointer-events: auto;
  }
  .btn {
    width: 2rem;
    height: 2rem;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: rgba(19, 24, 49, 0.85);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    font-size: 1.05rem;
    cursor: pointer;
    backdrop-filter: blur(4px);
  }
  .btn:hover:not(:disabled) {
    background: rgba(35, 40, 80, 0.95);
  }
  .btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  .zoom {
    font-variant-numeric: tabular-nums;
    font-size: 0.78rem;
    padding: 0.2rem 0.55rem;
    background: rgba(19, 24, 49, 0.85);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    backdrop-filter: blur(4px);
    opacity: 0.85;
  }

  .placeholder {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    color: rgba(255, 255, 255, 0.5);
    font-size: 0.95rem;
    pointer-events: none;
  }
</style>
