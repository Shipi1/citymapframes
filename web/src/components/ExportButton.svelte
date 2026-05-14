<script lang="ts">
  // Export the current map to PNG, JPEG, or SVG. PNG/JPG honor the
  // resolution multiplier (1× = same bitmap pixels as the on-screen
  // canvas; 4× = print-quality). SVG ignores resolution — it's vector.
  //
  // Click-outside + Esc dismiss matches the ShareButton popup pattern.

  import { app } from '../lib/state.svelte';
  import {
    downloadBlob,
    exportFilename,
    exportRaster,
    exportSvg,
  } from '../lib/export';
  import { ensureCompiled, getCompiled } from '../lib/compiled-cache';

  type Format = 'png' | 'jpg' | 'svg';

  let open = $state(false);
  let format = $state<Format>('png');
  let scale = $state(2);
  let busy = $state(false);
  let error = $state<string | null>(null);

  let root: HTMLElement | undefined = $state();

  // Derived: can we export? Need data + registry + at least one layer on.
  const ready = $derived(
    !!app.data && !!app.registry && app.enabledLayers.size > 0,
  );

  function toggle() {
    open = !open;
    error = null;
  }

  function close() {
    open = false;
    error = null;
  }

  // Click outside the button + popup closes the popup.
  $effect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (!root) return;
      if (!root.contains(e.target as Node)) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
    };
    // queueMicrotask: defer until the click that OPENED the popup
    // finishes bubbling, otherwise we'd immediately close ourselves.
    queueMicrotask(() => {
      window.addEventListener('mousedown', onClick);
      window.addEventListener('keydown', onKey);
    });
    return () => {
      window.removeEventListener('mousedown', onClick);
      window.removeEventListener('keydown', onKey);
    };
  });

  /** Read the on-screen canvas dimensions so the export uses the user's
   *  current framing. Falls back to a sane default if we can't find it. */
  function readCanvasSize(): { cssW: number; cssH: number } {
    const c = document.querySelector<HTMLCanvasElement>('canvas');
    const cssW = c?.clientWidth || 1200;
    const cssH = c?.clientHeight || 800;
    return { cssW, cssH };
  }

  async function doExport() {
    if (!ready || !app.data || !app.registry) return;
    busy = true;
    error = null;
    const tTotal = performance.now();
    try {
      const { cssW, cssH } = readCanvasSize();
      const opts = {
        data: app.data,
        registry: app.registry,
        enabledLayers: app.enabledLayers,
        compiled: new Map(), // exportSvg doesn't need it; exportRaster
                              // gets the real map below
        overrides: app.layerOverrides,
        view: app.view,
      };

      if (format === 'svg') {
        const tSvg = performance.now();
        const svg = exportSvg(opts, cssW, cssH);
        console.log(`exportSvg           ${(performance.now() - tSvg).toFixed(1)} ms (${(svg.length / 1024 / 1024).toFixed(2)} MB)`);
        const blob = new Blob([svg], { type: 'image/svg+xml;charset=utf-8' });
        downloadBlob(blob, exportFilename(app.data, 'svg'));
      } else {
        // Reuse the compiled paths MapCanvas already built. If the user
        // toggled new layers on, ensureCompiled fills in the missing
        // ones; everything else stays cached. Worst case (cold cache):
        // ~4s for a 30k-element place. Typical case: 0ms.
        const tCompile = performance.now();
        ensureCompiled(app.data, app.registry);
        const compiled = getCompiled();
        const tElapsed = performance.now() - tCompile;
        if (tElapsed > 5) {
          console.log(`ensureCompiled (cold) ${tElapsed.toFixed(1)} ms — built ${compiled.size} layers`);
        }

        const mime: 'image/png' | 'image/jpeg' =
          format === 'png' ? 'image/png' : 'image/jpeg';
        const blob = await exportRaster(
          { ...opts, compiled },
          cssW,
          cssH,
          scale,
          mime,
        );
        if (!blob) throw new Error('rendering failed');
        downloadBlob(blob, exportFilename(app.data, format));
      }
      console.log(`%cexport total       ${(performance.now() - tTotal).toFixed(1)} ms`, 'font-weight:bold');
      close();
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      busy = false;
    }
  }

