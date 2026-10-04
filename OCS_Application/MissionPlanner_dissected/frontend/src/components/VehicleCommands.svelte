<script lang="ts">
  // Logic 9: arm, disarm and mode change for one vehicle. Every command can move a vehicle, so each
  // one needs an explicit confirmation (CLAUDE.md safety rules); Cancel is the default button.
  // Commands are enabled only while the vehicle is LIVE and no other command is waiting.
  import { tick } from 'svelte';
  import { flightPhase, robotState } from '../lib/autonomy';
  import { VEHICLE_TYPE, type VehicleId } from '../lib/config';
  import { sendCommand } from '../lib/linkB';
  import { MOCK } from '../lib/mode';
  import { vehicles } from '../lib/sources';
  import { vehicleCommand, vehicleModes, vehicleState, type CommandKind, type CommandStatus } from '../lib/telemetry';

  let { vehicle }: { vehicle: VehicleId } = $props();

  const type = $derived(VEHICLE_TYPE[vehicle]);
  const status = $derived(vehicles[vehicle].status);
  const cs = $derived(vehicleState[vehicle]);
  const modes = $derived(vehicleModes[vehicle]);
  const command = $derived(vehicleCommand[vehicle]);

  const live = $derived($status.state === 'live');
  const pending = $derived($command?.status === 'sending' || $command?.status === 'sent');
  const canCommand = $derived(!MOCK && live && !pending);

  let selectedMode = $state('');
  let dialog: HTMLDialogElement | undefined = $state();
  let cancelButton: HTMLButtonElement | undefined = $state();

  interface Ask {
    cmd: CommandKind;
    mode?: string;
    title: string;
    lines: string[];
    warnings: string[];
    action: string;
  }
  let ask: Ask | null = $state(null);

  const autonomous = (mode: string | undefined) => mode !== undefined && robotState(type, { armed: true, mode }) === 'AUTO';

  async function open(cmd: CommandKind, mode?: string) {
    const current = $cs.mode ?? 'unknown';
    const lines: string[] = [];
    const warnings: string[] = [];
    if (cmd === 'arm') {
      lines.push(`Current mode: ${current}. The vehicle runs its pre-arm checks and may refuse.`);
      if (autonomous($cs.mode))
        warnings.push(`${current} is an autonomous mode: ${vehicle} may start moving on its own as soon as it is armed.`);
    } else if (cmd === 'disarm') {
      lines.push('The motors stop.');
      if (type === 'UAV' && flightPhase($cs) === 'AIRBORNE')
        warnings.push(`${vehicle} is AIRBORNE. ArduPilot should refuse to disarm in flight; if it does not, the vehicle falls.`);
    } else {
      lines.push(`From ${current} to ${mode}.`);
      if ($cs.armed && autonomous(mode))
        warnings.push(`${vehicle} is armed: in ${mode} it acts on its own (autonomous mode).`);
    }
    const verb = cmd === 'mode' ? `Set ${vehicle} to ${mode}` : `${cmd === 'arm' ? 'Arm' : 'Disarm'} ${vehicle}`;
    ask = { cmd, mode, title: `${verb}?`, lines, warnings, action: verb.toUpperCase() };
    await tick(); // the dialog's buttons exist only after this render
    dialog?.showModal();
    cancelButton?.focus(); // Enter or Space on a stray key press cancels
  }

  function confirm() {
    if (ask && canCommand) sendCommand(vehicle, ask.cmd, ask.mode);
    close();
  }

  function close() {
    dialog?.close();
    ask = null;
  }

  const LABEL: Record<CommandStatus, string> = {
    sending: 'SENDING…',
    sent: 'SENT, waiting for the vehicle…',
    accepted: 'ACCEPTED',
    rejected: 'REJECTED',
    timeout: 'NO ANSWER',
    error: 'NOT SENT',
  };
  const commandName = (c: { cmd: CommandKind; mode?: string }) =>
    c.cmd === 'mode' ? `mode${c.mode ? ' ' + c.mode : ''}` : c.cmd;
  const clock = (ms: number) => new Date(ms).toLocaleTimeString([], { hour12: false });
