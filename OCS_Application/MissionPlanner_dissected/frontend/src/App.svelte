<script lang="ts">
  import Panel from './components/Panel.svelte';
  import Placeholder from './components/Placeholder.svelte';
  import TopBar from './components/TopBar.svelte';
  import { VEHICLES } from './lib/config';
  import { ocs, vehicles } from './lib/sources';
</script>

<div class="app">
  <TopBar />
  <main class="grid">
    <div class="vehicles">
      {#each VEHICLES as id (id)}
        <Panel title={id} source={vehicles[id]}>
          <Placeholder text="Status: armed / mode / battery / GPS (logic 1, 6, 7)" />
        </Panel>
      {/each}
    </div>

    <div class="hud">
      <Panel title="HUD">
        <Placeholder text="Attitude HUD, canvas + rAF (logic 0, 5)" />
      </Panel>
    </div>

    <div class="map">
      <Panel title="Map">
        <Placeholder text="Leaflet, local base layer: vehicles, keep-out areas, moving object" />
      </Panel>
    </div>

    <div class="robocommand">
      <Panel title="RoboCommand (via OCS)" source={ocs}>
        <Placeholder text="Connection, preflight, run ID, commands and responses (link C)" />
      </Panel>
    </div>
  </main>
</div>

<style>
  .app {
    display: flex;
    flex-direction: column;
    height: 100%;
  }
  .grid {
    flex: 1;
    min-height: 0;
    display: grid;
    gap: 0.5rem;
    padding: 0.5rem;
    grid-template-columns: minmax(16rem, 1fr) 2fr 2fr;
    grid-template-rows: 3fr 1fr;
    grid-template-areas:
      'vehicles hud map'
      'vehicles robocommand robocommand';
  }
  .grid > div {
    min-height: 0;
    min-width: 0;
    display: flex;
    flex-direction: column;
  }
  .grid > div > :global(.panel) {
    flex: 1;
  }
  .vehicles {
    grid-area: vehicles;
    gap: 0.5rem;
  }
  .hud {
    grid-area: hud;
  }
  .map {
    grid-area: map;
  }
  .robocommand {
    grid-area: robocommand;
  }
</style>