</script>

<div class="wrap" bind:this={root}>
  <button
    class="trigger"
    onclick={toggle}
    disabled={!ready}
    title={ready ? 'Export current map' : 'Search a place first'}
  >Export</button>

  {#if open}
    <div class="popup" role="dialog" aria-label="Export map">
      <div class="row">
        <span class="label">Format</span>
        <div class="seg">
          <button
            class:on={format === 'png'}
            onclick={() => (format = 'png')}
            type="button"
          >PNG</button>
          <button
            class:on={format === 'jpg'}
            onclick={() => (format = 'jpg')}
            type="button"
          >JPG</button>
          <button
            class:on={format === 'svg'}
            onclick={() => (format = 'svg')}
            type="button"
          >SVG</button>
        </div>
      </div>

      <div class="row" class:disabled={format === 'svg'}>
        <span class="label">Resolution</span>
        <div class="seg">
          <button
            class:on={scale === 1}
            onclick={() => (scale = 1)}
            disabled={format === 'svg'}
            type="button"
          >1×</button>
          <button
            class:on={scale === 2}
            onclick={() => (scale = 2)}
            disabled={format === 'svg'}
            type="button"
          >2×</button>
          <button
            class:on={scale === 4}
            onclick={() => (scale = 4)}
            disabled={format === 'svg'}
            type="button"
          >4×</button>
        </div>
      </div>

      {#if error}
        <div class="error">{error}</div>
      {/if}

      <div class="actions">
        <button class="ghost" type="button" onclick={close}>Cancel</button>
        <button
          class="primary"
          type="button"
          onclick={doExport}
          disabled={busy || !ready}
        >
          {busy ? 'Exporting…' : 'Save'}
        </button>
      </div>
    </div>
  {/if}
</div>

<style>
  .wrap {
    position: relative;
    display: inline-block;
  }
  .trigger {
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
  .trigger:hover:not(:disabled) {
    border-color: var(--accent);
  }
  .trigger:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .popup {
    position: absolute;
    top: calc(100% + 6px);
    right: 0;
    z-index: 100;
    min-width: 280px;
    padding: 0.85rem;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    box-shadow: 0 6px 24px rgba(0, 0, 0, 0.4);
    display: flex;
    flex-direction: column;
    gap: 0.7rem;
  }

  .row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.6rem;
  }
  .row.disabled {
    opacity: 0.45;
  }
  .label {
    font-size: 0.8rem;
    opacity: 0.75;
  }

  .seg {
    display: inline-flex;
    border: 1px solid var(--border);
    border-radius: 6px;
    overflow: hidden;
  }
  .seg button {
    padding: 0.35rem 0.7rem;
    background: transparent;
    color: var(--text);
    border: none;
    border-left: 1px solid var(--border);
    font-size: 0.8rem;
    font-weight: 600;
    cursor: pointer;
  }
  .seg button:first-child {
    border-left: none;
  }
  .seg button:disabled {
    cursor: not-allowed;
  }
  .seg button.on {
    background: var(--accent);
    color: #0b1020;
  }
  .seg button:hover:not(:disabled):not(.on) {
    background: rgba(255, 255, 255, 0.04);
  }

  .actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
    margin-top: 0.2rem;
  }
  .actions button {
    padding: 0.4rem 0.9rem;
    border-radius: 6px;
    font-size: 0.8rem;
    font-weight: 600;
    cursor: pointer;
    border: 1px solid var(--border);
  }
  .actions .ghost {
    background: transparent;
    color: var(--text);
  }
  .actions .primary {
    background: var(--accent);
    color: #0b1020;
    border-color: var(--accent);
  }
  .actions button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .error {
    font-size: 0.78rem;
    color: #ff8a8a;
    background: rgba(255, 90, 90, 0.08);
    border: 1px solid rgba(255, 90, 90, 0.3);
    border-radius: 4px;
    padding: 0.35rem 0.5rem;
  }
</style>
