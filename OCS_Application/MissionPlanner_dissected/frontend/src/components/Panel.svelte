<script lang="ts">
  import type { Snippet } from 'svelte';
  import type { Source } from '../lib/source';
  import SourceBadge from './SourceBadge.svelte';

  let {
    title,
    source,
    children,
  }: { title: string; source?: Source; children?: Snippet } = $props();
</script>

<section class="panel">
  <header>
    <h2>{title}</h2>
    {#if source}<SourceBadge {source} />{/if}
  </header>
  <div class="body">
    {@render children?.()}
  </div>
</section>

<style>
  .panel {
    display: flex;
    flex-direction: column;
    min-height: 0;
    min-width: 0;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 6px;
  }
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    padding: 0.4rem 0.7rem;
    border-bottom: 1px solid var(--border);
  }
  h2 {
    margin: 0;
    font-size: 0.95rem;
    font-weight: 600;
  }
  .body {
    flex: 1;
    min-height: 0;
    padding: 0.6rem 0.7rem;
    overflow: auto;
  }
</style>
