<script lang="ts">
  import { app } from '../lib/state.svelte';
  import { createShare, ApiError } from '../lib/api';
  import { extractShareId, loadShareWithFeedback } from '../lib/share';
  import type { SharedDesign } from '../lib/types';

  // The popup goes through three states:
  //   options — pick "style only" / "full" OR paste a link to load
  //   busy    — POSTing or loading
  //   url     — link is ready, showing copy button
  type PopupState =
    | { kind: 'options' }
    | { kind: 'busy' }
    | { kind: 'url'; url: string; deduped: boolean; mode: ShareMode };

  type ShareMode = 'style' | 'full';

  let popup: PopupState | null = $state(null);
  let copied = $state(false);

  // Paste-to-load form state. Lives in the options popup.
  let loadInput = $state('');
  let loadError: string | null = $state(null);
  let parsedLoadId = $derived(extractShareId(loadInput));

  let canShare = $derived(app.anchor !== null && app.loading === 'idle');

  /** Build a SharedDesign for the chosen mode.
   *  - 'style' includes only the design layer (layers + overrides).
   *  - 'full' adds place + viewport so the recipient sees the same map. */
  function buildDesign(mode: ShareMode): SharedDesign {
    const overrides =
      Object.keys(app.layerOverrides).length > 0
        ? app.layerOverrides
        : undefined;

    const design: SharedDesign = {
      schemaVersion: 1,
      name: app.anchor?.name?.slice(0, 80) || 'Untitled',
      enabledLayers: [...app.enabledLayers],
    };
    if (overrides) design.overrides = overrides;

    if (mode === 'full') {
      design.query = app.searchQuery || (app.anchor?.name ?? '');
      design.anchor = {
        osm_type: app.anchor!.osm_type,
        osm_id: app.anchor!.osm_id,
        lat: app.anchor!.lat,
        lon: app.anchor!.lon,
      };
      design.radiusKm = app.radiusKm;
      if (app.view) design.view = app.view;
    }
    return design;
  }

  function openOptions() {
    if (popup?.kind === 'busy') return;
    popup = { kind: 'options' };
    loadInput = '';
    loadError = null;
  }

  async function onLoadShared() {
    loadError = null;
    const id = parsedLoadId;
    if (!id) {
      loadError = "That doesn't look like a valid share link.";
      return;
    }
    popup = { kind: 'busy' };
    const ok = await loadShareWithFeedback(id);
    if (ok) {
      // Success — close the popup. The canvas already redrew via the
      // reactive $effect chain in MapCanvas.
      popup = null;
    } else {
      // loadShareWithFeedback set app.error; surface it locally too.
      loadError = app.error ?? 'Could not load that share.';
      popup = { kind: 'options' };
    }
  }

  function onLoadKey(e: KeyboardEvent) {
    if (e.key === 'Enter') onLoadShared();
  }

  async function postShare(mode: ShareMode) {
    popup = { kind: 'busy' };
    try {
      const design = buildDesign(mode);
      const result = await createShare(
        design,
        app.parentShareId ?? undefined,
      );
      const url = `${window.location.origin}/?share=${result.id}`;
      popup = { kind: 'url', url, deduped: result.deduped, mode };
      // Mirror the share id in the address bar so the page IS the
      // shared link from now on. parentShareId tracks lineage for
      // any future re-shares from this state.
      app.parentShareId = result.id;
      const qs = new URLSearchParams(window.location.search);
      qs.set('share', result.id);
      history.replaceState(null, '', `${window.location.pathname}?${qs}`);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      app.error = `Share failed: ${msg}`;
      popup = null;
    }
  }

  async function onCopy() {
    if (popup?.kind !== 'url') return;
    try {
      await navigator.clipboard.writeText(popup.url);
      copied = true;
      setTimeout(() => (copied = false), 1500);
    } catch {
      const input = document.getElementById(
        'share-url-input',
      ) as HTMLInputElement | null;
      input?.select();
    }
  }

  function onClose() {
    popup = null;
  }

  // Esc + click-outside dismiss the popup at any state.
  $effect(() => {
    if (popup === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') popup = null;
    };
    const onClick = (e: MouseEvent) => {
      const t = e.target as Element | null;
      if (!t || !t.closest('.share-popup, .share-btn')) popup = null;
    };
    let attached = false;
    queueMicrotask(() => {
      window.addEventListener('keydown', onKey);
      window.addEventListener('mousedown', onClick);
      attached = true;
    });
    return () => {
      if (attached) {
        window.removeEventListener('keydown', onKey);
        window.removeEventListener('mousedown', onClick);
      }
    };
  });

  let placeHint = $derived(app.anchor?.name ?? 'this place');
</script>

