<script lang="ts">
  // One thumbnail = the gallery's reference geometry painted with one
  // design's overrides. The reference data + compiled paths are shared
  // across every Thumbnail in the modal — only the paint settings
  // change between cards. Cheap to mount many at once.

  import { onMount } from 'svelte';
  import { app } from '../lib/state.svelte';
  import { renderToCanvas } from '../lib/render';
  import type { SharedDesign } from '../lib/types';
  import type { ReferenceBundle } from '../lib/gallery';

  interface Props {
    design: SharedDesign;
    bundle: ReferenceBundle;
  }
  let { design, bundle }: Props = $props();

  let canvas: HTMLCanvasElement | undefined = $state(undefined);

  function draw() {
    if (!canvas || !app.registry) return;
    renderToCanvas(canvas, {
      data: bundle.data,
      registry: app.registry,
      enabledLayers: new Set(design.enabledLayers),
      compiled: bundle.compiled,
      overrides: design.overrides ?? {},
      // Don't honor the design's stored view — every thumbnail uses the
      // default cover framing of the reference bbox so they're visually
      // comparable.
      view: null,
    });
  }

  onMount(() => {
    draw();
  });

  // Redraw if registry or design changes (registry is unlikely to
  // change while modal is open; design changes when the parent reuses
  // the same component instance — Svelte 5 keys keep this rare).
  $effect(() => {
    design;
    bundle;
    draw();
  });
</script>

<canvas bind:this={canvas} class="thumb"></canvas>

<style>
  .thumb {
    display: block;
    width: 100%;
    aspect-ratio: 4 / 3;
    border-radius: 5px;
    background: #0b1020;
  }
</style>
