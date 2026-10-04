<script lang="ts">
  import Hud from './components/Hud.svelte';
  import Panel from './components/Panel.svelte';
  import MapPanel from './components/MapPanel.svelte';
  import RoboCommandPanel from './components/RoboCommandPanel.svelte';
  import TopBar from './components/TopBar.svelte';
  import VehicleStatus from './components/VehicleStatus.svelte';
  import { VEHICLES, type VehicleId } from './lib/config';
  import { ocs, vehicles } from './lib/sources';

  let hudVehicle: VehicleId = $state(VEHICLES[0]);
</script>

<div class="app">
  <TopBar />
  <main class="grid">
    <div class="vehicles">
      {#each VEHICLES as id (id)}
        <Panel title={id} source={vehicles[id]}>
          <VehicleStatus vehicle={id} />
        </Panel>
      {/each}
    </div>

    <div class="hud">
      <Panel title="HUD" source={vehicles[hudVehicle]}>
        <div class="hud-body">
          <div class="tabs">
            {#each VEHICLES as id (id)}
              <button class:active={id === hudVehicle} onclick={() => (hudVehicle = id)}>{id}</button>
            {/each}
          </div>
          <Hud vehicle={hudVehicle} />
        </div>
      </Panel>
    </div>

    <div class="map">
      <Panel title="Map">
        <MapPanel />
      </Panel>
    </div>

    <div class="robocommand">
      <Panel title="RoboCommand (via OCS)" source={ocs}>
        <RoboCommandPanel />
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
  .hud-body {
    height: 100%;
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
  }
  .hud-body > :global(.hud) {
    flex: 1;
    min-height: 0;
  }
  .tabs {
    display: flex;
    gap: 0.3rem;
  }
  .tabs button {
    background: transparent;
    color: var(--muted);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 0.15rem 0.7rem;
    font: inherit;
    font-size: 0.85rem;
    cursor: pointer;
  }
  .tabs button.active {
    color: var(--text);
    border-color: var(--connecting);
  }
  .map {
    grid-area: map;
  }
  .robocommand {
    grid-area: robocommand;
  }
</style>
