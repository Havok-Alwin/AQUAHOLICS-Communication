<script lang="ts">
  // The tracking map (Leaflet, bundled: no CDN, no online tiles; the course network has no Internet).
  // Overlays: course boundary and UAV geofence (link C), Task 4 keep-out zones and the moving object
  // with its 10 m ring (link C), vehicles with heading and track (link B).
  // Redrawn on MP's map cadence (pacing.ts MAP_*), not per message, outside Svelte's reactivity.
  // Base layer: optional, from map/base.json next to index.html (see loadBaseLayer); the map must work
  // without one, because the overlays are what matter.
  import 'leaflet/dist/leaflet.css';
  import L from 'leaflet';
  import { get } from 'svelte/store';
  import { onMount } from 'svelte';
  import { VEHICLES, VEHICLE_TYPE, type VehicleId } from '../lib/config';
  import {
    MOVING_OBJECT_MIN_DISTANCE_M,
    Track,
    movingObjectView,
    proximityAlerts,
    vehiclePosition,
    offset,
    type LatLng,
  } from '../lib/mapModel';
  import { ocsState } from '../lib/ocs';
  import { MAP_AUTOPAN_MS, MAP_UPDATE_DISCONNECTED_MS, MAP_UPDATE_MS } from '../lib/pacing';
  import { ocs, vehicles } from '../lib/sources';
  import { formatAge } from '../lib/source';
  import { attitude, vehicleState } from '../lib/telemetry';

  let container: HTMLDivElement;
  let follow: 'none' | VehicleId = $state('none');
  let alerts: string[] = $state([]);
  let baseNote = $state('');
  let empty = $state(true);

  const COLOR: Record<VehicleId, string> = { USV1: '#58a6ff', UAV1: '#d2a8ff' };
  const STALE_COLOR = '#8b949e';

  function arrowIcon(id: VehicleId): L.DivIcon {
    return L.divIcon({
      className: 'vehicle-icon',
      iconSize: [28, 28],
      iconAnchor: [14, 14],
      html: `<svg viewBox="-14 -14 28 28" width="28" height="28"><path d="M0,-12 L8,10 L0,5 L-8,10 Z" stroke="#000" stroke-width="1.5"/></svg>`,
    });
  }

  /** map/base.json: {"image": "map/course.png", "bounds": [[s, w], [n, e]]} or {"tiles": "map/tiles/{z}/{x}/{y}.png", "maxZoom": 22}. */
  async function loadBaseLayer(map: L.Map) {
    try {
      const res = await fetch('map/base.json', { cache: 'no-cache' });
      if (!res.ok) throw new Error(`${res.status}`);
      const cfg = await res.json();
      if (cfg.image && cfg.bounds) {
        L.imageOverlay(cfg.image, cfg.bounds, { opacity: cfg.opacity ?? 1 }).addTo(map).bringToBack();
        baseNote = '';
      } else if (cfg.tiles) {
        L.tileLayer(cfg.tiles, { maxZoom: cfg.maxZoom ?? 22, maxNativeZoom: cfg.maxNativeZoom ?? cfg.maxZoom ?? 19 }).addTo(map);
        baseNote = '';
      } else {
        baseNote = 'map/base.json has neither "image"+"bounds" nor "tiles": overlays only';
      }
    } catch {
      baseNote = 'No base layer (map/base.json): overlays only';
    }
  }

  onMount(() => {
    const map = L.map(container, { zoomControl: true, attributionControl: false, maxZoom: 22, zoomSnap: 0.25 });
    map.setView([1.2966, 103.7764], 17);
    L.control.scale({ metric: true, imperial: false }).addTo(map);
    void loadBaseLayer(map);

    const course = L.polygon([], { color: '#e6edf3', weight: 2, dashArray: '6 4', fill: false }).addTo(map);
    const geofence = L.polygon([], { color: '#d29922', weight: 2, dashArray: '2 6', fill: false }).addTo(map);
    course.bindTooltip('Course boundary', { sticky: true });
    geofence.bindTooltip('UAV geofence', { sticky: true });
    const zones = L.layerGroup().addTo(map);
    const mo = {
      ring: L.circle([0, 0], { radius: MOVING_OBJECT_MIN_DISTANCE_M, color: '#f0883e', weight: 2, dashArray: '4 4', fillOpacity: 0.12 }),
      dot: L.circleMarker([0, 0], { radius: 6, color: '#000', weight: 1, fillColor: '#f0883e', fillOpacity: 1 }),
      reported: L.circleMarker([0, 0], { radius: 4, color: '#f0883e', weight: 2, fill: false }),
      heading: L.polyline([], { color: '#f0883e', weight: 2, dashArray: '2 4' }),
    };
    mo.dot.bindTooltip('', { permanent: true, direction: 'right', offset: [8, 0], className: 'map-label' });
    const vehicleLayers = Object.fromEntries(
      VEHICLES.map((id) => {
        const marker = L.marker([0, 0], { icon: arrowIcon(id), interactive: false });
        marker.bindTooltip(id, { permanent: true, direction: 'right', offset: [12, 0], className: 'map-label' });
        const track = L.polyline([], { color: COLOR[id], weight: 2, opacity: 0.7 });
        return [id, { marker, track, trackModel: new Track(), shown: false }];
      }),
    ) as Record<VehicleId, { marker: L.Marker; track: L.Polyline; trackModel: Track; shown: boolean }>;

    let fittedCourse = '';
    let fittedVehicles = false;
    let lastPan = 0;
    let timer: ReturnType<typeof setTimeout>;

    const show = (layer: L.Layer, on: boolean) => {
      if (on && !map.hasLayer(layer)) layer.addTo(map);
      if (!on && map.hasLayer(layer)) layer.remove();
    };

    function update() {
      const now = Date.now();
      const s = get(ocsState);
      const ocsLive = get(ocs.status).state === 'live';
      const newAlerts: string[] = [];
      let anyLive = false;
      let anything = false;

      // Course and geofence: drawn while known, even if the OCS feed is down (they do not move).
      const corners = (s?.course?.corners ?? []) as LatLng[];
      course.setLatLngs(corners);
      geofence.setLatLngs((s?.geofence ?? []) as LatLng[]);
      if (corners.length > 2) {
        anything = true;
        if (fittedCourse !== s!.course!.course_id) {
          fittedCourse = s!.course!.course_id;
          map.fitBounds(L.latLngBounds(corners), { padding: [20, 20] });
        }
      }

      // Task 4 keep-out zones.
      zones.clearLayers();
      const zoneList = s?.task4.keep_out_zones ?? [];
      for (const z of zoneList) {
        L.circle(z.center, { radius: z.radius_m, color: '#f85149', weight: 2, fillOpacity: 0.2 })
          .bindTooltip(`Keep-out ${z.vehicle_type}: ${z.radius_m} m${ocsLive ? '' : ' (OCS not live)'}`, { sticky: true })
          .addTo(zones);
        anything = true;
      }

      // Task 4 moving object: dead-reckoned while fresh, STALE at its last report after 10 s.
      const moState = s?.task4.moving_object ?? null;
      const moView = moState ? movingObjectView(moState, now) : null;
      for (const layer of Object.values(mo)) show(layer, moView !== null);
      if (moState && moView) {
        anything = true;
        const color = moView.stale ? STALE_COLOR : '#f0883e';
        mo.ring.setLatLng(moView.estimate).setStyle({ color });
        mo.dot.setLatLng(moView.estimate).setStyle({ fillColor: color });
        mo.reported.setLatLng(moView.reported).setStyle({ color });
        mo.heading.setLatLngs(moView.stale ? [] : [moView.estimate, offset(moView.estimate, moState.heading_deg, moState.speed_mps * 10)]);
        mo.dot.setTooltipContent(
          `Moving object ${moState.speed_mps.toFixed(1)} m/s ${moState.heading_deg.toFixed(0)}° · ` +
            (moView.stale ? `STALE (${formatAge(moView.ageMs)})` : `${formatAge(moView.ageMs)}`),
        );
      }

      // Vehicles: live ones in colour with heading and track; others grey at the last position, labelled.
      for (const id of VEHICLES) {
        const v = vehicleLayers[id];
        const status = get(vehicles[id].status);
        const live = status.state === 'live';
        const pos = vehiclePosition(get(vehicleState[id]));
        show(v.marker, pos !== null);
        show(v.track, pos !== null);
        if (!pos) continue;
        anything = true;
        anyLive ||= live;
        if (live) v.trackModel.add(pos);
        v.marker.setLatLng(pos);
        v.track.setLatLngs(v.trackModel.latLngs as LatLng[]);
        const svg = v.marker.getElement()?.querySelector('svg');
        if (svg) {
          const track = attitude[id];
          const yaw = track.received ? track.shown.yaw : 0;
          svg.style.transform = `rotate(${yaw}deg)`;
          svg.style.fill = live ? COLOR[id] : STALE_COLOR;
        }
        v.marker.setTooltipContent(live ? id : `${id} ${status.state.toUpperCase()} · ${formatAge(status.ageMs)}`);
        if (!fittedVehicles && !fittedCourse) {
          fittedVehicles = true;
          map.setView(pos, 18);
        }
        if (live)
          for (const a of proximityAlerts(id, VEHICLE_TYPE[id], pos, zoneList, moView, moState?.affected ?? []))
            newAlerts.push(a.text);
        if (follow === id && now - lastPan >= MAP_AUTOPAN_MS) {
          lastPan = now;
          map.panTo(pos);
        }
      }

      if (newAlerts.join('|') !== alerts.join('|')) alerts = newAlerts;
      empty = !anything;
      // MP keeps the map moving at 2 s while disconnected.
      timer = setTimeout(update, anyLive ? MAP_UPDATE_MS : MAP_UPDATE_DISCONNECTED_MS);
    }

    update();
    const resize = new ResizeObserver(() => map.invalidateSize());
    resize.observe(container);
    return () => {
      clearTimeout(timer);
      resize.disconnect();
      map.remove();
    };
  });
