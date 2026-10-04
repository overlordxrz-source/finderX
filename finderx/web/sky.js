// Aladin Lite wrapper + an FX canvas for scan pings, cone sweeps and lock-on.

const SURVEYS = [
  ["DSS", "P/DSS2/color"],
  ["PS1", "P/PanSTARRS/DR1/color-z-zg-g"],
  ["2MASS", "P/2MASS/color"],
  ["WISE", "P/allWISE/color"],
  ["GAIA", "P/DM/flux-color-Rp-G-Bp/I/355/gaiadr3"],
];

const ALADIN_JS = "https://aladin.cds.unistra.fr/AladinLite/api/v3/latest/aladin.js";

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const el = document.createElement("script");
    el.src = src;
    el.charset = "utf-8";
    el.onload = resolve;
    el.onerror = () => { el.remove(); reject(new Error("load failed: " + src)); };
    document.head.appendChild(el);
  });
}

const KIND_STYLE = {
  planet: { color: "#32a467", shape: "circle", size: 14 },
  eb: { color: "#ec9a3c", shape: "circle", size: 12 },
  var: { color: "#f0b726", shape: "circle", size: 11 },
  star: { color: "#3fb6d9", shape: "rhomb", size: 12 },
  deep: { color: "#a98fea", shape: "square", size: 11 },
  sso: { color: "#ff8a65", shape: "circle", size: 8 },
  neocp: { color: "#e76a6e", shape: "triangle", size: 12 },
  exam: { color: "#5b6573", shape: "circle", size: 6 },
};

export function kindGroup(c) {
  const k = c.kind || "";
  if (c.engine === "variable") return "var";
  if (c.engine === "transit") return k.includes("planet") ? "planet" : "eb";
  if (c.engine === "stellar") return "star";
  if (c.engine === "galaxy") return "deep";
  return "exam";
}

export class Sky {
  constructor(el, fx, hud, surveysEl, onPick) {
    this.el = el;
    this.fx = fx;
    this.hud = hud;
    this.onPick = onPick;
    this.a = null;
    this.cats = {};
    this.pings = [];
    this.sweeps = new Map();
    this.lock = null;
    this.ready = this.init(surveysEl);
    requestAnimationFrame(this.loop.bind(this));
  }

