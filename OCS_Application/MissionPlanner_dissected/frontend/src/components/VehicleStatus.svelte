<script lang="ts">
  import { flightPhase, robotState } from '../lib/autonomy';
  import { GROUP_LABEL, bindingsFor, formatBinding, type Binding, type Group } from '../lib/bindings';
  import { VEHICLE_TYPE, type VehicleId } from '../lib/config';
  import { formatAge } from '../lib/source';
  import { vehicles } from '../lib/sources';
  import { severityLevel, severityName } from '../lib/severity';
  import { displayUnits, toDisplay, type UnitKind } from '../lib/units';
  import { attitude, vehicleLog, vehicleParams, vehicleState } from '../lib/telemetry';
  import { batteryLevel, batteryThresholds, warnings } from '../lib/warnings';

  let { vehicle }: { vehicle: VehicleId } = $props();

  const type = $derived(VEHICLE_TYPE[vehicle]);
  const status = $derived(vehicles[vehicle].status);
  const cs = $derived(vehicleState[vehicle]);
  const live = $derived($status.state === 'live');
  const hasData = $derived(Object.keys($cs).length > 0);

  const state = $derived(robotState(type, $cs));
  const phase = $derived(flightPhase($cs));
  const params = $derived(vehicleParams[vehicle]);
  const battery = $derived(batteryLevel($cs, batteryThresholds($params)));
  const active = $derived(warnings($cs, battery));
  const log = $derived(vehicleLog[vehicle]);
  const LOG_SHOWN = 50;
  const clock = (ms: number) => new Date(ms).toLocaleTimeString([], { hour12: false });
  // Heading from the attitude track (FAST channel), sampled whenever the SLOW snapshot updates.
  const heading = $derived.by(() => {
    void $cs;
    const track = attitude[vehicle];
    return track.received ? (((track.shown.yaw % 360) + 360) % 360).toFixed(0) + '°' : '—';
  });

  const GPS_FIX = ['No GPS', 'No fix', '2D', '3D', 'DGPS', 'RTK float', 'RTK fixed', 'Static', 'PPP'];
  const n = (v: number | undefined, dp: number, unit = '') =>
    v === undefined || !Number.isFinite(v) ? '—' : `${v.toFixed(dp)}${unit}`;
  // Logic 10: SI from the backend -> operator's display unit.
  const u = (v: number | undefined, kind: UnitKind, dp: number) =>
    v === undefined ? '—' : n(toDisplay($displayUnits, kind, v), dp, ' ' + $displayUnits[kind]);

  const groups = $derived(
    Object.entries(
      bindingsFor(type).reduce<Record<string, Binding[]>>((acc, b) => {
        (acc[b.group] ??= []).push(b);
        return acc;
      }, {}),
    ) as [Group, Binding[]][],
  );
</script>

