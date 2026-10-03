// Logic 5: HUD geometry, ported from MissionPlanner.Controls.HUD.doPaint (1.3.x, decompiled).
// All sizes scale with the canvas exactly as in MP (H/30 font, H/65 px per pitch degree, ...).
// C# integer division is reproduced with `idiv`.
//
// Ported: horizon + gradients, pitch ladder, roll scale + pointer, aircraft symbol, heading tape
// (target / course markers), cross-track + turn-rate indicator, speed tape, altitude tape + VSI,
// mode + WP distance, centre ARMED/DISARMED/SAFE/FAILSAFE, status message, NaN guard.
// Not drawn on the HUD (shown on the vehicle card instead, to avoid clutter): battery, GPS,
// link bars + clock, vibration, EKF, pre-arm, CPU load, custom items, AOA/SSA.
//
// Deviations from MP (deliberate):
//   - heading tape: a target off the right edge is marked at the right edge (MP draws it at the
//     centre, a bug), and off-tape checks use the wrapped angle difference (MP ignores wrap at 0/360);
//   - heading readout box: text is dark on the light box (MP draws white on near-white).

export interface HudInput {
  roll: number;
  pitch: number;
  heading: number;
  targetheading: number; // nav_bearing
  groundcourse: number;
  xtrack_error: number;
  turnrate: number;
  airspeed: number;
  groundspeed: number;
  targetspeed: number; // targetairspeed
  alt: number;
  targetalt: number;
  groundalt: number; // HomeAlt
  verticalspeed: number;
  mode: string;
  disttowp: number;
  wpno: number;
  armed: boolean;
  safetyactive: boolean;
  failsafe: boolean;
  message: string;
  messageSeverity: number; // MAV_SEVERITY
  // Warning flags: computed by logic 6. Until then they are false.
  lowairspeed: boolean;
  lowgroundspeed: boolean;
}

export interface HudOptions {
  /** MP displayalt: altitude tape + VSI (off for the USV). */
  showAlt: boolean;
  /** Airspeed line under the speed tape (off for the USV). */
  showAirspeed: boolean;
  /** MP: show ARMED for 8 s after arming. */
  armedRecently: boolean;
  /** MP: mode text red for 2 s after a mode change. */
  modeRecentlyChanged: boolean;
  speedunit: string;
  altunit: string;
  distunit: string;
}

const idiv = (a: number, b: number) => Math.trunc(a / b);
const rad = (d: number) => (d * Math.PI) / 180;

// MP default colours
const SKY1 = 'rgb(0,0,255)'; // Color.Blue
const SKY2 = 'rgb(173,216,230)'; // Color.LightBlue
const GROUND1 = 'rgb(155,184,36)';
const GROUND2 = 'rgb(65,79,7)';
const WHITE = '#fff';
const GREEN = 'rgb(0,128,0)'; // Color.Green
const GREEN_HALF = 'rgba(0,128,0,0.5)';
const RED = 'rgb(255,0,0)';
const YELLOW = 'rgb(255,255,0)';
const TAPE_BG = 'rgba(255,255,255,0.333)'; // FromArgb(85, white)
const HDG_BOX = 'rgba(255,255,255,0.863)'; // FromArgb(220, white)
const ALT_GROUND = 'rgba(222,184,135,0.392)'; // FromArgb(100, BurlyWood)
const TEXT_OUTLINE = 'rgb(38,39,40)';
const ALICE_BLUE = 'rgb(240,248,255)';
const FONT = 'Arial, Helvetica, sans-serif';

const CARDINAL: Record<number, string> = { 0: 'N', 45: 'NE', 90: 'E', 135: 'SE', 180: 'S', 225: 'SW', 270: 'W', 315: 'NW' };

