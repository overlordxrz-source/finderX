// Minimal canvas plotting + the animated signal/field views.

const C = {
  bg: "#0b0e12", grid: "#1a2029", axis: "#2f3843", tick: "#5b6573", label: "#7d8796",
  pt: "#8a95a5", blue: "#4c90f0", green: "#32a467", greenL: "#72ca9b", orange: "#ec9a3c",
  gold: "#f0b726", red: "#e76a6e", violet: "#a98fea", cyan: "#3fb6d9", tx: "#e4e8ee",
};
export const COLORS = C;

export const ease = (k) => (k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2);

// one animation per canvas; a new one cancels the old
const running = new WeakMap();
export function animate(key, ms, draw) {
  const prev = running.get(key);
  if (prev) prev.cancelled = true;
  const tok = { cancelled: false };
  running.set(key, tok);
  const t0 = performance.now();
  return new Promise((done) => {
    const step = (now) => {
      if (tok.cancelled) return done(false);
      const k = Math.min(1, (now - t0) / ms);
      draw(k);
      if (k < 1) requestAnimationFrame(step);
      else done(true);
    };
    requestAnimationFrame(step);
  });
}
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function niceStep(span, n) {
  const raw = span / Math.max(1, n);
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  const m = raw / p;
  return (m >= 5 ? 10 : m >= 2 ? 5 : m >= 1 ? 2 : 1) * p;
}
function ticks(lo, hi, n) {
  if (!(hi > lo)) return [];
  const st = niceStep(hi - lo, n);
  const out = [];
  for (let v = Math.ceil(lo / st) * st; v <= hi + st * 1e-9; v += st) out.push(+v.toPrecision(12));
  return out;
}
function logTicks(lo, hi) {
  const out = [];
  for (let e = Math.floor(Math.log10(lo)); e <= Math.ceil(Math.log10(hi)); e++) {
    for (const m of [1, 2, 5]) {
      const v = m * Math.pow(10, e);
      if (v >= lo && v <= hi) out.push(v);
    }
  }
  return out;
}
function fmt(v) {
  const a = Math.abs(v);
  if (a === 0) return "0";
  if (a >= 1000) return v.toFixed(0);
  if (a >= 10) return (+v.toFixed(1)).toString();
  if (a >= 1) return (+v.toFixed(2)).toString();
  return (+v.toPrecision(3)).toString();
}

export function quantile(arr, q) {
  const a = Float64Array.from(arr.filter(Number.isFinite)).sort();
  if (!a.length) return NaN;
  const i = Math.min(a.length - 1, Math.max(0, Math.floor(q * (a.length - 1))));
  return a[i];
}

