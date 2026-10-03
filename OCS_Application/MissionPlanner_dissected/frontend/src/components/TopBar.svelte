<script lang="ts">
  import { MOCK } from '../lib/mode';
  import { ocs, vehicleBackend } from '../lib/sources';
  import SourceBadge from './SourceBadge.svelte';
</script>

{#if MOCK}
  <div class="mock-banner">MOCK DATA — NOT A REAL VEHICLE — DEV MODE ONLY</div>
{/if}
<div class="topbar">
  <strong class="title">AQUAHOLICS OCS</strong>
  <span class="mode {MOCK ? 'is-mock' : 'is-real'}">{MOCK ? 'MOCK MODE' : 'REAL MODE'}</span>
  <span class="spacer"></span>
  <span class="link">Vehicle backend <SourceBadge source={vehicleBackend} /></span>
  <span class="link">OCS <SourceBadge source={ocs} /></span>
</div>

<style>
  .mock-banner {
    background: var(--mock);
    color: #000;
    text-align: center;
    font-weight: 800;
    font-size: 1.1rem;
    letter-spacing: 0.08em;
    padding: 0.3rem;
  }
  .topbar {
    display: flex;
    align-items: center;
    gap: 1rem;
    padding: 0.4rem 0.8rem;
    border-bottom: 1px solid var(--border);
    background: var(--panel);
  }
  .title {
    font-size: 1.05rem;
  }
  .mode {
    font-size: 0.8rem;
    font-weight: 700;
    padding: 0.1em 0.5em;
    border-radius: 4px;
  }
  .is-mock {
    background: var(--mock);
    color: #000;
  }
  .is-real {
    border: 1px solid var(--muted);
    color: var(--muted);
  }
  .spacer {
    flex: 1;
  }
  .link {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.85rem;
    color: var(--muted);
  }
</style>
