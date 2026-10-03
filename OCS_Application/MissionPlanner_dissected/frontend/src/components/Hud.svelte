<script lang="ts">
  import { onMount } from 'svelte';
  import { VEHICLE_TYPE, type VehicleId } from '../lib/config';
  import type { CurrentStateFields } from '../lib/currentState';
  import { onFrame } from '../lib/frameClock';
  import { drawHud, type HudInput } from '../lib/hudDraw';
  import { formatAge, type SourceStatus } from '../lib/source';
  import { vehicles } from '../lib/sources';
  import { attitude, vehicleState } from '../lib/telemetry';

  let { vehicle }: { vehicle: VehicleId } = $props();

  let canvas: HTMLCanvasElement;
  let wrapEl: HTMLDivElement;
  let stats = $state('');

  // Plain (non-reactive) copies for the draw loop.
  let status: SourceStatus = { state: 'offline', ageMs: null };
  let cs: Partial<CurrentStateFields> = {};
  // MP HUD timers: ARMED shown 8 s after arming; mode red 2 s after a change.
  let armedAt = -Infinity;
  let modeChangedAt = -Infinity;
  let lastArmed: boolean | undefined;
  let lastMode: string | undefined;

  $effect(() => vehicles[vehicle].status.subscribe((s) => (status = s)));
  $effect(() => {
    // New vehicle selected: forget the previous vehicle's timers.
    lastArmed = lastMode = undefined;
    armedAt = modeChangedAt = -Infinity;
    return vehicleState[vehicle].subscribe((s) => {
      const t = performance.now();
      if (s.armed !== undefined && lastArmed !== undefined && s.armed && !lastArmed) armedAt = t;
      if (s.mode !== undefined && lastMode !== undefined && s.mode !== lastMode) modeChangedAt = t;
      lastArmed = s.armed ?? lastArmed;
      lastMode = s.mode ?? lastMode;
      cs = s;
    });
  });

  onMount(() => {
    const ctx = canvas.getContext('2d')!;
    let frames = 0;

    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(wrapEl.clientWidth * dpr);
      canvas.height = Math.round(wrapEl.clientHeight * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    const ro = new ResizeObserver(resize);
    ro.observe(wrapEl);
    resize();

    const stopFrames = onFrame((nowMs, dtS) => {
      attitude[vehicle].step(nowMs, dtS);
      draw(ctx, wrapEl.clientWidth, wrapEl.clientHeight, nowMs);
      frames++;
    });

    const statsTimer = setInterval(() => {
      const pkts = attitude[vehicle].takePacketCount();
      stats = `draw ${frames} fps · attitude ${pkts} pkt/s`;
      frames = 0;
    }, 1000);

    return () => {
      stopFrames();
      ro.disconnect();
      clearInterval(statsTimer);
    };
  });

  const num = (v: number | undefined) => (v === undefined || !Number.isFinite(v) ? 0 : v);

  function draw(g: CanvasRenderingContext2D, W: number, H: number, nowMs: number) {
    const a = attitude[vehicle].shown;
    const type = VEHICLE_TYPE[vehicle];
    const input: HudInput = {
      roll: a.roll,
      pitch: a.pitch,
      heading: a.yaw, // MP binds heading <- yaw
      targetheading: num(cs.nav_bearing),
      groundcourse: num(cs.groundcourse),
      xtrack_error: num(cs.xtrack_error),
      turnrate: num(cs.turnrate),
      airspeed: num(cs.airspeed),
      groundspeed: num(cs.groundspeed),
      targetspeed: num(cs.targetairspeed),
      alt: num(cs.alt),
      targetalt: num(cs.targetalt),
      groundalt: num(cs.HomeAlt),
      verticalspeed: num(cs.verticalspeed),
      mode: cs.mode ?? '',
      disttowp: num(cs.wp_dist),
      wpno: num(cs.wpno),
      armed: cs.armed ?? false,
      safetyactive: cs.safetyactive ?? false,
      failsafe: cs.failsafe ?? false,
      message: cs.messageHigh ?? '',
      messageSeverity: num(cs.messageHighSeverity),
      lowairspeed: false, // logic 6
      lowgroundspeed: false, // logic 6
    };
    drawHud(g, W, H, input, {
      showAlt: type === 'UAV',
      showAirspeed: type === 'UAV',
      armedRecently: nowMs - armedAt < 8000,
      modeRecentlyChanged: nowMs - modeChangedAt < 2000,
      speedunit: 'm/s', // logic 10
      altunit: 'm',
      distunit: 'm',
    });

    // A frozen HUD must never be read as a still vehicle.
    if (status.state !== 'live' || !attitude[vehicle].received) {
      const cx = W / 2;
      const cy = H / 2;
      g.fillStyle = 'rgba(0,0,0,0.65)';
      g.fillRect(0, 0, W, H);
      g.fillStyle = status.state === 'stale' ? '#d29922' : '#f85149';
      g.font = 'bold 22px system-ui, sans-serif';
      g.textAlign = 'center';
      g.textBaseline = 'alphabetic';
      const label = status.state === 'live' ? 'NO ATTITUDE YET' : status.state.toUpperCase();
      g.fillText(label, cx, cy - 6);
      g.font = '14px system-ui, sans-serif';
      g.fillStyle = '#e6edf3';
      g.fillText(`last update ${formatAge(status.ageMs)}`, cx, cy + 18);
      g.textAlign = 'left';
    }
  }
</script>

<div class="hud">
  <div class="canvas-wrap" bind:this={wrapEl}>
    <canvas bind:this={canvas}></canvas>
  </div>
  <div class="stats mono">{stats}</div>
</div>

<style>
  .hud {
    height: 100%;
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
  }
  .canvas-wrap {
    flex: 1;
    min-height: 0;
    position: relative;
    border-radius: 4px;
    overflow: hidden;
  }
  canvas {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
  }
  .stats {
    text-align: right;
    color: var(--muted);
    font-size: 0.75rem;
  }
</style>