</script>

<div class="commands">
  <div class="row">
    <button class="arm" disabled={!canCommand || $cs.armed === true} onclick={() => open('arm')}>ARM</button>
    <button class="disarm" disabled={!canCommand || $cs.armed === false} onclick={() => open('disarm')}>DISARM</button>
    <select bind:value={selectedMode} disabled={!canCommand || $modes.length === 0} aria-label="Flight mode">
      <option value="" disabled>Mode…</option>
      {#each $modes as m (m)}
        <option value={m}>{m}</option>
      {/each}
    </select>
    <button
      disabled={!canCommand || selectedMode === '' || selectedMode === $cs.mode}
      onclick={() => open('mode', selectedMode)}>Set mode</button
    >
  </div>
  {#if MOCK}
    <div class="hint">Commands: real mode only</div>
  {:else if !live}
    <div class="hint">Commands need a LIVE vehicle</div>
  {/if}
  {#if $command}
    <div class="last {$command.status}">
      <span class="mono">{clock($command.at)}</span>
      {commandName($command)}: <b>{LABEL[$command.status]}</b>
      {#if $command.detail && $command.status !== 'sent' && $command.status !== 'accepted'}<span class="detail">({$command.detail})</span>{/if}
    </div>
  {/if}
</div>

<dialog bind:this={dialog} onclose={() => (ask = null)}>
  {#if ask}
    <h3>{ask.title}</h3>
    {#each ask.lines as line (line)}<p>{line}</p>{/each}
    {#each ask.warnings as w (w)}<p class="warning">{w}</p>{/each}
    {#if !canCommand}<p class="warning">Not possible now: the vehicle is not LIVE or a command is still waiting.</p>{/if}
    <div class="buttons">
      <button bind:this={cancelButton} onclick={close}>Cancel</button>
      <button class="confirm" disabled={!canCommand} onclick={confirm}>{ask.action}</button>
    </div>
  {/if}
</dialog>

<style>
  .commands {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
  }
  .row {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem;
  }
  button,
  select {
    font: inherit;
    font-size: 0.8rem;
    color: var(--text);
    background: transparent;
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 0.2rem 0.6rem;
    cursor: pointer;
  }
  select {
    background: var(--panel);
  }
  button:disabled,
  select:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  .arm:not(:disabled) {
    border-color: var(--stale);
    color: var(--stale);
    font-weight: 700;
  }
  .disarm:not(:disabled) {
    border-color: var(--offline);
    color: var(--offline);
    font-weight: 700;
  }
  .hint {
    color: var(--muted);
    font-size: 0.75rem;
  }
  .last {
    font-size: 0.8rem;
  }
  .last .detail {
    color: var(--muted);
  }
  .sending,
  .sent {
    color: var(--connecting);
  }
  .accepted {
    color: var(--live);
  }
  .rejected,
  .timeout,
  .error {
    color: var(--offline);
  }
  dialog {
    background: var(--panel);
    color: var(--text);
    border: 1px solid var(--stale);
    border-radius: 6px;
    max-width: 26rem;
    padding: 1rem 1.2rem;
  }
  dialog::backdrop {
    background: rgb(0 0 0 / 0.6);
  }
  h3 {
    margin: 0 0 0.6rem;
  }
  p {
    margin: 0.3rem 0;
    font-size: 0.9rem;
  }
  .warning {
    color: var(--stale);
    font-weight: 600;
  }
  .buttons {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
    margin-top: 0.9rem;
  }
  .buttons button {
    font-size: 0.9rem;
    padding: 0.35rem 0.9rem;
  }
  .confirm:not(:disabled) {
    background: var(--stale);
    border-color: var(--stale);
    color: #000;
    font-weight: 700;
  }
</style>