{#if !hasData}
  <div class="empty">No status received</div>
{:else}
  {#if !live}
    <div class="not-live">Last known values ({formatAge($status.ageMs)}): NOT LIVE</div>
  {/if}

  <div class="card" class:dim={!live}>
    <!-- 1. Autonomy state: what RobotCommand sees in the heartbeat -->
    <div class="state-row">
      {#if $cs.armed === false}
        <span class="pill disarmed">DISARMED</span>
      {:else}
        <span class="pill {state.toLowerCase()}">{state}</span>
      {/if}
      <span class="mode">mode <b class="mono">{$cs.mode ?? '—'}</b></span>
      {#if type === 'UAV'}
        <span class="pill phase {phase.toLowerCase()}">{phase}</span>
      {/if}
    </div>
    <div class="task">Task <b>—</b> <span class="hint">(set by OCS, not wired yet)</span></div>

    <!-- 2. Warnings (logic 6): only when something is wrong -->
    <div class="alerts">
      {#each active as w (w.id)}
        <span class="alert {w.level}">{w.text}</span>
      {:else}
        <span class="ok">No warnings</span>
      {/each}
    </div>

    <!-- 3. Key numbers -->
    <div class="tiles">
      <div class="tile batt-{battery}">
        <span class="k">Battery</span>
        <span class="v mono">{n($cs.battery_voltage, 1, ' V')}</span>
        <span class="s mono"
          >{n($cs.battery_remaining, 0, ' %')}{battery === 'unknown' && $cs.battery_voltage !== undefined
            ? ' · limits unknown'
            : ''}</span
        >
      </div>
      <div class="tile">
        <span class="k">GPS</span>
        <span class="v mono">{$cs.gpsstatus === undefined ? '—' : (GPS_FIX[$cs.gpsstatus] ?? $cs.gpsstatus)}</span>
        <span class="s mono">{n($cs.satcount, 0, ' sats')}</span>
      </div>
      <div class="tile">
        <span class="k">Link</span>
        <span class="v mono">{n($cs.linkqualitygcs, 0, ' %')}</span>
      </div>
      <div class="tile">
        <span class="k">Speed</span>
        <span class="v mono">{u($cs.groundspeed, 'speed', 1)}</span>
      </div>
      <div class="tile">
        <span class="k">Heading</span>
        <span class="v mono">{heading}</span>
      </div>
      {#if type === 'UAV'}
        <div class="tile">
          <span class="k">Altitude</span>
          <span class="v mono">{u($cs.alt, 'alt', 1)}</span>
        </div>
      {:else}
        <div class="tile">
          <span class="k">To WP</span>
          <span class="v mono">{u($cs.wp_dist, 'dist', 0)}</span>
          <span class="s mono">WP {n($cs.wpno, 0)}</span>
        </div>
      {/if}
    </div>

    <!-- Vehicle's high-priority message (logic 7): coloured by MAV_SEVERITY, clears 10 s after it was set -->
    {#if $cs.messageHigh}
      <div
        class="message {severityLevel($cs.messageHighSeverity ?? 0)}"
        title="{severityName($cs.messageHighSeverity ?? 0)}: {$cs.messageHigh}"
      >
        {$cs.messageHigh}
      </div>
    {:else}
      <div class="message none">No vehicle message</div>
    {/if}

    <!-- 4. Everything else from the binding map, out of the way -->
    <details>
      <summary>All telemetry</summary>
      <div class="groups">
        {#each groups as [group, rows] (group)}
          <div class="group">
            <h3>{GROUP_LABEL[group]}</h3>
            <dl>
              {#each rows as b (b.cs)}
                <dt title="{b.mp} ← CurrentState.{b.cs}">{b.label}</dt>
                <dd class="mono">{formatBinding(b, $cs[b.cs], $displayUnits)}</dd>
              {/each}
            </dl>
          </div>
        {/each}
      </div>
    </details>

    <details>
      <summary>Messages ({$log.length})</summary>
      <ol class="log mono">
        {#each $log.slice(-LOG_SHOWN).reverse() as e, i ($log.length - i)}
          <li class={severityLevel(e.severity)}>
            <span class="time">{clock(e.at)}</span>
            <span class="sev">{severityName(e.severity)}</span>
            <span class="text">{e.text}</span>
          </li>
        {:else}
          <li class="none">No messages yet</li>
        {/each}
      </ol>
    </details>
  </div>
{/if}

<style>
  .empty {
    color: var(--muted);
    font-size: 0.85rem;
  }
  .not-live {
    background: var(--stale);
    color: #000;
    font-weight: 700;
    font-size: 0.8rem;
    padding: 0.2rem 0.5rem;
    border-radius: 4px;
    margin-bottom: 0.4rem;
  }
  .card {
    display: flex;
    flex-direction: column;
    gap: 0.45rem;
  }
  .dim {
    opacity: 0.45;
    font-style: italic;
  }

  .state-row {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
  .pill {
    font-weight: 800;
    font-size: 1.05rem;
    letter-spacing: 0.05em;
    padding: 0.1rem 0.6rem;
    border-radius: 4px;
    color: #000;
    background: var(--muted);
  }
  .pill.auto {
    background: var(--live);
  }
  .pill.manual {
    background: var(--stale);
  }
  .pill.killed {
    background: var(--offline);
  }
  .pill.disarmed {
    background: transparent;
    color: var(--muted);
    border: 1px solid var(--muted);
  }
  .pill.phase {
    margin-left: auto;
    font-size: 0.8rem;
    background: transparent;
    color: var(--connecting);
    border: 1px solid currentColor;
  }
  .pill.phase.unknown {
    color: var(--muted);
  }
  .mode {
    font-size: 0.9rem;
    color: var(--muted);
  }
  .task {
    font-size: 0.85rem;
    color: var(--muted);
  }
  .task b {
    color: var(--text);
  }
  .hint {
    font-size: 0.75rem;
  }

  .alerts {
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
    min-height: 1.4rem;
  }
  .alert {
    color: #000;
    font-weight: 700;
    font-size: 0.75rem;
    padding: 0.1rem 0.45rem;
    border-radius: 3px;
  }
  .alert.critical {
    background: var(--offline);
  }
  .alert.warn {
    background: var(--stale);
  }
  .batt-critical {
    border-color: var(--offline);
  }
  .batt-critical .v {
    color: var(--offline);
  }
  .batt-low {
    border-color: var(--stale);
  }
  .batt-low .v {
    color: var(--stale);
  }
  .ok {
    color: var(--live);
    font-size: 0.8rem;
  }

  .tiles {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 0.35rem;
  }
  .tile {
    display: flex;
    flex-direction: column;
    padding: 0.3rem 0.45rem;
    border: 1px solid var(--border);
    border-radius: 4px;
    min-width: 0;
  }
  .k {
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted);
  }
  .v {
    font-size: 1.05rem;
    font-weight: 600;
    white-space: nowrap;
  }
  .s {
    font-size: 0.75rem;
    color: var(--muted);
  }

  .message {
    font-size: 0.8rem;
    padding: 0.2rem 0.4rem;
    border: 1px solid var(--border);
    border-radius: 4px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .message.critical {
    border-color: var(--offline);
    color: var(--offline);
    font-weight: 700;
  }
  .message.warn {
    border-color: #e3b341;
    color: #e3b341;
    font-weight: 700;
  }
  .message.none {
    color: var(--muted);
  }

  .log {
    list-style: none;
    margin: 0.3rem 0 0;
    padding: 0;
    max-height: 10rem;
    overflow: auto;
    font-size: 0.75rem;
  }
  .log li {
    display: grid;
    grid-template-columns: auto 5.5rem 1fr;
    gap: 0.5rem;
    padding: 0.05rem 0;
  }
  .log .time {
    color: var(--muted);
  }
  .log .critical {
    color: var(--offline);
  }
  .log .warn {
    color: #e3b341;
  }
  .log .none {
    color: var(--muted);
  }

  summary {
    cursor: pointer;
    font-size: 0.8rem;
    color: var(--muted);
  }
  .groups {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(12rem, 1fr));
    gap: 0.5rem 1rem;
    margin-top: 0.4rem;
  }
  h3 {
    margin: 0 0 0.2rem;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted);
  }
  dl {
    margin: 0;
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 0.05rem 0.6rem;
    font-size: 0.85rem;
  }
  dt {
    color: var(--muted);
  }
  dd {
    margin: 0;
    text-align: right;
  }
</style>