</script>

<div class="map-wrap">
  <div class="map" bind:this={container}></div>
  <div class="toolbar">
    <label>
      Follow
      <select bind:value={follow}>
        <option value="none">—</option>
        {#each VEHICLES as id (id)}<option value={id}>{id}</option>{/each}
      </select>
    </label>
  </div>
  {#if alerts.length > 0}
    <div class="alerts">
      {#each alerts as a (a)}<div>{a}</div>{/each}
    </div>
  {/if}
  {#if empty}
    <div class="hint">Waiting for the course (OCS) and vehicle positions (3D fix)</div>
  {/if}
  {#if baseNote}<div class="base-note">{baseNote}</div>{/if}
</div>

<style>
  .map-wrap {
    position: relative;
    height: 100%;
    min-height: 12rem;
  }
  .map {
    position: absolute;
    inset: 0;
    border-radius: 4px;
    background: #0b1a26; /* water-ish, so the overlays read without a base layer */
  }
  .toolbar {
    position: absolute;
    top: 0.5rem;
    right: 0.5rem;
    z-index: 1000;
    font-size: 0.8rem;
    background: rgb(22 27 34 / 0.85);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 0.2rem 0.4rem;
  }
  .toolbar select {
    font: inherit;
    background: var(--panel);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 3px;
  }
  .alerts {
    position: absolute;
    top: 0.5rem;
    left: 3.2rem;
    right: 9rem;
    z-index: 1000;
    background: var(--offline);
    color: #fff;
    font-weight: 700;
    font-size: 0.85rem;
    border-radius: 4px;
    padding: 0.3rem 0.6rem;
  }
  .hint,
  .base-note {
    position: absolute;
    left: 0.5rem;
    z-index: 1000;
    color: var(--muted);
    font-size: 0.75rem;
    background: rgb(22 27 34 / 0.85);
    padding: 0.15rem 0.4rem;
    border-radius: 3px;
  }
  .hint {
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
  }
  .base-note {
    bottom: 1.8rem;
  }
  :global(.vehicle-icon svg) {
    transition: transform 0.2s linear;
  }
  :global(.leaflet-tooltip.map-label) {
    background: rgb(13 17 23 / 0.85);
    color: var(--text);
    border: 1px solid var(--border);
    box-shadow: none;
    font-size: 0.72rem;
    padding: 1px 4px;
  }
  :global(.leaflet-tooltip.map-label::before) {
    display: none;
  }
  :global(.leaflet-container) {
    font: inherit;
  }
</style>
