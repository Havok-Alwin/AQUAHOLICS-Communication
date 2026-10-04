// Link C transport: Server-Sent Events from the OCS (../main.py, link_c.py), read-only.
// Real mode only (main.ts). The OCS serves it on its own port (5081) next to the vehicle backend
// (5080) on the same laptop, and allows this page's origin (CORS, config.LINK_C_ALLOWED_ORIGINS).
// EventSource reconnects by itself; while it is down the `ocs` Source is OFFLINE.
import { clearOcsLog, handleLinkC, type LinkCMsg } from './ocs';
import { ocs } from './sources';

const LINK_C_PORT = 5081;

function linkCBase(): string {
  return `${location.protocol}//${location.hostname}:${LINK_C_PORT}`;
}

function linkCUrl(): string {
  return `${linkCBase()}/linkc`;
}

/**
 * The one operator action on link C: report that the vehicle reached the assistance point and is
 * loitering, so the OCS sends the ReadinessReport for that AssistanceRequest (../task4.py).
 */
export async function reportReady(commandSeq: number): Promise<{ ok: boolean; detail: string }> {
  try {
    const res = await fetch(`${linkCBase()}/task4/ready`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command_seq: commandSeq }),
    });
    const body = (await res.json().catch(() => ({}))) as { ok?: boolean; detail?: string };
    return { ok: res.ok && body.ok === true, detail: body.detail ?? `HTTP ${res.status}` };
  } catch (error) {
    return { ok: false, detail: `OCS not reachable: ${String(error)}` };
  }
}

export function startLinkC(): () => void {
  const es = new EventSource(linkCUrl());
  es.onopen = () => {
    clearOcsLog(); // the OCS replays its recent lines to each new connection
    ocs.setConnected(true);
  };
  es.onerror = () => {
    // CONNECTING: the browser is retrying; CLOSED: it gave up. Either way the feed is down.
    ocs.setConnected(false);
  };
  es.onmessage = (e: MessageEvent) => {
    let msg: unknown;
    try {
      msg = JSON.parse(e.data as string);
    } catch {
      console.warn('link C: not JSON', e.data);
      return;
    }
    const ch = (msg as { ch?: unknown } | null)?.ch;
    if (ch === 'state' || ch === 'log') handleLinkC(msg as LinkCMsg);
  };
  return () => {
    es.close();
    ocs.setConnected(false);
  };
}