export class Plot {
  constructor(canvas, o = {}) {
    this.c = canvas;
    this.o = { pad: [10, 12, 22, 46], ...o };
    this.ctx = canvas.getContext("2d");
    this.resize();
  }
  resize() {
    const r = this.c.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this.w = Math.max(10, r.width);
    this.h = Math.max(10, r.height);
    this.c.width = Math.round(this.w * dpr);
    this.c.height = Math.round(this.h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  set(o) { Object.assign(this.o, o); return this; }
  get L() { return this.o.pad[3]; }
  get R() { return this.w - this.o.pad[1]; }
  get T() { return this.o.pad[0]; }
  get B() { return this.h - this.o.pad[2]; }
  X(v) {
    const [a, b] = this.o.x;
    if (this.o.logx) return this.L + (Math.log(v / a) / Math.log(b / a)) * (this.R - this.L);
    return this.L + ((v - a) / (b - a)) * (this.R - this.L);
  }
  Y(v) {
    const [a, b] = this.o.y;
    const k = (v - a) / (b - a);
    return this.o.invertY ? this.T + k * (this.B - this.T) : this.B - k * (this.B - this.T);
  }
  clear() {
    const g = this.ctx;
    g.clearRect(0, 0, this.w, this.h);
    g.fillStyle = C.bg;
    g.fillRect(0, 0, this.w, this.h);
  }
  axes() {
    const g = this.ctx, o = this.o;
    g.save();
    g.font = "9px JetBrains Mono, monospace";
    g.fillStyle = C.tick;
    g.strokeStyle = C.grid;
    g.lineWidth = 1;
    const xt = o.logx ? logTicks(o.x[0], o.x[1]) : ticks(o.x[0], o.x[1], Math.max(3, Math.floor((this.R - this.L) / 80)));
    const yt = ticks(Math.min(...o.y), Math.max(...o.y), Math.max(2, Math.floor((this.B - this.T) / 34)));
    g.textAlign = "center";
    g.textBaseline = "top";
    for (const v of xt) {
      const x = Math.round(this.X(v)) + 0.5;
      g.beginPath(); g.moveTo(x, this.T); g.lineTo(x, this.B); g.stroke();
      g.fillText(o.xfmt ? o.xfmt(v) : fmt(v), x, this.B + 5);
    }
    g.textAlign = "right";
    g.textBaseline = "middle";
    for (const v of yt) {
      const y = Math.round(this.Y(v)) + 0.5;
      g.beginPath(); g.moveTo(this.L, y); g.lineTo(this.R, y); g.stroke();
      g.fillText(o.yfmt ? o.yfmt(v) : fmt(v), this.L - 5, y);
    }
    g.strokeStyle = C.axis;
    g.strokeRect(this.L + 0.5, this.T + 0.5, this.R - this.L, this.B - this.T);
    g.restore();
  }
  clip(fn) {
    const g = this.ctx;
    g.save();
    g.beginPath();
    g.rect(this.L, this.T, this.R - this.L, this.B - this.T);
    g.clip();
    fn(g);
    g.restore();
  }
  points(xs, ys, { color = C.pt, r = 1.2, alpha = 0.7, upto = xs.length } = {}) {
    this.clip((g) => {
      g.fillStyle = color;
      g.globalAlpha = alpha;
      const s = r * 2;
      for (let i = 0; i < upto; i++) {
        const x = this.X(xs[i]), y = this.Y(ys[i]);
        if (Number.isFinite(x) && Number.isFinite(y)) g.fillRect(x - r, y - r, s, s);
      }
    });
  }
  line(xs, ys, { color = C.blue, w = 1.2, alpha = 1, dash = null, upto = xs.length, gapX = Infinity } = {}) {
    this.clip((g) => {
      g.strokeStyle = color;
      g.lineWidth = w;
      g.globalAlpha = alpha;
      if (dash) g.setLineDash(dash);
      g.beginPath();
      let pen = false;
      for (let i = 0; i < upto; i++) {
        const x = this.X(xs[i]), y = this.Y(ys[i]);
        if (!Number.isFinite(x) || !Number.isFinite(y) || (i && xs[i] - xs[i - 1] > gapX)) { pen = false; if (!Number.isFinite(x)) continue; }
        if (!pen) { g.moveTo(x, y); pen = true; } else g.lineTo(x, y);
      }
      g.stroke();
    });
  }
  vline(v, { color = C.green, w = 1, dash = [3, 3], alpha = 0.9, label = null } = {}) {
    const x = this.X(v);
    this.clip((g) => {
      g.strokeStyle = color; g.lineWidth = w; g.globalAlpha = alpha;
      if (dash) g.setLineDash(dash);
      g.beginPath(); g.moveTo(x, this.T); g.lineTo(x, this.B); g.stroke();
    });
    if (label) this.text(label, x + 4, this.T + 4, { color, align: "left" });
  }
  hline(v, { color = C.axis, w = 1, dash = [3, 3], alpha = 1 } = {}) {
    const y = this.Y(v);
    this.clip((g) => {
      g.strokeStyle = color; g.lineWidth = w; g.globalAlpha = alpha;
      if (dash) g.setLineDash(dash);
      g.beginPath(); g.moveTo(this.L, y); g.lineTo(this.R, y); g.stroke();
    });
  }
  text(s, x, y, { color = C.label, align = "left", base = "top", size = 9 } = {}) {
    const g = this.ctx;
    g.save();
    g.font = `${size}px JetBrains Mono, monospace`;
    g.fillStyle = color; g.textAlign = align; g.textBaseline = base;
    g.fillText(s, x, y);
    g.restore();
  }
  xlabel(s) { this.text(s, this.R, this.B - 12, { align: "right", color: C.tick }); }
}

// ── helpers ──────────────────────────────────────────────────────────
export function phaseOf(t, P, t0) {
  const out = new Float64Array(t.length);
  for (let i = 0; i < t.length; i++) out[i] = ((((t[i] - t0) / P + 0.5) % 1) + 1) % 1 - 0.5;
  return out;
}
export function binXY(xs, ys, lo, hi, n) {
  const sum = new Float64Array(n), cnt = new Uint32Array(n);
  for (let i = 0; i < xs.length; i++) {
    const k = Math.floor(((xs[i] - lo) / (hi - lo)) * n);
    if (k >= 0 && k < n && Number.isFinite(ys[i])) { sum[k] += ys[i]; cnt[k]++; }
  }
  const bx = [], by = [];
  for (let k = 0; k < n; k++) if (cnt[k]) { bx.push(lo + ((k + 0.5) * (hi - lo)) / n); by.push(sum[k] / cnt[k]); }
  return [bx, by];
}
function ylim(f, lo = 0.003, hi = 0.997, padFrac = 0.12) {
  const a = quantile(f, lo), b = quantile(f, hi);
  const pad = (b - a) * padFrac || 1e-3;
  return [a - pad, b + pad];
}
const ppt = (v) => ((v - 1) * 1e3).toFixed(Math.abs(v - 1) < 0.01 ? 1 : 0);

// ── signal view ──────────────────────────────────────────────────────
export async function drawSignal(cand, els) {
  const pl = cand.plots;
  if (!pl) return;
  const m = cand.metrics || {};
  const isVar = cand.engine === "variable";
  const P = isVar ? m.true_period : m.period;
  const t0 = isVar ? pl.raw.t[0] : m.t0;
  const dur = m.duration || 0;
  const plots = Object.fromEntries(Object.entries(els.canvas).map(([k, c]) => [k, new Plot(c)]));

  // 1 · raw light curve + trend, drawn left→right
  const raw = pl.raw;
  const rp = plots.raw.set({ x: [raw.t[0], raw.t[raw.t.length - 1]], y: ylim(raw.f), yfmt: ppt, pad: [10, 12, 22, 46] });
  const tmarks = [];
  if (!isVar && P) for (let k = Math.ceil((raw.t[0] - t0) / P); t0 + k * P <= raw.t[raw.t.length - 1]; k++) tmarks.push(t0 + k * P);
  els.titles.raw.innerHTML = `<b>${pl.sectors.map((s) => "S" + s).join(" ")}</b> · ${raw.t.length.toLocaleString()} bins · ppt`;
  const drawRaw = (k) => {
    rp.clear(); rp.axes();
    const n = Math.floor(raw.t.length * ease(k));
    rp.points(raw.t, raw.f, { upto: n, r: 1.1, alpha: 0.75 });
    if (k > 0.6) rp.line(raw.t, raw.trend, { color: C.blue, w: 1, alpha: Math.min(1, (k - 0.6) / 0.4), upto: n, gapX: 0.5 });
    if (k >= 1) for (const tm of tmarks) {
      const x = rp.X(tm);
      rp.ctx.fillStyle = C.green; rp.ctx.fillRect(x - 0.5, rp.B - 7, 1.5, 7);
    }
  };

  // 2 · periodogram sweep
  const pg = isVar ? pl.ls : pl.bls;
  const pp = plots.pgram;
  const pgOk = pg && pg.p && pg.p.length > 2;
  if (pgOk) {
    const pmax = Math.max(...pg.power);
    pp.set({ x: [pg.p[0], pg.p[pg.p.length - 1]], y: [Math.min(0, Math.min(...pg.power)), pmax * 1.12], logx: true, pad: [10, 12, 22, 40] });
  }
  els.titles.pgram.textContent = isVar ? "LOMB-SCARGLE · power vs period (d)" : "BLS · SDE vs period (d)";
  const drawPgram = (k) => {
    pp.clear();
    if (!pgOk) return pp.text("no periodogram", 50, 20);
    pp.axes();
    const n = Math.floor(pg.p.length * ease(k));
    pp.line(pg.p, pg.power, { color: C.pt, w: 1, upto: n });
    if (k >= 1 && P) {
      const peak = isVar ? m.period : P;
      pp.vline(peak, { color: C.green, dash: null, w: 1.5, label: `${peak.toFixed(4)} d` });
      for (const h of [peak / 2, peak * 2]) if (h > pg.p[0] && h < pg.p[pg.p.length - 1]) pp.vline(h, { color: C.green, alpha: 0.35 });
    }
  };

  // 3 · fold morph: points glide from time to phase
  const src = isVar ? raw : pl.detr;
  const ph = phaseOf(src.t, P, t0);
  const tlo = src.t[0], thi = src.t[src.t.length - 1];
  const xt = Float64Array.from(src.t, (t) => (t - tlo) / (thi - tlo) - 0.5);
  const fp = plots.fold.set({ x: [-0.5, 0.5], y: ylim(src.f, 0.002, 0.998, 0.15), yfmt: ppt, pad: [10, 12, 22, 46] });
  els.titles.fold.innerHTML = `PHASE FOLD · <b>P ${P.toFixed(5)} d</b>`;
  const [bx, by] = binXY(ph, src.f, -0.5, 0.5, isVar ? 80 : 160);
  const xs = new Float64Array(src.t.length);
  const drawFold = (k) => {
    fp.clear(); fp.axes();
    const e = ease(k);
    for (let i = 0; i < xs.length; i++) xs[i] = xt[i] + (ph[i] - xt[i]) * e;
    fp.points(xs, src.f, { r: 1, alpha: 0.35 + 0.25 * (1 - e) });
    if (k >= 1) {
      fp.line(bx, by, { color: isVar ? C.gold : C.green, w: 1.6 });
      if (!isVar && m.depth) {
        const q = dur / P / 2;
        fp.line([-0.5, -q, -q, q, q, 0.5], [1, 1, 1 - m.depth, 1 - m.depth, 1, 1], { color: C.blue, w: 1, alpha: 0.8, dash: [4, 3] });
      }
    } else {
      fp.text(e < 0.5 ? "TIME" : "PHASE", fp.R - 4, fp.T + 4, { align: "right", color: C.tick });
    }
  };

  // 4 · zoom: odd vs even (transits) or two-cycle profile (variables)
  const zp = plots.zoom;
  let drawZoom;
  if (!isVar) {
    const win = Math.max(3 * dur, 0.15) * 24;
    const hx = [], hy = [], par = [];
    for (let i = 0; i < src.t.length; i++) {
      const h = ph[i] * P * 24;
      if (Math.abs(h) <= win) {
        hx.push(h); hy.push(src.f[i]);
        par.push(Math.round((src.t[i] - t0) / P) & 1);
      }
    }
    const odd = [[], []], even = [[], []];
    hx.forEach((h, i) => {
      const dst = par[i] ? odd : even;
      dst[0].push(h);
      dst[1].push(hy[i]);
    });
    const nb = Math.max(12, Math.round((2 * win) / Math.max(dur * 24 / 6, 0.05)));
    const [ox, oy] = binXY(odd[0], odd[1], -win, win, nb);
    const [ex, ey] = binXY(even[0], even[1], -win, win, nb);
    const lo = Math.min(quantile(hy, 0.01), 1 - 1.4 * (m.depth || 0)), hi = quantile(hy, 0.995);
    zp.set({ x: [-win, win], y: [lo - (hi - lo) * 0.1, hi + (hi - lo) * 0.1], yfmt: ppt, pad: [10, 12, 22, 46] });
    els.titles.zoom.innerHTML = `TRANSIT ZOOM · hours · odd <i class="sw sw-odd"></i> even <i class="sw sw-even"></i>`;
    drawZoom = (k) => {
      zp.clear(); zp.axes();
      zp.points(hx, hy, { r: 1.1, alpha: 0.3 * ease(k) });
      if (k >= 1) {
        zp.line(ox, oy, { color: C.blue, w: 1.5 });
        zp.line(ex, ey, { color: C.orange, w: 1.5 });
        zp.hline(1 - m.depth, { color: C.green, alpha: 0.6 });
        zp.vline(-dur * 12, { color: C.tick, alpha: 0.5 });
        zp.vline(dur * 12, { color: C.tick, alpha: 0.5 });
      }
    };
  } else {
    const ph2 = Array.from(ph, (p) => p + 0.5);
    const xs2 = ph2.concat(ph2.map((p) => p + 1));
    const ys2 = Array.from(raw.f).concat(Array.from(raw.f));
    const [b2x, b2y] = binXY(xs2, ys2, 0, 2, 120);
    zp.set({ x: [0, 2], y: ylim(raw.f, 0.002, 0.998, 0.15), yfmt: ppt, pad: [10, 12, 22, 46] });
    els.titles.zoom.innerHTML = `TWO CYCLES · <b>${m.type_guess}</b> · ${m.type_label}`;
    drawZoom = (k) => {
      zp.clear(); zp.axes();
      zp.points(xs2, ys2, { r: 1, alpha: 0.25 * ease(k) });
      if (k >= 1) zp.line(b2x, b2y, { color: C.gold, w: 1.6 });
    };
  }

  // choreography: light curve → periodogram → fold → zoom
  drawRaw(0); drawPgram(0); drawFold(0); drawZoom(0);
  els.replay = async () => {
    if (!(await animate(els.canvas.raw, 700, drawRaw))) return;
    if (!(await animate(els.canvas.pgram, 600, drawPgram))) return;
    await sleep(120);
    if (!(await animate(els.canvas.fold, 1100, drawFold))) return;
    await animate(els.canvas.zoom, 450, drawZoom);
  };
  els.redraw = () => {
    for (const p of Object.values(plots)) p.resize();
    drawRaw(1); drawPgram(1); drawFold(1); drawZoom(1);
  };
  return els.replay();
}

// ── vetting tests table ──────────────────────────────────────────────
export function tests(cand) {
  const m = cand.metrics || {};
  const rows = [];
  const add = (state, name, value, desc) => rows.push({ state, name, value, desc });
  if (cand.engine === "variable") {
    add(m.power > 0.4 ? "ok" : "warn", "periodogram power", m.power?.toFixed(2), "fraction of variance explained by one sinusoid");
    add(m.fap < 1e-10 ? "ok" : "warn", "false-alarm probability", m.fap?.toExponential(1), "Baluev approximation");
    add("ok", "semi-amplitude", `${(m.amplitude_ppm / 1e3).toFixed(2)} ppt`, `point scatter ${(m.noise_ppm / 1e3).toFixed(2)} ppt`);
    add(Math.abs(m.skew) > 0.35 ? "warn" : "ok", "profile skew", m.skew?.toFixed(2), "≈0 sinusoidal · <0 sharp minima (eclipses) · >0 sharp maxima (pulsators)");
    add("na", "type guess", m.type_guess, m.type_label);
    const crowd = m.star?.crowdsap;
    add(crowd == null ? "na" : crowd >= 0.8 ? "ok" : "warn", "aperture contamination", crowd == null ? "—" : `${((1 - crowd) * 100).toFixed(0)}%`, "flux in the aperture from other stars");
    return rows;
  }
  add(m.snr >= 12 ? "ok" : m.snr >= 8 ? "warn" : "bad", "signal-to-noise (red)", m.snr?.toFixed(1), "depth vs scatter of duration-long bins");
  add(m.sde >= 12 ? "ok" : "warn", "signal detection efficiency", m.sde?.toFixed(1), "BLS peak height above the periodogram noise");
  add(m.n_transits >= 3 ? "ok" : "warn", "transits observed", m.n_transits, "≥3 pins the period down");
  add(m.odd_even_sigma < 3 ? "ok" : m.odd_even_sigma < 4 ? "warn" : "bad", "odd / even depths", `${m.odd_even_sigma?.toFixed(1)}σ`, "different depths → eclipsing binary at 2× period");
  add(m.secondary_snr < 3 ? "ok" : m.secondary_snr < 5 ? "warn" : "bad", "secondary eclipse", `${m.secondary_snr?.toFixed(1)}σ`, "a dip at phase 0.5 means a self-luminous companion");
  add(m.shape_ratio == null ? "na" : m.shape_ratio > 0.6 ? "ok" : m.shape_ratio > 0.45 ? "warn" : "bad", "transit shape", m.shape_ratio == null ? "—" : m.shape_ratio.toFixed(2), "box/U ≈ 1 (planet) · V ≈ 0.33 (grazing binary)");
  add(m.centroid_sigma == null ? "na" : m.centroid_sigma < 3 ? "ok" : m.centroid_sigma < 5 ? "warn" : "bad", "centroid shift", m.centroid_sigma == null ? "—" : `${m.centroid_sigma.toFixed(1)}σ`, "image moves during transit → light from a neighbour");
  add(m.duration_ratio == null ? "na" : m.duration_ratio < 1.5 && m.duration_ratio > 0.2 ? "ok" : "warn", "duration vs star", m.duration_ratio == null ? "—" : m.duration_ratio.toFixed(2), "observed / expected for a central transit of this star");
  add(m.rp_earth == null ? "na" : m.rp_earth < 16 ? "ok" : m.rp_earth < 24 ? "warn" : "bad", "implied radius", m.rp_earth == null ? "—" : `${m.rp_earth.toFixed(1)} R⊕`, "> 2 R♃ cannot be a planet");
  add(m.single_event_frac < 0.5 ? "ok" : m.single_event_frac < 0.65 ? "warn" : "bad", "single-event dominance", m.single_event_frac?.toFixed(2), "share of the signal carried by one event");
  add(m.edge_frac < 0.3 ? "ok" : m.edge_frac < 0.5 ? "warn" : "bad", "near data gaps", m.edge_frac?.toFixed(2), "systematics cluster at orbit edges");
  const nc = (m.contaminants || []).length;
  add(nc ? "warn" : "ok", "contaminating neighbours", nc, "Gaia stars within 2 TESS pixels bright enough to fake the dip");
  return rows;
}

// ── field view: HR diagram / WISE colours ────────────────────────────
export function drawField(cand, canvas, title, siblings = []) {
  const pl = cand.plots || {};
  const p = new Plot(canvas);
  const m = cand.metrics || {};
  if (cand.engine === "stellar" && pl.hr) {
    p.set({ x: [-0.6, 4.6], y: [-3, 18], invertY: true, pad: [12, 14, 26, 40] });
    title.innerHTML = `HERTZSPRUNG–RUSSELL · M<sub>G</sub> vs BP−RP · <b>${pl.field.n.toLocaleString()} stars</b> in field`;
    const bx = pl.hr.map((d) => d[0]), by = pl.hr.map((d) => d[1]);
    return animate(canvas, 900, (k) => {
      p.clear(); p.axes();
      p.points(bx, by, { r: 0.9, alpha: 0.45, upto: Math.floor(bx.length * ease(k)) });
      p.line([-0.6, 2.8], [3.1 * -0.6 + 9.2, 3.1 * 2.8 + 9.2], { color: C.cyan, alpha: 0.35, dash: [4, 4] });
      p.text("white-dwarf boundary", p.X(0.3), p.Y(3.1 * 0.3 + 9.2) + 6, { color: C.cyan });
      for (const s of siblings) {
        if (s.metrics?.bp_rp == null || s.id === cand.id) continue;
        p.points([s.metrics.bp_rp], [s.metrics.M_G], { color: C.cyan, r: 2.2, alpha: 0.8 });
      }
      if (m.bp_rp != null && k >= 1) reticle(p, p.X(m.bp_rp), p.Y(m.M_G), cand.title.replace("Gaia DR3 ", ""));
      p.xlabel("BP − RP");
    });
  }
  if (cand.engine === "galaxy" && pl.wise) {
    p.set({ x: [-0.5, 6], y: [-0.6, 2.4], pad: [12, 14, 26, 40] });
    title.innerHTML = `WISE COLOURS · W1−W2 vs W2−W3 · <b>${pl.wise.length.toLocaleString()} sources</b>`;
    const bx = pl.wise.map((d) => d[0]), by = pl.wise.map((d) => d[1]);
    return animate(canvas, 900, (k) => {
      p.clear(); p.axes();
      p.points(bx, by, { r: 0.9, alpha: 0.4, upto: Math.floor(bx.length * ease(k)) });
      p.hline(0.8, { color: C.violet, alpha: 0.6 });
      p.text("AGN (Stern+12)  W1−W2 ≥ 0.8", p.X(-0.4), p.Y(0.8) - 12, { color: C.violet });
      p.text("stars", p.X(0.1), p.Y(-0.1), { color: C.tick });
      p.text("galaxies", p.X(3.2), p.Y(0.25), { color: C.tick });
      for (const s of siblings) {
        const sm = s.metrics || {};
        if (sm["W1-W2"] == null || sm.W3 == null || s.id === cand.id) continue;
        p.points([sm.W2 - sm.W3], [sm["W1-W2"]], { color: C.violet, r: 2.2, alpha: 0.85 });
      }
      if (m["W1-W2"] != null && m.W3 != null && k >= 1) reticle(p, p.X(m.W2 - m.W3), p.Y(m["W1-W2"]), cand.title);
      else if (k >= 1) p.text("this source has no W3 detection", p.R - 8, p.T + 8, { align: "right", color: C.tick });
      p.xlabel("W2 − W3");
    });
  }
  p.clear();
  p.text("no field context for this candidate", 20, 20);
}

function reticle(p, x, y, label) {
  const g = p.ctx;
  g.save();
  g.strokeStyle = C.green;
  g.lineWidth = 1.5;
  const r = 9, l = 5;
  for (const [sx, sy] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
    g.beginPath();
    g.moveTo(x + sx * r, y + sy * (r - l)); g.lineTo(x + sx * r, y + sy * r); g.lineTo(x + sx * (r - l), y + sy * r);
    g.stroke();
  }
  g.fillStyle = C.green;
  g.fillRect(x - 1.5, y - 1.5, 3, 3);
  g.restore();
  p.text(label, x + 14, y - 4, { color: C.greenL, size: 10 });
}