<div class="share-wrap">
  <button
    class="share-btn"
    class:open={popup !== null}
    onclick={openOptions}
    disabled={popup?.kind === 'busy'}
    title="Share or load a design"
  >
    Share
  </button>

  {#if popup?.kind === 'options'}
    <div class="share-popup" role="dialog" aria-label="Share options">
      <div class="head">
        <span>Share this design</span>
        <button class="x" onclick={onClose} aria-label="Close">×</button>
      </div>
      <button
        class="mode primary"
        onclick={() => postShare('style')}
        disabled={!canShare}
        title={canShare ? '' : 'Search a place first'}
      >
        <span class="mode-title">Style only</span>
        <span class="mode-sub">
          Layer selection + colors. Recipient applies it to the map they're already viewing — no refetch.
        </span>
      </button>
      <button
        class="mode"
        onclick={() => postShare('full')}
        disabled={!canShare}
        title={canShare ? '' : 'Search a place first'}
      >
        <span class="mode-title">Full design</span>
        <span class="mode-sub">
          Includes the place ({placeHint}) and your view. Recipient sees the same map.
        </span>
      </button>

      <div class="divider"><span>or load a design</span></div>

      <div class="load-row">
        <input
          class="load-input"
          type="text"
          bind:value={loadInput}
          onkeydown={onLoadKey}
          placeholder="Paste a share link or id…"
          aria-label="Paste a share link or id"
        />
        <button
          class="load-btn"
          onclick={onLoadShared}
          disabled={!parsedLoadId}
          title={parsedLoadId ? 'Load this shared design' : 'Paste a valid link first'}
        >Load</button>
      </div>
      {#if loadError}
        <div class="load-error">{loadError}</div>
      {/if}
    </div>
  {:else if popup?.kind === 'busy'}
    <div class="share-popup busy" role="status">Sharing…</div>
  {:else if popup?.kind === 'url'}
    <div class="share-popup" role="dialog" aria-label="Share link">
      <div class="head">
        <span>
          {popup.deduped ? 'Existing' : 'New'}
          {popup.mode === 'style' ? 'style' : 'design'} link
        </span>
        <button class="x" onclick={onClose} aria-label="Close">×</button>
      </div>
      <input
        id="share-url-input"
        class="url"
        type="text"
        readonly
        value={popup.url}
        onclick={(e) => (e.currentTarget as HTMLInputElement).select()}
      />
      <button class="copy" onclick={onCopy}>
        {copied ? '✓ Copied' : 'Copy'}
      </button>
    </div>
  {/if}
</div>

<style>
  .share-wrap {
    position: relative;
    flex: 0 0 auto;
  }
  .share-btn {
    padding: 0.55rem 0.95rem;
    background: transparent;
    color: var(--text);
    border: 1px solid var(--accent);
    border-radius: 6px;
    font-weight: 600;
    font-size: 0.85rem;
    cursor: pointer;
  }
  .share-btn:hover:not(:disabled) {
    background: rgba(90, 219, 160, 0.15);
  }
  .share-btn.open {
    background: var(--accent);
    color: #0b1020;
  }
  .share-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .share-popup {
    position: absolute;
    top: calc(100% + 0.5rem);
    right: 0;
    z-index: 50;
    min-width: 22rem;
    max-width: 90vw;
    padding: 0.75rem;
    background: var(--surface);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 8px;
    box-shadow: 0 10px 25px rgba(0, 0, 0, 0.5);
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  .share-popup.busy {
    align-items: center;
    justify-content: center;
    min-height: 4rem;
    font-size: 0.85rem;
    opacity: 0.85;
  }

  .head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 0.78rem;
    opacity: 0.85;
  }
  .x {
    background: transparent;
    border: none;
    color: var(--text);
    font-size: 1.1rem;
    line-height: 1;
    cursor: pointer;
    padding: 0 0.25rem;
    opacity: 0.7;
  }
  .x:hover {
    opacity: 1;
  }

  /* Mode picker buttons. The "primary" style hints that style-only is
   * the recommended common case. */
  .mode {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    text-align: left;
    padding: 0.6rem 0.7rem;
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    cursor: pointer;
    transition: border-color 120ms;
  }
  .mode:hover {
    border-color: var(--accent);
  }
  .mode.primary {
    border-color: var(--accent);
    background: rgba(90, 219, 160, 0.08);
  }
  .mode:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }
  .mode:disabled:hover {
    border-color: var(--border);
  }
  .mode-title {
    font-weight: 600;
    font-size: 0.85rem;
  }
  .mode-sub {
    font-size: 0.74rem;
    opacity: 0.7;
    line-height: 1.3;
  }

  /* Divider with centered label between the share section and the
   * load section. Pure CSS — two lines flanking inline text. */
  .divider {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    margin: 0.15rem 0 0.25rem;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    opacity: 0.55;
  }
  .divider::before,
  .divider::after {
    content: '';
    flex: 1 1 auto;
    height: 1px;
    background: var(--border);
  }

  /* Paste-and-load row: input + Load button side-by-side. */
  .load-row {
    display: flex;
    gap: 0.4rem;
  }
  .load-input {
    flex: 1 1 auto;
    min-width: 0;
    padding: 0.45rem 0.55rem;
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 4px;
    font-family: ui-monospace, Consolas, monospace;
    font-size: 0.78rem;
  }
  .load-input:focus {
    outline: 1px solid var(--accent);
    outline-offset: -1px;
  }
  .load-btn {
    flex: 0 0 auto;
    padding: 0.4rem 0.85rem;
    background: var(--accent);
    color: #0b1020;
    border: none;
    border-radius: 4px;
    font-weight: 600;
    font-size: 0.78rem;
    cursor: pointer;
  }
  .load-btn:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }
  .load-error {
    color: #ff7676;
    font-size: 0.74rem;
    margin-top: -0.1rem;
  }

  .url {
    width: 100%;
    padding: 0.45rem 0.55rem;
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 4px;
    font-family: ui-monospace, Consolas, monospace;
    font-size: 0.78rem;
  }
  .copy {
    align-self: flex-end;
    padding: 0.4rem 0.85rem;
    background: var(--accent);
    color: #0b1020;
    border: none;
    border-radius: 4px;
    font-weight: 600;
    font-size: 0.78rem;
    cursor: pointer;
  }
</style>
