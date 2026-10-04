// Link B transport: WebSocket to the vehicle backend (backend/OcsBackend/LinkBServer.cs).
// Real mode only (main.ts); mock mode feeds handleLinkB from mock.ts instead.
//
// The page is served by the backend itself, so /linkb is same-origin. On the Vite dev server the
// same path is proxied to the backend (vite.config.ts), so this URL never changes.
// Logic 9: commands go to the backend on this socket (sendCommand); the backend accepts the socket
// only from this page's origin or the Vite dev server (LinkBServer.OriginAllowed).
import type { VehicleId } from './config';
import { LINK_B_RECONNECT_MS } from './pacing';
import { vehicleBackend } from './sources';
import { clearVehicleLinks, commandSending, commandsLost, handleLinkB, type CommandKind, type LinkBMsg } from './telemetry';

const CHANNELS = new Set(['att', 'status', 'params', 'statustext', 'backend', 'modes', 'cmdack']);

let socket: WebSocket | undefined;
let commandCounter = 0;

/** Connects a vehicle slot to a serial port (backend: LinkBHub.OnConnect; it remembers it). */
export function connectVehicle(vehicle: VehicleId, port: string, baud: number, sysid?: number): boolean {
  return sendRaw({ ch: 'connect', id: nextId(), vehicle, port, baud, ...(sysid !== undefined ? { sysid } : {}) });
}

/** Disconnects a vehicle slot (the port is released; the vehicle shows OFF). */
export function disconnectVehicle(vehicle: VehicleId): boolean {
  return sendRaw({ ch: 'disconnect', id: nextId(), vehicle });
}

function nextId(): string {
  return `${Date.now().toString(36)}-${++commandCounter}`;
}

function sendRaw(msg: object): boolean {
  if (!socket || socket.readyState !== WebSocket.OPEN) return false;
  socket.send(JSON.stringify(msg));
  return true;
}

/**
 * Sends a command to a vehicle through the backend (logic 9). The caller has already asked the
 * operator to confirm. Returns false if link B is not open (nothing was sent).
 */
export function sendCommand(vehicle: VehicleId, cmd: CommandKind, mode?: string): boolean {
  if (!socket || socket.readyState !== WebSocket.OPEN) return false;
  const id = nextId();
  commandSending(vehicle, { id, cmd, mode, status: 'sending', detail: '', at: Date.now() });
  return sendRaw({ ch: 'cmd', id, vehicle, cmd, ...(mode !== undefined ? { mode } : {}) });
}

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
    socket = ws;
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
      commandsLost();
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
