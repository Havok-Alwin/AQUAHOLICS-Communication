<script lang="ts">
  import { onMount } from 'svelte';
  import type { VehicleId } from '../lib/config';
  import { onFrame } from '../lib/frameClock';
  import { formatAge, type SourceStatus } from '../lib/source';
  import { vehicles } from '../lib/sources';
  import { attitude } from '../lib/telemetry';

  let { vehicle }: { vehicle: VehicleId } = $props();

  let canvas: HTMLCanvasElement;
  let wrapEl: HTMLDivElement;
  let status: SourceStatus = { state: 'offline', ageMs: null };
  let readout = $state({ roll: '—', pitch: '—', yaw: '—' });
  let stats = $state('');

  // Plain (non-reactive) copy for the draw loop.
  $effect(() => vehicles[vehicle].status.subscribe((s) => (status = s)));

  const fmt = (v: number, d = 1) => v.toFixed(d);
  const heading = (y: number) => ((y % 360) + 360) % 360;

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
      draw(ctx, wrapEl.clientWidth, wrapEl.clientHeight);
      frames++;
    });

    // Numeric readouts at 5 Hz: text that changes every frame is unreadable.
    const readoutTimer = setInterval(() => {
      const track = attitude[vehicle];
      const a = track.shown;
      readout =
        status.state === 'live' && track.received
          ? { roll: fmt(a.roll) + '°', pitch: fmt(a.pitch) + '°', yaw: fmt(heading(a.yaw), 0) + '°' }
          : { roll: '—', pitch: '—', yaw: '—' };
    }, 200);

    const statsTimer = setInterval(() => {
      const pkts = attitude[vehicle].takePacketCount();
      stats = `draw ${frames} fps · attitude ${pkts} pkt/s`;
      frames = 0;
    }, 1000);

    return () => {
      stopFrames();
      ro.disconnect();
      clearInterval(readoutTimer);
      clearInterval(statsTimer);
    };
  });

  // Temporary drawing: horizon + aircraft symbol only. Real HUD geometry is logic 5.
  function draw(g: CanvasRenderingContext2D, W: number, H: number) {
    const a = attitude[vehicle].shown;
    const cx = W / 2;
    const cy = H / 2;
    const pxPerDeg = 4;

    g.save();
    g.clearRect(0, 0, W, H);
    g.translate(cx, cy);
    g.rotate((-a.roll * Math.PI) / 180);
    g.translate(0, a.pitch * pxPerDeg);
    g.fillStyle = '#3d86c6';
    g.fillRect(-W * 2, -H * 3, W * 4, H * 3);
    g.fillStyle = '#7a5a3a';
    g.fillRect(-W * 2, 0, W * 4, H * 3);
    g.strokeStyle = '#fff';
    g.lineWidth = 2;
    g.beginPath();
    g.moveTo(-W * 2, 0);
    g.lineTo(W * 2, 0);
    g.stroke();
    g.restore();

    g.strokeStyle = '#ffd400';
    g.lineWidth = 3;
    g.beginPath();
    g.moveTo(cx - 70, cy);
    g.lineTo(cx - 20, cy);
    g.lineTo(cx, cy + 14);
    g.lineTo(cx + 20, cy);
    g.lineTo(cx + 70, cy);
    g.stroke();

    // A frozen horizon must never be read as a still vehicle.
    if (status.state !== 'live' || !attitude[vehicle].received) {
      g.fillStyle = 'rgba(0,0,0,0.65)';
      g.fillRect(0, 0, W, H);
      g.fillStyle = status.state === 'stale' ? '#d29922' : '#f85149';
      g.font = 'bold 22px system-ui, sans-serif';
      g.textAlign = 'center';
      const label = status.state === 'live' ? 'NO ATTITUDE YET' : status.state.toUpperCase();
      g.fillText(label, cx, cy - 6);
      g.font = '14px system-ui, sans-serif';
      g.fillStyle = '#e6edf3';
      g.fillText(`last update ${formatAge(status.ageMs)}`, cx, cy + 18);
    }
  }
</script>

<div class="hud">
  <div class="canvas-wrap" bind:this={wrapEl}>
    <canvas bind:this={canvas}></canvas>
  </div>
  <div class="readouts mono">
    <span>Roll <b>{readout.roll}</b></span>
    <span>Pitch <b>{readout.pitch}</b></span>
    <span>Yaw <b>{readout.yaw}</b></span>
    <span class="stats">{stats}</span>
  </div>
</div>

<style>
  .hud {
    height: 100%;
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
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
  .readouts {
    display: flex;
    gap: 1.2rem;
    font-size: 0.9rem;
  }
  .stats {
    margin-left: auto;
    color: var(--muted);
    font-size: 0.8rem;
  }
</style>
