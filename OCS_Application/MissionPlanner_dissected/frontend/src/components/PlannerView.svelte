<script lang="ts">
  // Mission planner view (phase 1-2: planning only, nothing is sent to a vehicle yet).
  // Logic 12 (map location, from MP FlightPlanner/FlightData): initial view, Zoom to vehicle/mission/home,
  // Go to place or coordinates, cursor coordinates with distance/azimuth, right-click menu.
  // Like Mission Planner's Flight Plan tab: click the map to add a waypoint, drag to move it, edit
  // the grid, save/load .waypoints files. Base layers come from public/map/sources.json (online,
  // optional) and map/base.json (the offline course image/tiles, same as the dashboard map).
  import 'leaflet/dist/leaflet.css';
  import L from 'leaflet';
  import { onMount } from 'svelte';
  import { get } from 'svelte/store';
  import { VEHICLES, VEHICLE_TYPE, type VehicleId } from '../lib/config';
  import { distanceM, vehiclePosition, type LatLng } from '../lib/mapModel';
  import { MAVCMD } from '../lib/mavcmd';
  import { ocsState } from '../lib/ocs';
  import { vehicleState } from '../lib/telemetry';
  import {
    CMD,
    CMD_SET,
    checkWaypoints,
    cmdName,
    columnNames,
    setCommand,
    fromWpl,
    hasPosition,
    newWaypoint,
    route,
    toWpl,
    totalDistanceM,
    DEFAULT_PLACE,
    ZOOM,
    cursorInfo,
    findPlace,
    initialView,
    parseLatLng,
    type Home,
    type MapView,
    type Waypoint,
  } from '../lib/waypoints';

  interface TileSource {
    name: string;
    tiles: string;
    maxZoom?: number;
    maxNativeZoom?: number;
    /** Values for {s} in the URL (Google: "0123" -> mt0..mt3). */
    subdomains?: string;
  }

  /** Bing addresses tiles by quadkey ({q} in the URL), not z/x/y; same scheme as GMap.NET's BingMapProvider. */
  function quadkey(x: number, y: number, z: number): string {
    let q = '';
    for (let i = z; i > 0; i--) {
      const mask = 1 << (i - 1);
      q += String((x & mask ? 1 : 0) + (y & mask ? 2 : 0));
    }
    return q;
  }
  const QuadkeyLayer = L.TileLayer.extend({
    getTileUrl(this: L.TileLayer & { _url: string; _getSubdomain(c: L.Coords): string }, c: L.Coords) {
      return L.Util.template(this._url.replace('{q}', quadkey(c.x, c.y, c.z)), { s: this._getSubdomain(c) } as never);
    },
  }) as unknown as new (url: string, opts: L.TileLayerOptions) => L.TileLayer;

  let container: HTMLDivElement;
  let vehicle: VehicleId = $state(VEHICLES[0]);
  let wps: Waypoint[] = $state([]);
  let home: Home | null = $state(null);
  let defaultAlt = $state(10);
  let settingHome = $state(false);
  let selected = $state(-1);
  let sources: TileSource[] = $state([]);
  let source = $state('base');
  let sourceNote = $state('');
  let message = $state('');
  let fileInput: HTMLInputElement;
  let geocoder = '';
  /** Go-to shortcuts: the built-in default place plus "places" from map/sources.json. */
  let places: Record<string, LatLng> = { [DEFAULT_PLACE.name]: [DEFAULT_PLACE.lat, DEFAULT_PLACE.lng] };
  let goText = $state('');
  let going = $state(false);
  let cursor: LatLng | null = $state(null);
  /** Right-click menu (MP contextMenuStrip1), at a container pixel, for a map point and maybe a waypoint. */
  let menu: { x: number; y: number; at: LatLng; wp: number | null } | null = $state(null);

  // Logic 12: MP keeps the map view in Settings (maplast_lat/lng/zoom) and the map type (MapType).
  // Here per browser; reading or writing may fail (private window), then the defaults apply.
  const VIEW_KEY = 'planner.maplast';
  const SOURCE_KEY = 'planner.maptype';
  const store = {
    get(key: string): string | null {
      try {
        return localStorage.getItem(key);
      } catch {
        return null;
      }
    },
    set(key: string, value: string) {
      try {
        localStorage.setItem(key, value);
      } catch {
        /* not remembered */
      }
    },
  };
  function lastView(): MapView | null {
    try {
      const v = JSON.parse(store.get(VIEW_KEY) ?? 'null');
      return v && typeof v.lat === 'number' && typeof v.lng === 'number' && typeof v.zoom === 'number' ? v : null;
    } catch {
      return null;
    }
  }

  const type = $derived(VEHICLE_TYPE[vehicle]);
  const zones = $derived($ocsState?.task4.keep_out_zones ?? []);
  const boundary = $derived((type === 'UAV' ? $ocsState?.geofence : $ocsState?.course?.corners) ?? []) as LatLng[];
  const issues = $derived(checkWaypoints(wps, boundary, type, zones));
  // Logic 13: MP's command list for this vehicle's firmware, and the grid headers of the selected row
  // (MP ChangeColumnHeader).
  const commands = $derived(MAVCMD[CMD_SET[type]]);
  const headers = $derived(columnNames(wps[selected]?.cmd ?? CMD.WAYPOINT));
  const distance = $derived(totalDistanceM(home, wps));

  let map: L.Map;
  let baseLayer: L.Layer | null = null;
  let baseCfg: { image?: string; bounds?: [LatLng, LatLng]; opacity?: number; tiles?: string; maxZoom?: number; maxNativeZoom?: number } | null = null;
  const overlay = L.layerGroup();
  const wpLayer = L.layerGroup();
  const line = L.polyline([], { color: '#e3b341', weight: 3 });
  const homeMarker = L.marker([0, 0], {
    draggable: true,
    icon: L.divIcon({ className: 'wp-icon', iconSize: [22, 22], html: '<div class="wp home">H</div>' }),
  });
  const vehicleMarker = L.circleMarker([0, 0], { radius: 7, color: '#000', weight: 1, fillColor: '#58a6ff', fillOpacity: 1 });

  function setBase(choice: string) {
    baseLayer?.remove();
    baseLayer = null;
    sourceNote = '';
    if (choice === 'none') return;
    if (choice === 'base') {
      if (!baseCfg) sourceNote = 'No offline base layer (map/base.json)';
      else if (baseCfg.image && baseCfg.bounds) baseLayer = L.imageOverlay(baseCfg.image, baseCfg.bounds, { opacity: baseCfg.opacity ?? 1 });
      else if (baseCfg.tiles) baseLayer = L.tileLayer(baseCfg.tiles, { maxZoom: baseCfg.maxZoom ?? 22, maxNativeZoom: baseCfg.maxNativeZoom ?? baseCfg.maxZoom ?? 19 });
    } else {
      const s = sources[Number(choice)];
      if (s) {
        const opts: L.TileLayerOptions = { maxZoom: s.maxZoom ?? 22, maxNativeZoom: s.maxNativeZoom ?? 19 };
        if (s.subdomains) opts.subdomains = s.subdomains;
        const layer = s.tiles.includes('{q}') ? new QuadkeyLayer(s.tiles, opts) : L.tileLayer(s.tiles, opts);
        layer.on('tileerror', () => (sourceNote = 'Tiles not loading: no Internet? Switch to the offline base layer'));
        baseLayer = layer;
      }
    }
    if (baseLayer) {
      baseLayer.addTo(map);
      (baseLayer as L.TileLayer | L.ImageOverlay).bringToBack();
    }
  }

  const getJson = async (url: string) => {
    try {
      const r = await fetch(url, { cache: 'no-cache' });
      return r.ok ? await r.json() : null;
    } catch {
      return null;
    }
  };

  function wpIcon(i: number, w: Waypoint, bad: boolean) {
    const label = w.cmd === CMD.WAYPOINT ? String(i + 1) : `${i + 1}`;
    return L.divIcon({
      className: 'wp-icon',
      iconSize: [22, 22],
      html: `<div class="wp ${i === selected ? 'sel' : ''} ${bad ? 'bad' : ''} ${w.cmd === CMD.WAYPOINT ? '' : 'special'}" title="${cmdName(w.cmd)}">${label}</div>`,
    });
  }

  function redraw() {
    wpLayer.clearLayers();
    const bad = new Set(issues.map((i) => i.index));
    wps.forEach((w, i) => {
      if (w.cmd === CMD.DO_SET_ROI && !(w.lat === 0 && w.lng === 0)) {
        // MP WPOverlay: ROI is a red marker, not part of the route.
        L.circleMarker([w.lat, w.lng], { radius: 7, color: '#000', weight: 1, fillColor: '#f85149', fillOpacity: 1 })
          .bindTooltip(`ROI ${i + 1}`, { permanent: true, direction: 'right', className: 'map-label' })
          .on('click', () => (selected = i))
          .addTo(wpLayer);
        return;
      }
      if (!hasPosition(w)) return;
      const m = L.marker([w.lat, w.lng], { draggable: true, icon: wpIcon(i, w, bad.has(i)) });
      m.on('click', () => (selected = i));
      m.on('dragend', () => {
        const p = m.getLatLng();
        const w = wps[i];
        if (w) {
          w.lat = p.lat;
          w.lng = p.lng;
        }
      });
      m.on('contextmenu', (e: L.LeafletMouseEvent) => {
        menu = { x: e.containerPoint.x, y: e.containerPoint.y, at: [e.latlng.lat, e.latlng.lng], wp: i };
      });
      m.addTo(wpLayer);
    });
    line.setLatLngs(route(home, wps));
    if (home) homeMarker.setLatLng([home.lat, home.lng]).addTo(map);
    else homeMarker.remove();
  }

  function drawOverlays() {
    overlay.clearLayers();
    const s = get(ocsState);
    const corners = (s?.course?.corners ?? []) as LatLng[];
    if (corners.length > 2) L.polygon(corners, { color: '#e6edf3', weight: 2, dashArray: '6 4', fill: false, interactive: false }).addTo(overlay);
    const fence = (s?.geofence ?? []) as LatLng[];
    if (fence.length > 2) L.polygon(fence, { color: '#d29922', weight: 2, dashArray: '2 6', fill: false, interactive: false }).addTo(overlay);
    for (const z of s?.task4.keep_out_zones ?? [])
      L.circle(z.center, { radius: z.radius_m, color: '#f85149', weight: 2, fillOpacity: 0.2, interactive: false }).addTo(overlay);
  }

  // ---- Logic 12: moving the map to a location (MP contextMenuStripZoom + zoomToToolStripMenuItem) ----
  function zoomToVehicle() {
    const pos = vehiclePosition(get(vehicleState[vehicle]));
    // MP: "Invalid Location" when the vehicle position is 0,0.
    if (!pos) return (message = `${vehicle}: invalid location (no 3D fix)`);
    map.setView(pos, Math.max(map.getZoom(), ZOOM.vehicleOrHomeMin));
  }
  function zoomToMission() {
    // MP ZoomAndCenterMarkers("WPOverlay"): the waypoint markers, home included.
    const pts = route(home, wps);
    if (pts.length === 0) return (message = 'No waypoints to zoom to');
    if (pts.length === 1) map.setView(pts[0]!, Math.max(map.getZoom(), ZOOM.vehicleOrHomeMin));
    else map.fitBounds(L.latLngBounds(pts), { padding: [40, 40] });
  }
  function zoomToHome() {
    // MP: the vehicle's home, else the planned home. Link B has no vehicle home yet: planned home only.
    if (!home) return (message = 'Home is not set');
    map.setView([home.lat, home.lng], Math.max(map.getZoom(), ZOOM.vehicleOrHomeMin));
  }
  async function goTo() {
    const text = goText.trim();
    if (!text) return;
    const at = findPlace(text, places) ?? parseLatLng(text);
    if (at) {
      map.setView(at, ZOOM.place);
      message = '';
      return;
    }
    if (!geocoder) return (message = 'Place search needs a geocoder in map/sources.json: enter "lat, lng" instead');
    going = true;
    try {
      const r = await fetch(geocoder.replace('{q}', encodeURIComponent(text)));
      const hits = r.ok ? await r.json() : [];
      const hit = Array.isArray(hits) ? hits[0] : null;
      if (!hit) {
        message = `Can't find: '${text}'${r.ok ? '' : ` (HTTP ${r.status})`}`;
      } else {
        map.setView([Number(hit.lat), Number(hit.lon)], ZOOM.place);
        message = hit.display_name ?? '';
      }
    } catch {
      message = `Can't find: '${text}' (no Internet? Enter "lat, lng" instead)`;
    } finally {
      going = false;
    }
  }
  function menuAction(fn: () => void) {
    fn();
    menu = null;
  }

  function remove(i: number) {
    wps.splice(i, 1);
    if (selected >= wps.length) selected = -1;
  }
  function move(i: number, d: -1 | 1) {
    const j = i + d;
    if (j < 0 || j >= wps.length) return;
    const a = wps[i]!;
    wps[i] = wps[j]!;
    wps[j] = a;
    selected = j;
  }
  function setCmd(i: number, cmd: number) {
    const w = wps[i];
    if (w) setCommand(w, cmd);
    selected = i;
  }
  /** MP BUT_Add ("Add Below"): a new row after the selected one, no position, to be turned into any command. */
  function addRow() {
    const at = selected >= 0 ? selected + 1 : wps.length;
    wps.splice(at, 0, newWaypoint([0, 0], 0));
    selected = at;
  }

  function homeFromVehicle() {
    const pos = vehiclePosition(get(vehicleState[vehicle]));
    if (!pos) return (message = `${vehicle} has no 3D fix: click "Set home" and then the map instead`);
    home = { lat: pos[0], lng: pos[1], alt: 0 };
    message = '';
  }

  function save() {
    const blob = new Blob([toWpl(home, wps)], { type: 'text/plain' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${vehicle}-mission.waypoints`;
    a.click();
    URL.revokeObjectURL(a.href);
  }
  async function load(e: Event) {
    const input = e.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    try {
      const m = fromWpl(await file.text());
      if (wps.length > 0 && !confirm(`Replace the ${wps.length} waypoints on the plan with ${m.wps.length} from the file?`)) return;
      wps = m.wps;
      home = m.home;
      selected = -1;
      message = `Loaded ${m.wps.length} waypoints from ${file.name}`;
      const pts = route(home, wps);
      if (pts.length > 0) map.fitBounds(L.latLngBounds(pts), { padding: [40, 40] });
    } catch (err) {
      message = (err as Error).message;
    }
  }
  function clear() {
    if (wps.length === 0 || confirm(`Clear all ${wps.length} waypoints?`)) {
      wps = [];
      selected = -1;
    }
  }

  const fmt = (m: number) => (m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${m.toFixed(0)} m`);

  $effect(() => {
    // Re-draw whenever the plan, selection or checks change.
    void [wps.map((w) => [w.cmd, w.lat, w.lng]), home, selected, issues];
    if (map) redraw();
  });
  $effect(() => {
    void [$ocsState];
    if (map) drawOverlays();
  });
  $effect(() => {
    void source;
    if (map) setBase(source);
    store.set(SOURCE_KEY, source);
  });

  const info = $derived(cursor ? cursorInfo(cursor, home, wps) : null);

  onMount(() => {
    map = L.map(container, { attributionControl: false, maxZoom: 22, zoomSnap: 0.25 });
    const start = initialView(home, lastView());
    if (start === 'course') map.setView([DEFAULT_PLACE.lat, DEFAULT_PLACE.lng], DEFAULT_PLACE.zoom);
    else map.setView([start.lat, start.lng], start.zoom);
    map.on('moveend', () => {
      const c = map.getCenter();
      store.set(VIEW_KEY, JSON.stringify({ lat: c.lat, lng: c.lng, zoom: map.getZoom() }));
    });
    map.on('mousemove', (e: L.LeafletMouseEvent) => (cursor = [e.latlng.lat, e.latlng.lng]));
    map.on('mouseout', () => (cursor = null));
    map.on('contextmenu', (e: L.LeafletMouseEvent) => {
      menu = { x: e.containerPoint.x, y: e.containerPoint.y, at: [e.latlng.lat, e.latlng.lng], wp: null };
    });
    map.on('movestart', () => (menu = null));
    L.control.scale({ metric: true, imperial: false, position: 'bottomright' }).addTo(map);
    overlay.addTo(map);
    line.addTo(map);
    wpLayer.addTo(map);
    homeMarker.on('dragend', () => {
      const p = homeMarker.getLatLng();
      home = { lat: p.lat, lng: p.lng, alt: home?.alt ?? 0 };
    });
    map.on('click', (e: L.LeafletMouseEvent) => {
      if (menu) return void (menu = null); // a click off the menu only closes it (MP isMouseClickOffMenu)
      if (settingHome) {
        home = { lat: e.latlng.lat, lng: e.latlng.lng, alt: 0 };
        settingHome = false;
        return;
      }
      wps.push(newWaypoint([e.latlng.lat, e.latlng.lng], defaultAlt));
      selected = wps.length - 1;
    });
    void (async () => {
      baseCfg = await getJson('map/base.json');
      const list = await getJson('map/sources.json');
      sources = list?.sources ?? [];
      geocoder = typeof list?.geocoder === 'string' ? list.geocoder : '';
      for (const [name, at] of Object.entries(list?.places ?? {}))
        if (Array.isArray(at) && at.length === 2 && at.every((n) => typeof n === 'number')) places[name] = at as LatLng;
      const saved = store.get(SOURCE_KEY);
      const valid = (v: string | null) => v === 'base' || v === 'none' || (v !== null && sources[Number(v)] !== undefined);
      if (valid(saved)) source = saved!;
      else if (!baseCfg && sources.length > 0) source = '0';
      setBase(source);
      const corners = (get(ocsState)?.course?.corners ?? []) as LatLng[];
      if (start === 'course' && corners.length > 2) map.fitBounds(L.latLngBounds(corners), { padding: [20, 20] });
    })();
    // Vehicle position, same cadence as the status feed is enough here.
    const t = setInterval(() => {
      const pos = vehiclePosition(get(vehicleState[vehicle]));
      if (pos) vehicleMarker.setLatLng(pos).addTo(map);
      else vehicleMarker.remove();
    }, 1000);
    const resize = new ResizeObserver(() => map.invalidateSize());
    resize.observe(container);
    return () => {
      clearInterval(t);
      resize.disconnect();
      map.remove();
    };
  });
</script>

<svelte:window onkeydown={(e) => e.key === 'Escape' && (menu = null)} />

<div class="planner">
  <div class="bar">
    <label>Vehicle
      <select bind:value={vehicle}>{#each VEHICLES as id (id)}<option value={id}>{id} ({VEHICLE_TYPE[id]})</option>{/each}</select>
    </label>
    <label>Default alt (m)
      <input type="number" bind:value={defaultAlt} min="0" step="1" />
    </label>
    <label>Map
      <select bind:value={source}>
        <option value="base">Course image / offline tiles</option>
        {#each sources as s, i (s.name)}<option value={String(i)}>{s.name}</option>{/each}
        <option value="none">None (overlays only)</option>
      </select>
    </label>
    <span class="spacer"></span>
    <span class="note">Planning only: nothing is sent to {vehicle} yet</span>
  </div>

  <div class="main">
    <div class="mapbox">
      <div class="map" bind:this={container}></div>
      {#if sourceNote}<div class="map-note">{sourceNote}</div>{/if}
      <div class="maptools">
        <form onsubmit={(e) => (e.preventDefault(), void goTo())}>
          <input placeholder="Go to: RMK, a place, or lat, lng" bind:value={goText} disabled={going} />
          <button type="submit" disabled={going || !goText.trim()}>{going ? '…' : 'Go'}</button>
        </form>
        <div class="zoomto">
          Zoom to
          <button onclick={zoomToVehicle}>Vehicle</button>
          <button onclick={zoomToMission}>Mission</button>
          <button onclick={zoomToHome}>Home</button>
        </div>
      </div>
      {#if menu}
        {@const m = menu}
        <div class="menu" style="left: {m.x}px; top: {m.y}px" role="menu">
          {#if m.wp !== null}
            <button onclick={() => menuAction(() => remove(m.wp!))}>Delete WP {m.wp + 1}</button>
          {:else}
            <button onclick={() => menuAction(() => { wps.push(newWaypoint(m.at, defaultAlt)); selected = wps.length - 1; })}>Add waypoint here</button>
          {/if}
          <button onclick={() => menuAction(() => (home = { lat: m.at[0], lng: m.at[1], alt: 0 }))}>Set home here</button>
          <hr />
          <button onclick={() => menuAction(zoomToVehicle)}>Zoom to vehicle</button>
          <button onclick={() => menuAction(zoomToMission)}>Zoom to mission</button>
          <button onclick={() => menuAction(zoomToHome)}>Zoom to home</button>
          <hr />
          <button onclick={() => menuAction(clear)} disabled={wps.length === 0}>Clear mission</button>
        </div>
      {/if}
      <div class="help">
        {#if cursor}
          <span class="mono">{cursor[0].toFixed(6)}, {cursor[1].toFixed(6)}</span>
          {#if info?.prev} · prev {fmt(info.prev.dist)} AZ {info.prev.az.toFixed(0)}°{/if}
          {#if info?.home !== null && info?.home !== undefined} · home {fmt(info.home)}{/if}
        {:else}
          Click: add waypoint · drag: move · right-click: menu
        {/if}
      </div>
    </div>

    <aside class="side">
      <div class="grid-wrap">
        <table>
          <thead>
            <tr>
              <th>#</th><th>Command</th>
              {#each headers as h, c (c)}<th class:blank={h === ''}>{h}</th>{/each}
              <th></th>
            </tr>
          </thead>
          <tbody>
            {#each wps as w, i (i)}
              {@const names = columnNames(w.cmd)}
              <tr class:sel={i === selected} class:bad={issues.some((x) => x.index === i)} onclick={() => (selected = i)}>
                <td>{i + 1}</td>
                <td>
                  <select value={w.cmd} onchange={(e) => setCmd(i, Number(e.currentTarget.value))}>
                    {#each commands as d (d[1])}<option value={d[1]}>{d[0]}</option>{/each}
                    {#if !commands.some((d) => d[1] === w.cmd)}<option value={w.cmd}>{cmdName(w.cmd)}</option>{/if}
                  </select>
                </td>
                {#each [0, 1, 2, 3] as k (k)}
                  <td><input class="mono narrow" type="number" step="any" bind:value={w.p[k]} disabled={names[k] === ''} title={names[k]} /></td>
                {/each}
                <td><input class="mono" type="number" step="0.000001" bind:value={w.lat} disabled={names[4] === ''} title={names[4]} /></td>
                <td><input class="mono" type="number" step="0.000001" bind:value={w.lng} disabled={names[5] === ''} title={names[5]} /></td>
                <td><input class="mono narrow" type="number" step="any" bind:value={w.alt} disabled={names[6] === ''} title={names[6]} /></td>
                <td class="acts">
                  <button title="Up" onclick={() => move(i, -1)}>↑</button>
                  <button title="Down" onclick={() => move(i, 1)}>↓</button>
                  <button title="Delete" onclick={() => remove(i)}>✕</button>
                </td>
              </tr>
            {:else}
              <tr><td colspan="10" class="muted empty">No waypoints. Click the map to add one.</td></tr>
            {/each}
          </tbody>
        </table>
      </div>

      <div class="homerow">
        Home:
        {#if home}
          <span class="mono">{home.lat.toFixed(6)}, {home.lng.toFixed(6)}</span>
        {:else}<span class="muted">not set (distance starts at WP 1)</span>{/if}
        <button onclick={homeFromVehicle}>= vehicle</button>
        <button class:on={settingHome} onclick={() => (settingHome = !settingHome)}>{settingHome ? 'Click the map…' : 'Set on map'}</button>
        {#if home}<button onclick={() => (home = null)}>✕</button>{/if}
      </div>

      <div class="totals">
        {wps.length} waypoints · {fmt(distance)}
        {#if wps.length > 1 && issues.length === 0}<span class="ok">· all inside the {type === 'UAV' ? 'geofence' : 'course'}</span>{/if}
      </div>
      {#if issues.length > 0}
        <div class="issues">{#each issues as i (i.text)}<div>{i.text}</div>{/each}</div>
      {/if}
      {#if boundary.length < 3 && wps.length > 0}
        <div class="muted small">No {type === 'UAV' ? 'geofence' : 'course'} from the OCS yet: boundary check is off.</div>
      {/if}
      {#if message}<div class="small">{message}</div>{/if}

      <div class="buttons">
        <button disabled title="Next phase: needs the vehicle backend">Read from vehicle</button>
        <button disabled title="Next phase: needs the vehicle backend">Write to vehicle</button>
        <button onclick={addRow} title="Add a row below the selected one (for DO_ / CONDITION_ commands)">Add row</button>
        <button onclick={save} disabled={wps.length === 0 && !home}>Save .waypoints</button>
        <button onclick={() => fileInput.click()}>Load .waypoints</button>
        <button onclick={clear} disabled={wps.length === 0}>Clear</button>
        <input bind:this={fileInput} type="file" accept=".waypoints,.txt,.wpl" onchange={load} hidden />
      </div>
    </aside>
  </div>
</div>

<style>
  .planner {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    padding: 0.5rem;
  }
  .bar,
  .homerow,
  .buttons {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.6rem;
    font-size: 0.85rem;
  }
  .bar {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.4rem 0.7rem;
  }
  .spacer {
    flex: 1;
  }
  .note,
  .muted {
    color: var(--muted);
  }
  .small {
    font-size: 0.8rem;
  }
  .main {
    flex: 1;
    min-height: 0;
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(30rem, 1fr);
    gap: 0.5rem;
  }
  .mapbox {
    position: relative;
    min-height: 0;
    border: 1px solid var(--border);
    border-radius: 6px;
    overflow: hidden;
  }
  .map {
    position: absolute;
    inset: 0;
    background: #0b1a26;
  }
  .map-note,
  .help {
    position: absolute;
    left: 0.5rem;
    z-index: 1000;
    font-size: 0.75rem;
    color: var(--muted);
    background: rgb(22 27 34 / 0.85);
    padding: 0.15rem 0.4rem;
    border-radius: 3px;
  }
  .map-note {
    bottom: 1.8rem;
  }
  .maptools {
    position: absolute;
    top: 0.5rem;
    right: 0.5rem;
    z-index: 1000;
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 0.3rem;
    font-size: 0.8rem;
  }
  .maptools form,
  .zoomto {
    display: flex;
    align-items: center;
    gap: 0.3rem;
    background: rgb(22 27 34 / 0.85);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 0.2rem 0.4rem;
  }
  .maptools input {
    width: 14rem;
  }
  .menu {
    position: absolute;
    z-index: 1100;
    display: flex;
    flex-direction: column;
    min-width: 11rem;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 0.2rem;
    box-shadow: 0 4px 12px rgb(0 0 0 / 0.5);
  }
  .menu button {
    text-align: left;
    border: none;
    background: transparent;
    padding: 0.25rem 0.5rem;
  }
  .menu button:hover:not(:disabled) {
    background: rgb(88 166 255 / 0.15);
  }
  .menu hr {
    border: none;
    border-top: 1px solid var(--border);
    margin: 0.15rem 0;
  }
  .help {
    bottom: 0.5rem;
  }
  .side {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    min-height: 0;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.5rem;
  }
  .grid-wrap {
    flex: 1;
    min-height: 0;
    overflow: auto;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.8rem;
  }
  th {
    text-align: left;
    color: var(--muted);
    font-weight: 600;
    position: sticky;
    top: 0;
    background: var(--panel);
  }
  td {
    padding: 0.12rem 0.2rem;
  }
  tr.sel {
    background: rgb(88 166 255 / 0.15);
  }
  tr.bad td:first-child {
    color: var(--offline);
    font-weight: 700;
  }
  input,
  select,
  button {
    font: inherit;
    font-size: 0.8rem;
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 3px;
  }
  td input {
    width: 6.4rem;
  }
  td input.narrow {
    width: 3.4rem;
  }
  td select {
    width: 9.5rem;
  }
  td input:disabled {
    opacity: 0.25;
  }
  th {
    white-space: nowrap;
  }
  th.blank::after {
    content: '—';
    color: var(--border);
  }
  .bar input {
    width: 4rem;
  }
  button {
    cursor: pointer;
    padding: 0.15rem 0.5rem;
  }
  button:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }
  button.on {
    border-color: var(--connecting);
  }
  .acts {
    white-space: nowrap;
  }
  .acts button {
    padding: 0 0.3rem;
  }
  .empty {
    padding: 1rem 0;
    text-align: center;
  }
  .totals {
    font-weight: 600;
  }
  .ok {
    color: var(--live);
    font-weight: 400;
  }
  .issues {
    background: var(--offline);
    color: #fff;
    font-size: 0.8rem;
    font-weight: 600;
    border-radius: 4px;
    padding: 0.3rem 0.5rem;
  }
  :global(.wp-icon) {
    background: none;
    border: none;
  }
  :global(.wp) {
    width: 22px;
    height: 22px;
    border-radius: 50%;
    background: #e3b341;
    color: #000;
    border: 2px solid #000;
    font: 700 11px/18px system-ui, sans-serif;
    text-align: center;
  }
  :global(.wp.special) {
    border-radius: 4px;
    background: #d2a8ff;
  }
  :global(.wp.home) {
    background: #3fb950;
  }
  :global(.wp.sel) {
    outline: 3px solid #58a6ff;
  }
  :global(.wp.bad) {
    background: #f85149;
    color: #fff;
  }
</style>