  async init(surveysEl) {
    for (let i = 0; !window.A && i < 3; i++) await loadScript(ALADIN_JS).catch(() => new Promise((r) => setTimeout(r, 1500 * (i + 1))));
    if (!window.A) throw new Error("Aladin Lite could not be loaded");
    await window.A.init;
    this.a = window.A.aladin(this.el, {
      survey: SURVEYS[0][1], fov: 60, target: "83.82 -5.39", cooFrame: "ICRS",
      showReticle: false, showZoomControl: false, showFullscreenControl: false, showLayersControl: false,
      showGotoControl: false, showFrame: false, showCooGridControl: false, showSettingsControl: false,
      showShareControl: false, showProjectionControl: false, showSimbadPointerControl: false,
      showStatusBar: false, showContextMenu: false, showCooLocation: false, showFov: false,
      backgroundColor: "rgb(5,7,10)",
    });
    for (const [k, st] of Object.entries(KIND_STYLE)) {
      const cat = window.A.catalog({ name: k, color: st.color, shape: st.shape, sourceSize: st.size, onClick: "showPopup" });
      this.a.addCatalog(cat);
      this.cats[k] = cat;
    }
    this.vectors = window.A.graphicOverlay({ color: "#ff8a65", lineWidth: 1 });
    this.a.addOverlay(this.vectors);
    this.a.on("objectClicked", (o) => { if (o?.data?.cid) this.onPick(o.data.cid); });
    this.a.on("positionChanged", () => this.updateHud());
    this.a.on("zoomChanged", () => this.updateHud());
    surveysEl.innerHTML = SURVEYS.map(([n, id]) => `<button data-s="${id}">${n}</button>`).join("");
    this.surveysEl = surveysEl;
    this.mark(SURVEYS[0][1]);
    surveysEl.addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      this.survey(b.dataset.s);
    });
    this.updateHud();
  }

  survey(id) {
    if (!this.a || id === this.current) return;
    this.current = id;
    this.mark(id);
    try { (this.a.setBaseImageLayer || this.a.setImageSurvey).call(this.a, id); } catch (e) { console.warn(e); }
  }

  mark(id) {
    this.current = this.current || id;
    this.surveysEl?.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x.dataset.s === id));
  }

  updateHud() {
    if (!this.a) return;
    const [ra, dec] = this.a.getRaDec();
    const fov = this.a.getFov()[0];
    this.hud.textContent = `RA ${ra.toFixed(4)}°  DEC ${dec >= 0 ? "+" : ""}${dec.toFixed(4)}°  FOV ${fov >= 1 ? fov.toFixed(1) + "°" : (fov * 60).toFixed(1) + "′"}`;
  }

  // whole-sky Aitoff map: patrol pings land all over it
  allsky() {
    if (!this.a) return;
    try { this.a.setProjection("AIT"); } catch { /* older Aladin */ }
    this.a.setFoV(360);
    this.a.gotoRaDec(0, 0);
  }

  goto(ra, dec, fov) {
    if (!this.a) return;
    if (fov) {
      if (this.a.zoomToFoV) this.a.zoomToFoV(fov, 1.2); else this.a.setFoV(fov);
    }
    if (this.a.animateToRaDec) this.a.animateToRaDec(ra, dec, 1.2); else this.a.gotoRaDec(ra, dec);
  }

  add(group, ra, dec, data) {
    const cat = this.cats[group];
    if (!cat || ra == null) return;
    cat.addSources([window.A.source(ra, dec, data)]);
  }

  clear(group) { this.cats[group]?.removeAll?.(); if (group === "sso") this.vectors?.removeAll?.(); }

  setCandidates(list) {
    if (!this.a) return;
    for (const g of ["planet", "eb", "var", "star", "deep"]) this.clear(g);
    for (const c of list) this.add(kindGroup(c), c.ra, c.dec, { cid: c.id, name: c.id, popupTitle: c.id, popupDesc: `${c.title}<br>${c.subtitle || ""}` });
  }

  motion(ra, dec, draH, ddecH) {
    // one-day track: arcsec/h × 24 → deg
    if (!this.vectors || draH == null) return;
    const k = 24 / 3600;
    const cosd = Math.max(Math.cos((dec * Math.PI) / 180), 1e-3);
    this.vectors.add(window.A.polyline([[ra, dec], [ra + (draH * k) / cosd, dec + ddecH * k]]));
  }

  // ── FX ──────────────────────────────────────────────────────────────
  ping(ra, dec, color = "#4c90f0") { this.pings.push({ ra, dec, color, t0: performance.now() }); }
  sweep(job, ra, dec, radius) { this.sweeps.set(job, { ra, dec, radius, t0: performance.now() }); }
  endSweep(job) { this.sweeps.delete(job); }
  lockOn(ra, dec) { this.lock = ra == null ? null : { ra, dec, t0: performance.now() }; }

  px(ra, dec) {
    try {
      const p = this.a.world2pix(ra, dec);
      return p && Number.isFinite(p[0]) ? p : null;
    } catch { return null; }
  }

  loop(now) {
    requestAnimationFrame(this.loop.bind(this));
    const c = this.fx;
    const r = c.getBoundingClientRect();
    if (!r.width || !this.a) return;
    const dpr = window.devicePixelRatio || 1;
    if (c.width !== Math.round(r.width * dpr)) { c.width = Math.round(r.width * dpr); c.height = Math.round(r.height * dpr); }
    const g = c.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, r.width, r.height);

    // cone sweeps (radar wedge inside the search radius)
    for (const s of this.sweeps.values()) {
      const p = this.px(s.ra, s.dec), q = this.px(s.ra, Math.min(89.9, s.dec + s.radius));
      if (!p || !q) continue;
      const R = Math.max(14, Math.hypot(q[0] - p[0], q[1] - p[1]));
      const ang = ((now - s.t0) / 1400) * Math.PI * 2;
      const grad = g.createConicGradient ? g.createConicGradient(ang - 0.9, p[0], p[1]) : null;
      g.save();
      g.beginPath(); g.arc(p[0], p[1], R, 0, Math.PI * 2);
      g.strokeStyle = "rgba(76,144,240,.55)"; g.setLineDash([4, 4]); g.lineWidth = 1; g.stroke();
      g.setLineDash([]);
      if (grad) {
        grad.addColorStop(0, "rgba(76,144,240,0)");
        grad.addColorStop(0.14, "rgba(76,144,240,.28)");
        grad.addColorStop(0.15, "rgba(76,144,240,0)");
        g.fillStyle = grad;
        g.beginPath(); g.moveTo(p[0], p[1]); g.arc(p[0], p[1], R, 0, Math.PI * 2); g.fill();
      }
      g.restore();
    }

    // pings: expanding rings where the pipeline is looking right now
    this.pings = this.pings.filter((pg) => now - pg.t0 < 1600);
    for (const pg of this.pings) {
      const p = this.px(pg.ra, pg.dec);
      if (!p) continue;
      const k = (now - pg.t0) / 1600;
      g.beginPath();
      g.arc(p[0], p[1], 4 + 26 * k, 0, Math.PI * 2);
      g.strokeStyle = pg.color; g.globalAlpha = 1 - k; g.lineWidth = 1.5; g.stroke();
      g.globalAlpha = 1;
      g.fillStyle = pg.color; g.fillRect(p[0] - 1.5, p[1] - 1.5, 3, 3);
    }

    // lock-on brackets for the selected candidate
    if (this.lock) {
      const p = this.px(this.lock.ra, this.lock.dec);
      if (p) {
        const k = Math.min(1, (now - this.lock.t0) / 450);
        const e = 1 - Math.pow(1 - k, 3);
        const s = 46 - 26 * e, l = 7;
        g.save();
        g.strokeStyle = `rgba(114,202,155,${0.4 + 0.6 * e})`;
        g.lineWidth = 1.5;
        for (const [sx, sy] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
          g.beginPath();
          g.moveTo(p[0] + sx * s, p[1] + sy * (s - l)); g.lineTo(p[0] + sx * s, p[1] + sy * s); g.lineTo(p[0] + sx * (s - l), p[1] + sy * s);
          g.stroke();
        }
        if (k >= 1) {
          g.globalAlpha = 0.35 + 0.25 * Math.sin(now / 300);
          g.beginPath(); g.moveTo(p[0] - s - 10, p[1]); g.lineTo(p[0] - s + 3, p[1]); g.moveTo(p[0] + s - 3, p[1]); g.lineTo(p[0] + s + 10, p[1]); g.stroke();
        }
        g.restore();
      }
    }
  }
}
