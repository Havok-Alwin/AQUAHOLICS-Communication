// Logic 7: status-text severity, ported from Mission Planner 1.3.x (decompiled).
//
// Selection of the "high" message happens in the backend, inside the MP DLLs:
//   MAVLinkInterface (STATUSTEXT, msg 253): a text becomes cs.messageHigh when
//     severity <= Settings "severity" (default 4 = WARNING), or it starts with "Tuning:",
//     "PreArm:" or "Arm:" (then severity EMERGENCY unless it already passed the threshold);
//   CurrentState.messageHigh: ignores empty/identical text, and reads back "" 10 s after it was set.
//   CurrentState also sets messageHigh itself (fence breach, EKF/sensor health, ...), always as EMERGENCY.
// Every STATUSTEXT, at any severity, goes to cs.messages (MP keeps the last 1000).
//
// Colour (HUD.doPaint): severity <= 3 red, 4 yellow, otherwise white.

export const SEVERITY_NAMES = ['EMERGENCY', 'ALERT', 'CRITICAL', 'ERROR', 'WARNING', 'NOTICE', 'INFO', 'DEBUG'] as const;

export type SeverityLevel = 'critical' | 'warn' | 'info';

export function severityLevel(severity: number): SeverityLevel {
  if (severity <= 3) return 'critical';
  if (severity === 4) return 'warn';
  return 'info';
}

export const severityName = (severity: number): string => SEVERITY_NAMES[severity] ?? String(severity);

/** MP default for Settings "severity": the highest severity that still reaches messageHigh. */
export const MESSAGE_HIGH_MAX_SEVERITY = 4;

/** MAVLinkInterface: does this STATUSTEXT become messageHigh, and with which severity? */
export function messageHighFrom(severity: number, text: string): { text: string; severity: number } | null {
  if (severity <= MESSAGE_HIGH_MAX_SEVERITY) return { text, severity };
  if (text.startsWith('Tuning:') || text.startsWith('PreArm:') || text.startsWith('Arm:')) return { text, severity: 0 };
  return null;
}

/** CurrentState.messageHigh reads back empty this long after it was set. */
export const MESSAGE_HIGH_HOLD_MS = 10_000;

/** MP keeps the last 1000 status texts per vehicle. */
export const MESSAGE_LOG_MAX = 1000;
