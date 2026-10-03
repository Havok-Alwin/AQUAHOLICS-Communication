<script lang="ts">
  import { LENGTH_UNITS, SPEED_UNITS, displayUnits, type LengthUnit, type SpeedUnit } from '../lib/units';

  // Logic 10: MP's Config > Planner unit choices (speed, altitude, distance). Display only:
  // data from the backend and to RoboCommand stays SI.
  const speeds = Object.keys(SPEED_UNITS) as SpeedUnit[];
  const lengths = Object.keys(LENGTH_UNITS) as LengthUnit[];
</script>

<span class="units" title="Display units (data stays SI)">
  <label>
    Speed
    <select bind:value={$displayUnits.speed}>
      {#each speeds as s (s)}<option value={s}>{s}</option>{/each}
    </select>
  </label>
  <label>
    Alt
    <select bind:value={$displayUnits.alt}>
      {#each lengths as l (l)}<option value={l}>{l}</option>{/each}
    </select>
  </label>
  <label>
    Dist
    <select bind:value={$displayUnits.dist}>
      {#each lengths as l (l)}<option value={l}>{l}</option>{/each}
    </select>
  </label>
</span>

<style>
  .units {
    display: inline-flex;
    gap: 0.6rem;
    font-size: 0.8rem;
    color: var(--muted);
  }
  label {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
  }
  select {
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 4px;
    font: inherit;
    padding: 0.05rem 0.2rem;
  }
</style>