export function drawHud(g: CanvasRenderingContext2D, W: number, H: number, inp: HudInput, opt: HudOptions): void {
  W = Math.floor(W);
  H = Math.floor(H);

  // MP drawstring: glyphs at fontsize + 5, dark outline then fill. y is the top of the text.
  const text = (s: string, size: number, color: string, x: number, y: number) => {
    g.font = `${size + 5}px ${FONT}`;
    g.textBaseline = 'top';
    g.textAlign = 'left';
    g.lineWidth = 2;
    g.strokeStyle = TEXT_OUTLINE;
    g.strokeText(s, x, y);
    g.fillStyle = color;
    g.fillText(s, x, y);
  };
  const measure = (s: string, size: number) => {
    g.font = `${size + 5}px ${FONT}`;
    return g.measureText(s).width;
  };
  const line = (color: string, width: number, x1: number, y1: number, x2: number, y2: number) => {
    g.strokeStyle = color;
    g.lineWidth = width;
    g.beginPath();
    g.moveTo(x1, y1);
    g.lineTo(x2, y2);
    g.stroke();
  };
  const poly = (pts: [number, number][], fill: string | null, stroke: string | null, width = 2) => {
    g.beginPath();
    pts.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y)));
    g.closePath();
    if (fill) {
      g.fillStyle = fill;
      g.fill();
    }
    if (stroke) {
      g.strokeStyle = stroke;
      g.lineWidth = width;
      g.stroke();
    }
  };

  let roll = inp.roll;
  let pitch = inp.pitch;
  let heading = ((inp.heading % 360) + 360) % 360;
  let nanError = false;
  if (Number.isNaN(roll) || Number.isNaN(pitch) || Number.isNaN(heading)) {
    nanError = true;
    roll = pitch = heading = 0;
  }

  const num2 = idiv(H, 30); // base font size
  const num3 = num2 - 10;
  const num4 = -idiv(H, 65); // px per pitch degree (negative: up is -y)
  const num5 = -pitch * num4; // horizon offset
  const num6 = idiv(W, 2);
  const num7 = idiv(H, 2);

  g.save();
  // Caller's transform (device-pixel-ratio scale): every MP ResetTransform() returns here.
  const base = g.getTransform();
  const reset = () => g.setTransform(base);
  g.clearRect(0, 0, W, H);

  // --- horizon -----------------------------------------------------------------------------
  g.translate(idiv(W, 2), idiv(H, 2));
  g.rotate(rad(-roll));
  {
    const skyY = -num7 * 2;
    const skyH = num7 * 2 + num5;
    if (skyH !== 0) {
      const gr = g.createLinearGradient(0, skyY, 0, skyY + skyH);
      gr.addColorStop(0, SKY1);
      gr.addColorStop(1, SKY2);
      g.fillStyle = gr;
      g.fillRect(-num6 * 2, skyY, W * 2, skyH);
    }
    const gndH = num7 * 2 - num5;
    if (gndH !== 0) {
      const gr = g.createLinearGradient(0, num5, 0, num5 + gndH);
      gr.addColorStop(0, GROUND1);
      gr.addColorStop(1, GROUND2);
      g.fillStyle = gr;
      g.fillRect(-num6 * 2, num5, W * 2, gndH);
    }
    line(WHITE, 2, -num6 * 2, num5, num6 * 2, num5);
  }
  reset();

  // --- pitch ladder (clipped below the heading tape) ------------------------------------------
  g.save();
  g.beginPath();
  g.rect(0, idiv(H, 14), W, H - idiv(H, 14));
  g.clip();
  g.translate(idiv(W, 2), idiv(H, 2));
  g.rotate(rad(-roll));
  {
    const minor = idiv(W, 14);
    const major = idiv(W, 10);
    for (let i = -90; i <= 90; i += 5) {
      if (!(i >= pitch - 29 && i <= pitch + 20)) continue;
      const y = num5 + i * num4;
      if (i % 10 === 0) {
        line(i === 0 ? GREEN : WHITE, 2, -major, y, major, y);
        text(String(i), num2 + 2, WHITE, -major - 30 - Math.trunc(num3 * 1.7), y - 8 - num3);
      } else {
        line(WHITE, 2, -minor, y, minor, y);
      }
    }
  }
  g.restore();

  // --- roll scale + pointer + aircraft symbol -------------------------------------------------
  {
    reset();
    g.translate(idiv(W, 2), idiv(H, 2));
    const s = idiv(H, 66);
    const r0 = Math.trunc((H / 15.0) * 4.9);
    const t = s + 2;
    poly(
      [
        [0, -t * 2 - r0],
        [-t, -t - r0],
        [t, -t - r0],
      ],
      null,
      RED,
      Math.abs(roll) > 45 ? 4 : 2,
    );
    for (const a of [-60, -45, -30, -20, -10, 0, 10, 20, 30, 45, 60]) {
      reset();
      g.translate(idiv(W, 2), idiv(H, 2));
      g.rotate(rad(a - roll));
      text(String(Math.abs(a)).padStart(2), num2, WHITE, -6 - num3, -s * 8 - r0);
      line(WHITE, 2, 0, -s * 3 - r0, 0, -s * 3 - r0 - s);
    }
    reset();
    g.translate(idiv(W, 2), idiv(H, 2));
    const R = r0 + s * 3;
    g.strokeStyle = WHITE;
    g.lineWidth = 2;
    g.beginPath();
    g.arc(0, 0, R, rad(210 - roll), rad(330 - roll));
    g.stroke();

    const left = -idiv(num6, 2);
    const right = left + num6;
    const sym = 'rgba(255,0,0,0.784)'; // FromArgb(200, red)
    line(sym, 4, left - idiv(num6, 5), 0, left, 0);
    line(sym, 4, right, 0, right + idiv(num6, 5), 0);
    line(sym, 4, -1, 0, right - idiv(num6, 3), idiv(num7, 10));
    line(sym, 4, 1, 0, left + idiv(num6, 3), idiv(num7, 10));
  }

  // --- heading tape ----------------------------------------------------------------------------
  reset();
  const hdgBottom = idiv(H, 14);
  {
    g.fillStyle = TAPE_BG;
    g.fillRect(0, 0, W, hdgBottom);
    g.strokeStyle = '#000';
    g.lineWidth = 2;
    g.strokeRect(0, 0, W, hdgBottom);
    line(WHITE, 2, 5, hdgBottom - 5, W - 5, hdgBottom - 5);
    const pxPerDeg = (W - 10) / 120;
    const start = Math.round(heading - 60);
    const x = (k: number) => 5 + pxPerDeg * (k - start);
    const tgtDiff = ((((inp.targetheading - heading) % 360) + 540) % 360) - 180;
    if (tgtDiff < -60) line(GREEN, 6, x(start), hdgBottom, x(start), 0);
    if (tgtDiff > 60) line(GREEN, 6, x(start + 120), hdgBottom, x(start + 120), 0);
    const labelY = hdgBottom - 24 - Math.trunc(num3 * 1.7);
    for (let k = start; k <= heading + 60; k++) {
      const kk = ((k % 360) + 360) % 360;
      if (kk === Math.trunc(inp.targetheading)) line(GREEN, 6, x(k), hdgBottom, x(k), 0);
      if (kk === Math.trunc(inp.groundcourse)) line('#000', 6, x(k), hdgBottom, x(k), 0);
      if (k % 15 === 0) {
        line(WHITE, 2, x(k), hdgBottom - 5, x(k), hdgBottom - 10);
        const lx = x(k) - 10 - num3;
        if (CARDINAL[kk] !== undefined) text(CARDINAL[kk]!.padStart(2), num2 + 4, WHITE, lx, labelY);
        else text(String(kk).padStart(3), num2, WHITE, lx, labelY);
      } else if (k % 5 === 0) {
        line(WHITE, 2, x(k), hdgBottom - 5, x(k), hdgBottom - 10);
      }
    }
    const boxW = num2 * 2.4;
    g.fillStyle = HDG_BOX;
    g.fillRect(idiv(W, 2) - boxW / 2, 0, boxW, hdgBottom);
    g.font = `${num2 + 5}px ${FONT}`;
    g.textBaseline = 'top';
    g.fillStyle = '#000';
    g.fillText(String(Math.trunc(heading % 360)).padStart(3), idiv(W, 2) - num2, labelY);
  }

  // --- cross-track error + turn rate ------------------------------------------------------------
  {
    const x0 = idiv(W, 10);
    const step = W / 10 / 3;
    const pad = 10;
    const top = hdgBottom + 5;
    const bot = hdgBottom + idiv(H, 10);
    const xt = Math.max(Math.min(inp.xtrack_error, 40), -40);
    line(Math.abs(xt) === 40 ? GREEN_HALF : GREEN, 2, x0 + (xt / 20) * step, top, x0 + (xt / 20) * step, bot);
    line(WHITE, 2, x0, top, x0, bot);
    for (const m of [-2, -1, 1, 2]) line(WHITE, 2, x0 + m * step, top + pad, x0 + m * step, bot - pad);
    const by = bot + 10;
    for (const c of [-2, 0, 2]) line(WHITE, 4, x0 + c * step - step / 2, by, x0 + c * step + step / 2, by);
    const span = 4 * step;
    const tr = Math.max(Math.min(inp.turnrate, 6), -6);
    const off = (tr / 12) * span;
    const col = Math.abs(tr) === 6 ? GREEN_HALF : GREEN;
    line(col, 4, x0 + off - step / 2, by + 3, x0 + off + step / 2, by + 3);
    line(col, 4, x0 + off, by + 3, x0 + off, by + 10);
  }

  // --- speed tape ---------------------------------------------------------------------------
  const tapeTop = num7 - idiv(num7, 2);
  const tapeW = idiv(W, 10);
  const tapeH = idiv(H, 2);
  const pointer = (w: number): [number, number][] => [
    [0, -10],
    [w - 10, -10],
    [w - 5, 0],
    [w - 10, 10],
    [0, 10],
  ];
  {
    g.fillStyle = TAPE_BG;
    g.fillRect(0, tapeTop, tapeW, tapeH);
    g.strokeStyle = WHITE;
    g.lineWidth = 2;
    g.strokeRect(0, tapeTop, tapeW, tapeH);
    g.translate(0, idiv(H, 2));
    const range = 26;
    const speed = inp.airspeed === 0 ? inp.groundspeed : inp.airspeed;
    const px = tapeH / range;
    const start = Math.trunc(speed - range / 2);
    const y = (v: number) => tapeTop - px * (v - start);
    if (start > inp.targetspeed) line(GREEN_HALF, 6, 0, tapeTop, tapeW, tapeTop);
    if (speed + range / 2 < inp.targetspeed) line(GREEN_HALF, 6, 0, tapeTop - px * range, tapeW, tapeTop - px * range);
    const end = Math.trunc(speed + range / 2);
    for (let v = start; v <= end; v++) {
      if (v === Math.trunc(inp.targetspeed) && inp.targetspeed !== 0) line(GREEN, 6, 0, y(v), tapeW, y(v));
      if (v % 5 === 0) {
        line(WHITE, 2, tapeW, y(v), tapeW - 10, y(v));
        text(String(v).padStart(5), num2, WHITE, 0, y(v) - 6 - num3);
      }
    }
    poly(pointer(tapeW), '#000', '#000');
    text(speed.toFixed(0) + opt.speedunit, 10, ALICE_BLUE, 0, -9);
    reset();
    const below = tapeTop + tapeH;
    if (opt.showAirspeed) {
      text(`AS ${inp.airspeed.toFixed(1)}${opt.speedunit}`, num2, inp.lowairspeed ? RED : WHITE, 1, below + 5);
    }
    text(`GS ${inp.groundspeed.toFixed(1)}${opt.speedunit}`, num2, inp.lowgroundspeed ? RED : WHITE, 1, below + num2 + 2 + 10);
  }

  // --- altitude tape + VSI ------------------------------------------------------------------
  const altLeft = W - idiv(W, 10);
  const below = tapeTop + tapeH;
  if (opt.showAlt) {
    g.fillStyle = TAPE_BG;
    g.fillRect(altLeft, tapeTop, tapeW, tapeH);
    g.strokeStyle = WHITE;
    g.lineWidth = 2;
    g.strokeRect(altLeft, tapeTop, tapeW, tapeH);
    g.translate(0, idiv(H, 2));
    const range = 26;
    const px = tapeH / range;
    const start = Math.trunc(inp.alt) - range / 2;
    const y = (v: number) => tapeTop - px * (v - start);
    if (start > inp.targetalt) line(GREEN_HALF, 6, altLeft, tapeTop, altLeft + tapeW, tapeTop);
    if (inp.alt + range / 2 < inp.targetalt) line(GREEN_HALF, 6, altLeft, tapeTop - px * range, altLeft + tapeW, tapeTop - px * range);
    for (let v = start; v <= inp.alt + range / 2; v++) {
      if (v === Math.round(inp.targetalt) && inp.targetalt !== 0) line(GREEN, 6, altLeft, y(v), altLeft + tapeW, y(v));
      if (v === Math.round(inp.groundalt) && inp.groundalt !== 0) {
        g.fillStyle = ALT_GROUND;
        g.fillRect(altLeft, y(v), tapeW, px * (v - start));
      }
      if (v % 5 === 0) {
        line(WHITE, 2, altLeft, y(v), altLeft + 10, y(v));
        text(String(v).padStart(5), num2, WHITE, altLeft, y(v) - 6 - num3);
      }
    }
    reset();

    // VSI: +-6 m/s over the tape height, left of the altitude tape
    const q = tapeW / 4;
    const mid = tapeTop + tapeH / 2;
    const vs = Math.max(Math.min(inp.verticalspeed, 6), -6);
    const dy = (vs / -12) * tapeH;
    let slant = 0;
    if (dy > 0) slant = mid + dy - q < mid ? -dy : -q;
    else if (dy < 0) slant = mid + dy + q > mid ? -dy : q;
    poly(
      [
        [altLeft, mid],
        [altLeft - q, mid],
        [altLeft - q, mid + dy + slant],
        [altLeft, mid + dy],
      ],
      'rgb(0,0,255)',
      null,
    );
    poly(
      [
        [altLeft, tapeTop],
        [altLeft - q, tapeTop + q],
        [altLeft - q, below - q],
        [altLeft, below],
      ],
      null,
      WHITE,
    );
    const per = tapeH / -12;
    for (let l = 1; l < 12; l++) line(WHITE, 2, altLeft - q, tapeTop - per * l, altLeft - tapeW / 8, tapeTop - per * l);

    g.translate(W, idiv(H, 2));
    g.rotate(Math.PI);
    poly(pointer(tapeW), '#000', '#000');
    reset();
    g.translate(0, idiv(H, 2));
    text(Math.trunc(inp.alt).toFixed(0) + ' ' + opt.altunit, 10, ALICE_BLUE, altLeft + 10, -9);
    reset();
  }

  // --- mode + distance to WP (MP draws these under the altitude tape) ------------------------
  {
    text(inp.mode, num2, opt.modeRecentlyChanged ? RED : WHITE, altLeft - 30, below + 5);
    let d = inp.disttowp;
    let unit = opt.distunit;
    if (d >= 1000) {
      unit = opt.distunit === 'm' ? 'k' : 'mi';
      d = Math.round((d / (opt.distunit === 'm' ? 1000 : 5280)) * 10) / 10;
    } else {
      d = Math.trunc(d);
    }
    text(`${d}${unit}>${inp.wpno}`, num2, WHITE, altLeft - 30, below + num2 + 2 + 10);
  }

  if (nanError) text('NaN Error ' + new Date().toLocaleTimeString(), idiv(H, 30) + 10, RED, 50, 50);

  // --- centre status texts + message --------------------------------------------------------
  reset();
  g.translate(idiv(W, 2), idiv(H, 2));
  // MP's fixed positions (-num7/3, -num7/6, -num7/5) overlap when several are active at once;
  // these are safety texts, so they are stacked downward from MP's first position instead.
  const centre: [string, number][] = [];
  if (!inp.armed) centre.push(['DISARMED', num2 + 10]);
  else if (opt.armedRecently) centre.push(['ARMED', num2 + 20]);
  if (inp.safetyactive) centre.push(['SAFE', num2 + 10]);
  if (inp.failsafe) centre.push(['FAILSAFE', num2 + 20]);
  let cy = -num7 / 3 - (centre.length > 1 ? (num2 + 20) * 0.5 : 0);
  for (const [s, size] of centre) {
    text(s, size, RED, -measure(s, size) / 2, cy);
    cy += size + 8;
  }
  if (inp.message) {
    const color = inp.messageSeverity <= 3 ? RED : inp.messageSeverity > 4 ? WHITE : YELLOW;
    let size = num2 + 10;
    while (size > 4 && measure(inp.message, size) > W - 100) size--; // MP calcfontsize
    text(inp.message, size, color, -measure(inp.message, size) / 2, num7 / 3);
  }
  g.restore();
}
