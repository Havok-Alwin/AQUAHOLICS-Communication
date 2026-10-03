<script lang="ts">
  import { formatAge, type Source } from '../lib/source';

  let { source }: { source: Source } = $props();
  const status = $derived(source.status);

  const LABEL = { connecting: 'CONNECTING', live: 'LIVE', stale: 'STALE', offline: 'OFFLINE' };
</script>

<span class="badge {$status.state}" title={source.name}>
  <span class="dot"></span>
  {LABEL[$status.state]}
  {#if $status.state !== 'live'}
    <span class="age mono">last update {formatAge($status.ageMs)}</span>
  {/if}
</span>

<style>
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 0.4em;
    padding: 0.15em 0.6em;
    border-radius: 4px;
    font-size: 0.8rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    border: 1px solid currentColor;
    white-space: nowrap;
  }
  .dot {
    width: 0.6em;
    height: 0.6em;
    border-radius: 50%;
    background: currentColor;
  }
  .age {
    font-weight: 400;
    color: var(--muted);
  }
  .live {
    color: var(--live);
  }
  .connecting {
    color: var(--connecting);
  }
  .stale {
    color: var(--stale);
  }
  .offline {
    color: var(--offline);
  }
</style>
