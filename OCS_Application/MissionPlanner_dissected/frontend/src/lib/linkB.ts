// Link B transport: WebSocket to the vehicle backend (backend/OcsBackend/LinkBServer.cs).
// Real mode only (main.ts); mock mode feeds handleLinkB from mock.ts instead.
//
// The page is served by the backend itself, so /linkb is same-origin. On the Vite dev server the
// same path is proxied to the backend (vite.config.ts), so this URL never changes.
// Read-only for now: commands (logic 9) will be sent on this socket.
import { LINK_B_RECONNECT_MS } from './pacing';
import { vehicleBackend } from './sources';
import { clearVehicleLinks, handleLinkB, type LinkBMsg } from './telemetry';

const CHANNELS = new Set(['att', 'status', 'params', 'statustext', 'backend']);

function linkBUrl(): string {
  const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${scheme}//${location.host}/linkb`;
}

/** Connects and keeps reconnecting until the returned stop function is called. */
export function startLinkB(): () => void {
  let ws: WebSocket | undefined;
  let retry: ReturnType<typeof setTimeout> | undefined;
  let delay: number = LINK_B_RECONNECT_MS.min;
  let stopped = false;

  const connect = () => {
    ws = new WebSocket(linkBUrl());
    ws.onopen = () => {
      delay = LINK_B_RECONNECT_MS.min;
      vehicleBackend.setConnected(true);
    };
    ws.onmessage = (e: MessageEvent) => {
      let msg: unknown;
      try {
        msg = JSON.parse(e.data as string);
      } catch {
        console.warn('link B: not JSON', e.data);
        return;
      }
      if (typeof msg !== 'object' || msg === null || !CHANNELS.has((msg as { ch?: unknown }).ch as string)) {
        console.warn('link B: unknown message', msg);
        return;
      }
      handleLinkB(msg as LinkBMsg);
    };
    ws.onclose = () => {
      // Every source fed by link B goes OFFLINE with the age of its last update.
      vehicleBackend.setConnected(false);
      clearVehicleLinks();
      if (stopped) return;
      retry = setTimeout(connect, delay);
      delay = Math.min(delay * 2, LINK_B_RECONNECT_MS.max);
    };
  };

  connect();
  return () => {
    stopped = true;
    clearTimeout(retry);
    ws?.close();
  };
}
