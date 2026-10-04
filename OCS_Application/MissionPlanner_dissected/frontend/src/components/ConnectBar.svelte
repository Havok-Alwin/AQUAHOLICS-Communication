<script lang="ts">
  // Connect a vehicle from the display (like Mission Planner's connect box): pick a serial port the
  // backend found, a baud rate and optionally the expected sysid, then Connect. While connected:
  // Reconnect (drop and reopen the same port) and Disconnect (confirmed while the vehicle is LIVE,
  // because it stops its telemetry, its heartbeats to RoboCommand and its commands).
  // The backend remembers the connection and restores it at its next start.
  import { tick } from 'svelte';
  import type { VehicleId } from '../lib/config';
  import { connectVehicle, disconnectVehicle } from '../lib/linkB';
  import { MOCK } from '../lib/mode';
  import { vehicleBackend } from '../lib/sources';
  import { serialPorts, vehicleConnectReply, vehicleLink } from '../lib/telemetry';

  let { vehicle }: { vehicle: VehicleId } = $props();

  const BAUDS = [57600, 115200, 921600, 38400, 9600];
  const link = $derived(vehicleLink[vehicle]);
  const reply = $derived(vehicleConnectReply[vehicle]);
  const backend = vehicleBackend.status;
  const backendUp = $derived($backend.state === 'live');

  let port = $state('');
  let baud = $state(57600);
  let sysid = $state('');
  let dialog: HTMLDialogElement | undefined = $state();
  let cancelButton: HTMLButtonElement | undefined = $state();

  // Pick a sensible default once ports appear: the first ArduPilot USB device, else the first port.
  $effect(() => {
    if (port === '' && $serialPorts.length > 0) {
      const preferred = $serialPorts.find((p) => p.label.includes('ArduPilot')) ?? $serialPorts[0]!;
      port = preferred.path;
    }
  });
  // USB (ttyACM, ArduPilot by-id) ignores the baud; telemetry radios (ttyUSB, SiK) default to 57600.
  $effect(() => {
    if (port.includes('ttyACM') || port.includes('ArduPilot')) baud = 115200;
  });

  const connected = $derived($link !== undefined && $link.state !== 'off');
  const label = (path: string | undefined) => $serialPorts.find((p) => p.path === path)?.label ?? path ?? '—';
  const sysidValue = $derived(sysid.trim() === '' ? undefined : Number(sysid));
  const sysidValid = $derived(sysidValue === undefined || (Number.isInteger(sysidValue) && sysidValue >= 1 && sysidValue <= 255));

  function connect() {
    if (port && sysidValid) connectVehicle(vehicle, port, baud, sysidValue);
  }

  function reconnect() {
    const l = $link;
    if (l?.port) connectVehicle(vehicle, l.port, l.baud ?? 57600, l.sysid);
  }

  async function askDisconnect() {
    if ($link?.state !== 'live') {
      disconnectVehicle(vehicle);
      return;
    }
    await tick();
    dialog?.showModal();
    cancelButton?.focus();
  }

  function confirmDisconnect() {
    dialog?.close();
    disconnectVehicle(vehicle);
  }
</script>

{#if !MOCK}
  <div class="connect">
    {#if !backendUp}
      <span class="muted">Vehicle backend not connected</span>
    {:else if !connected}
      <select bind:value={port} aria-label="Serial port" class="port">
        {#each $serialPorts as p (p.path)}
          <option value={p.path}>{p.label}</option>
        {:else}
          <option value="" disabled>No serial ports found</option>
        {/each}
      </select>
      <select bind:value={baud} aria-label="Baud rate">
        {#each BAUDS as b (b)}<option value={b}>{b}</option>{/each}
      </select>
      <input bind:value={sysid} class="sysid" class:bad={!sysidValid} placeholder="sysid" title="Expected sysid (optional): refuses another vehicle on this port" />
      <button class="go" disabled={!port || !sysidValid} onclick={connect}>Connect</button>
    {:else if !$link?.port}
      <span class="muted">Disconnecting…</span>
    {:else}
      <span class="where mono" title={$link.port}>{label($link.port)} · {$link.baud}{$link.sysid ? ` · sysid ${$link.sysid}` : ''}</span>
      <button onclick={reconnect}>Reconnect</button>
      <button class="stop" onclick={askDisconnect}>Disconnect</button>
    {/if}
    {#if $reply && $reply.status === 'error'}
      <div class="error">{$reply.cmd} refused: {$reply.detail}</div>
    {/if}
  </div>

  <dialog bind:this={dialog}>
    <h3>Disconnect {vehicle}?</h3>
    <p>{vehicle} is LIVE. Disconnecting stops its telemetry on this display, its heartbeats to RoboCommand, and commands to it.</p>
    <div class="buttons">
      <button bind:this={cancelButton} onclick={() => dialog?.close()}>Cancel</button>
      <button class="confirm" onclick={confirmDisconnect}>DISCONNECT {vehicle}</button>
    </div>
  </dialog>
{/if}

<style>
  .connect {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.3rem;
    margin-bottom: 0.45rem;
    font-size: 0.8rem;
  }
  select,
  input,
  button {
    font: inherit;
    color: var(--text);
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 0.15rem 0.4rem;
  }
  .port {
    max-width: 14rem;
  }
  .sysid {
    width: 4rem;
  }
  .sysid.bad {
    border-color: var(--offline);
  }
  button {
    background: transparent;
    cursor: pointer;
  }
  button:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  .go:not(:disabled) {
    border-color: var(--live);
    color: var(--live);
    font-weight: 700;
  }
  .stop {
    border-color: var(--offline);
    color: var(--offline);
  }
  .where {
    color: var(--muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 15rem;
  }
  .muted {
    color: var(--muted);
  }
  .error {
    flex-basis: 100%;
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
  .confirm {
    background: var(--stale);
    border-color: var(--stale);
    color: #000;
    font-weight: 700;
  }
</style>
