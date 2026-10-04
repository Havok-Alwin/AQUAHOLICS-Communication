<script lang="ts">
  // Link C: the OCS's view of RoboCommand. The handbook asks the operator display to show the
  // RoboCommand connection state and command receipt / response state. Read-only.
  import { formatAge } from '../lib/source';
  import { ocs } from '../lib/sources';
  import { ocsLog, ocsState } from '../lib/ocs';
  import { now } from '../lib/clock';

  const status = ocs.status;
  const live = $derived($status.state === 'live');
  const s = $derived($ocsState);

  const connClass = (c: string) => (c === 'Connected' ? 'good' : c === 'Reconnecting' || c === 'Connecting' ? 'warn' : 'bad');
  const overallClass = { READY: 'good', CONFIRM: 'warn', INCOMPLETE: 'warn', FAIL: 'bad' } as const;
  const itemClass = { PASS: 'good', FAIL: 'bad', WAIT: 'muted', SKIP: 'muted', MANUAL: 'warn', CONFIRM: 'warn' } as const;
  const linkClass = (v: string) => (v === 'LIVE' ? 'good' : 'bad');
  const clock = (ms: number) => new Date(ms).toLocaleTimeString([], { hour12: false });
  const ago = (ms: number) => formatAge(Math.max(0, $now - ms));
  const ll = (p: [number, number]) => `${p[0].toFixed(6)}, ${p[1].toFixed(6)}`;
</script>

{#if !s}
  <div class="empty">{$status.state === 'offline' ? 'OCS not connected (link C)' : 'Waiting for the OCS…'}</div>
{:else}
  {#if !live}
    <div class="not-live">Last known OCS state ({formatAge($status.ageMs)}): NOT LIVE</div>
  {/if}
  <div class="grid" class:dim={!live}>
    <section>
      <h3>RoboCommand</h3>
      <div class="big {connClass(s.connection)}">{s.connection}</div>
      <div class="kv"><span>broker</span><b class="mono">{s.broker ?? '—'}</b></div>
      <div class="kv"><span>team</span><b class="mono">{s.team_id}</b></div>
      <div class="kv">
        <span>run</span>
        <b class="mono">
          {#if s.run.started}RUNNING · run_id {s.run.run_id}
          {:else if s.run.declared}declared (seq {s.run.declaration_seq}), waiting for RunStart
          {:else}not declared{/if}
        </b>
      </div>
      {#if s.local_test}<div class="sim">LOCAL TEST MODE: simulated data</div>{/if}
    </section>

    <section>
      <h3>Preflight <span class="{overallClass[s.preflight.overall]}">{s.preflight.passed}/{s.preflight.total} {s.preflight.overall}</span></h3>
      <details>
        <summary>checklist</summary>
        <ol class="items">
          {#each s.preflight.items as item (item.key)}
            <li title={item.detail}>
              <span class="st {itemClass[item.status]} mono">{item.status}</span>
              {item.label}{#if item.detail}<span class="detail"> — {item.detail}</span>{/if}
            </li>
          {/each}
        </ol>
      </details>
      <div class="kv"><span>course</span><b class="mono">{s.course ? `${s.course.course_id} · ${s.course.pinger_freq_hz} Hz` : '—'}</b></div>
      <div class="kv"><span>tiers</span><b class="mono">{Object.values(s.tiers).join(' / ')}</b></div>
      {#if s.vehicles}
        <div class="kv">
          <span>heartbeats</span>
          <b class="mono">
            {#each Object.entries(s.vehicles) as [id, v] (id)}<span class={linkClass(v)}>{id} {v}</span>{' '}{/each}
          </b>
        </div>
      {/if}
    </section>

    <section>
      <h3>Commands <span class="muted mono">ok {s.command.counts.accepted} · rejected {s.command.counts.rejected} · ignored {s.command.counts.ignored}</span></h3>
      <div class="kv"><span>last</span><b class="mono">{s.command.last}</b></div>
      {#if s.last_error}<div class="error">Last error: {s.last_error}</div>{/if}
      <ol class="log mono">
        {#each [...$ocsLog].reverse().slice(0, 30) as line, i (i)}
          <li class={line.kind}><span class="time">{clock(line.t)}</span> {line.text}</li>
        {:else}
          <li class="muted">No commands yet</li>
        {/each}
      </ol>
      {#if s.log_dropped > 0}<div class="muted">{s.log_dropped} older lines dropped (display was slow)</div>{/if}
    </section>

    <section>
      <h3>Task 4</h3>
      {#if s.task4.moving_object}
        {@const mo = s.task4.moving_object}
        <div class="alert">
          Moving object at <span class="mono">{ll(mo.position)}</span>, {mo.heading_deg.toFixed(0)}° at {mo.speed_mps.toFixed(1)} m/s,
          affects {mo.affected.join(', ') || '—'} <span class="muted">({ago(mo.at)})</span>
        </div>
      {/if}
      {#each s.task4.keep_out_zones as z (z.seq)}
        <div class="alert">Keep-out {z.vehicle_type}: {z.radius_m} m around <span class="mono">{ll(z.center)}</span> <span class="muted">({ago(z.at)})</span></div>
      {/each}
      {#if s.task4.assistance_request}
        {@const a = s.task4.assistance_request}
        <div class="info">Assistance request ({a.vehicle_type}) at <span class="mono">{ll(a.position)}</span> <span class="muted">({ago(a.at)})</span></div>
      {/if}
      {#if s.task4.readiness_confirm}
        <div class="info">Readiness confirmed for {s.task4.readiness_confirm.vehicle_id} (report {s.task4.readiness_confirm.report_seq})</div>
      {/if}
      {#if !s.task4.moving_object && s.task4.keep_out_zones.length === 0 && !s.task4.assistance_request}
        <div class="muted">No active Task 4 items</div>
      {/if}
    </section>
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
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
    gap: 0.4rem 1rem;
    font-size: 0.8rem;
  }
  .dim {
    opacity: 0.45;
    font-style: italic;
  }
  section {
    min-width: 0;
  }
  h3 {
    margin: 0 0 0.3rem;
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  h3 span {
    text-transform: none;
    letter-spacing: 0;
    margin-left: 0.4rem;
  }
  .big {
    font-size: 1.1rem;
    font-weight: 800;
    margin-bottom: 0.2rem;
  }
  .kv {
    display: flex;
    gap: 0.4rem;
    overflow-wrap: anywhere;
  }
  .kv > span:first-child {
    color: var(--muted);
    min-width: 4.5rem;
  }
  .good {
    color: var(--live);
  }
  .warn {
    color: var(--stale);
  }
  .bad,
  .error {
    color: var(--offline);
  }
  .muted {
    color: var(--muted);
  }
  .sim {
    color: var(--mock);
    font-weight: 700;
  }
  .items,
  .log {
    list-style: none;
    margin: 0.2rem 0;
    padding: 0;
  }
  .items li {
    padding: 0.05rem 0;
  }
  .st {
    display: inline-block;
    min-width: 4.2rem;
    font-weight: 700;
  }
  .detail {
    color: var(--muted);
  }
  .log {
    max-height: 7.5rem;
    overflow-y: auto;
    font-size: 0.72rem;
  }
  .log li {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .log .error {
    color: var(--offline);
  }
  .log .vehicle {
    color: var(--connecting);
  }
  .time {
    color: var(--muted);
  }
  .alert {
    color: var(--stale);
    font-weight: 600;
  }
  .info {
    color: var(--connecting);
  }
</style>
