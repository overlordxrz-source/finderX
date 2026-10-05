// finderX terminal — controller: live stream, operations, queue, dossier, console.
import { Sky, kindGroup } from "./sky.js";
import { drawSignal, tests, drawField, animate, ease } from "./plot.js";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (v, d = 2) => (v == null || !Number.isFinite(+v) ? "—" : (+v).toFixed(d));

async function api(path, opts) {
  const r = await fetch(path, opts);
  const ct = r.headers.get("content-type") || "";
  const body = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error(body?.detail || body || r.statusText);
  return body;
}
const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem("fx." + k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("fx." + k, JSON.stringify(v)); } catch { /* private mode */ } },
};

const S = {
  cands: [],
  filter: { status: "new,flagged", engine: "", known: store.get("known", false), order: store.get("order", "score") },
  sel: null,
  detail: null,
  jobs: new Map(),
  patrol: null,
  engine: "transit",
  mode: { transit: "star", solar: "watch" },
  stages: [],
  solar: { neocp: [], approaches: [], sso: [], gone: [] },
  view: "sky",
  stats: {},
  sig: null,
  history: store.get("history", []),
};

// ── flags explained ──────────────────────────────────────────────────
const FLAGS = {
  SECONDARY_ECLIPSE: ["bad", "dip at phase 0.5 — the companion shines, so it is a star"],
  ODD_EVEN_MISMATCH: ["bad", "alternate dips differ — an eclipsing binary at twice the period"],
  TOO_LARGE_FOR_PLANET: ["bad", "implied size is beyond any known planet"],
  V_SHAPED: ["bad", "V-shaped dip — typical of grazing binaries"],
  SINUSOID_PREFERRED: ["warn", "a smooth sinusoid fits as well as a transit"],
  CENTROID_SHIFT: ["warn", "the star image shifts in transit — the dip may come from a neighbour"],
  LONG_DURATION: ["warn", "longer than a planet can transit this star (or the star is bigger than catalogued)"],
  SINGLE_EVENT_DOMINATED: ["warn", "one event carries most of the signal — could be a glitch"],
  NEAR_DATA_GAPS: ["warn", "events sit next to data gaps where systematics live"],
  TESS_ORBIT_ALIAS: ["warn", "period close to TESS's 13.7-day orbit"],
  CROWDED_APERTURE: ["warn", "other stars contribute >20% of the aperture flux"],
  EVOLVED_HOST: ["warn", "host is a giant — its noise often mimics transits"],
  COMMON_MODE_SYSTEMATIC: ["bad", "other stars in this sector dip at the same instants — a spacecraft systematic"],
  SEEN_IN_SEVERAL_SECTORS: ["good", "the same transits recur in independent TESS sectors"],
  ONE_SECTOR_ONLY: ["warn", "all transits fall in one sector even though others were searched"],
  SINGLE_SECTOR_ONLY: ["warn", "TESS has observed this star in only one sector so far"],
  VARIABLE_HOST: ["warn", "the star itself pulsates or rotates; the dip was found after removing that signal"],
  NEARBY_CONTAMINANT: ["warn", "a nearby Gaia star could produce this dip if it were an eclipsing binary"],
  VSX_ENTRY_LACKS_PERIOD: ["good", "VSX knows the star but has no period — you can add it"],
  NOT_IN_SIMBAD: ["good", "no SIMBAD entry at all"],
  BARELY_STUDIED: ["good", "SIMBAD lists ≤ 2 papers"],
  NOT_IN_WD_CATALOG: ["good", "missing from the Gentile Fusillo 2021 white-dwarf catalogue"],
  POSSIBLY_UNBOUND: ["good", "faster than ~550 km/s relative to the Galaxy — near or above escape speed"],
  HIGH_VELOCITY: ["info", "tangential speed > 400 km/s — halo or runaway star"],
  NEARBY: ["info", "within 50 parsecs"],
  HIDDEN_COMPANION: ["info", "astrometric wobble (RUWE > 2) — unseen companion likely"],
  ULTRACOOL: ["info", "very faint and red — late-M / L dwarf candidate"],
  WHITE_DWARF: ["info", "below the main sequence: white-dwarf locus"],
  GAIA_QSO: ["info", "Gaia DR3 classifies it as a quasar candidate"],
  GAIA_GALAXY: ["info", "Gaia DR3 classifies it as a galaxy candidate"],
  WISE_AGN_COLOURS: ["info", "mid-IR colour W1−W2 ≥ 0.8: hot AGN dust"],
  ZERO_ASTROMETRIC_MOTION: ["good", "no parallax or proper motion — extragalactic"],
  MOVES_LIKE_A_STAR: ["bad", "measurable parallax or proper motion — probably a star"],
  HIGH_REDSHIFT: ["good", "Gaia redshift above 3.5 — rare, early-universe quasar"],
  NO_GAIA_COUNTERPART: ["info", "invisible to Gaia — possibly dust-obscured"],
  NO_CATALOGUE_ENTRY: ["good", "nothing in Milliquas, SIMBAD or NED at this position"],
  NOT_CLASSIFIED_EXTRAGALACTIC: ["good", "catalogued, but nobody has classified it as a galaxy/quasar"],
};
const FC = { bad: "var(--red)", warn: "var(--gold)", good: "var(--green)", info: "var(--blue)" };

const KIND_LABEL = {
  planet_candidate: "planet candidate", known_planet: "known planet", eclipsing_binary: "eclipsing binary", known_eb: "known EB",
  variable: "variable star", known_variable: "known variable", high_velocity: "high velocity", nearby: "nearby star",
  hidden_companion: "hidden companion", ultracool: "ultracool dwarf", white_dwarf: "white dwarf",
  quasar: "quasar candidate", galaxy: "galaxy candidate", obscured_agn: "obscured AGN",
  systematic: "systematic", known_quasar: "known quasar", known_galaxy: "known galaxy", known_obscured_agn: "known AGN",
};

// ── sky ──────────────────────────────────────────────────────────────
const sky = new Sky($("#aladin"), $("#sky-fx"), $("#sky-pos"), $("#sky-surveys"), (cid) => select(cid));
sky.ready.catch((err) => { console.error("sky init failed", err); $("#sky-fallback").hidden = false; });

// ── log console ──────────────────────────────────────────────────────
const logEl = $("#log");
function logLine({ ts = Date.now() / 1000, src = "SYS", msg = "", level = "info", cls = "" }) {
  const atBottom = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 30;
  const d = document.createElement("div");
  d.className = `ll ${level} ${cls}`;
  const t = new Date(ts * 1000).toISOString().slice(11, 19);
  d.innerHTML = `<span class="t">${t}</span><span class="s">${esc(src)}</span><span class="m">${esc(msg)}</span>`;
  logEl.appendChild(d);
  while (logEl.childElementCount > 500) logEl.firstChild.remove();
  if (atBottom) logEl.scrollTop = logEl.scrollHeight;
  if (cls === "hit") typeIn(d.querySelector(".m"));
}
function typeIn(el) {
  const full = el.textContent;
  el.textContent = "";
  el.classList.add("cursor");
  let i = 0;
  const step = () => {
    i = Math.min(full.length, i + 3);
    el.textContent = full.slice(0, i);
    if (i < full.length) requestAnimationFrame(step); else setTimeout(() => el.classList.remove("cursor"), 600);
  };
  step();
}

// ── header: links, clock, counters ───────────────────────────────────
async function refreshHealth() {
  try {
    const h = await api("/api/health");
    $("#links").innerHTML = Object.entries(h.links).map(([k, v]) =>
      `<span class="lk ${v.ok ? "ok" : "bad"}" title="${k}: ${v.ok ? v.ms + " ms" : "unreachable"}"><i></i>${k}<b>${v.ok ? v.ms : "—"}</b></span>`).join("");
    return h;
  } catch {
    $("#links").innerHTML = `<span class="lk bad"><i></i>API offline</span>`;
    return null;
  }
}
setInterval(refreshHealth, 60000);
setInterval(() => { $("#clock").textContent = new Date().toISOString().slice(11, 19) + " UTC"; }, 1000);

const shown = {};
function tween(el, to) {
  const from = shown[el.id] ?? 0;
  shown[el.id] = to;
  if (from === to) { el.textContent = to.toLocaleString(); return; }
  animate(el, 600, (k) => { el.textContent = Math.round(from + (to - from) * ease(k)).toLocaleString(); });
}
let statsTimer = null;
function refreshStats(delay = 0) {
  clearTimeout(statsTimer);
  statsTimer = setTimeout(async () => {
    try {
      S.stats = await api("/api/stats");
      const c = S.stats.candidates || {};
      tween($("#st-examined"), S.stats.examined_total || 0);
      tween($("#st-new"), (c.new || 0) + (c.flagged || 0));
      tween($("#st-confirmed"), c.confirmed || 0);
      if (S.view === "log") renderLog();
    } catch { /* offline */ }
  }, delay);
}

// ── boot sequence (real link latencies) ──────────────────────────────
async function boot() {
  const el = $("#boot"), pre = $("#boot-text");
  let seen = false;
  try { seen = sessionStorage.getItem("fx.boot") === "1"; sessionStorage.setItem("fx.boot", "1"); } catch { /* ignore */ }
  const healthP = refreshHealth();
  if (seen) { el.remove(); await healthP; return; }
  let skip = false;
  el.addEventListener("click", () => { skip = true; });
  const out = [];
  const put = async (html, ms = 60) => { out.push(html); pre.innerHTML = out.join("\n"); if (!skip) await new Promise((r) => setTimeout(r, ms)); };
  await put(`<span class="b-hi">FINDERX 2.0 · DISCOVERY TERMINAL</span>`, 120);
  await put(`<span>TESS · Gaia DR3 · AllWISE · SIMBAD · NED · MPC · JPL</span>`, 160);
  await put("");
  await put("establishing archive links");
  const h = await healthP;
  for (const [k, v] of Object.entries(h?.links || {})) {
    await put(`  ${k.padEnd(10, " ")}${".".repeat(16)} ${v.ok ? `<span class="b-ok">LINK ${String(v.ms).padStart(4)} ms</span>` : `<span class="b-bad">NO LINK</span>`}`, 70);
  }
  await put("");
  await put(`operator console <span class="b-ok">ready</span>`, 380);
  el.classList.add("done");
  setTimeout(() => el.remove(), 450);
}

// ── live stream ──────────────────────────────────────────────────────
function connect() {
  const es = new EventSource("/api/stream");
  es.onmessage = (m) => { try { handle(JSON.parse(m.data)); } catch (e) { console.error(e); } };
  es.onerror = () => logLine({ src: "SYS", msg: "stream interrupted — reconnecting …", level: "warn" });
}

let queueTimer = null;
const refreshQueueSoon = () => { clearTimeout(queueTimer); queueTimer = setTimeout(loadQueue, 700); };

function handle(ev) {
  switch (ev.type) {
    case "hello":
      for (const r of (ev.recent || []).filter((e) => e.type === "log").slice(-60)) logLine(r);
      for (const j of ev.running || []) {
        S.jobs.set(j.id, { ...j, progress: null });
        if (j.params?.patrol) { setPatrol(j.id); sky.ready.then(() => sky.allsky()).catch(() => {}); }
      }
      renderJobs();
      logLine({ src: "SYS", msg: "live stream connected", cls: "sys" });
      break;
    case "log":
      logLine(ev);
      break;
    case "pipeline":
      setStages(ev.stages);
      break;
    case "stage":
      markStage(ev.stage, ev.state);
      break;
    case "progress": {
      const j = S.jobs.get(ev.job);
      if (j) { j.progress = ev; renderJobs(); }
      break;
    }
    case "counter":
      if (ev.key === "examined") refreshStats(1500);
      break;
    case "target":
      if (ev.radius) { sky.sweep(ev.job, ev.ra, ev.dec, ev.radius); sky.goto(ev.ra, ev.dec, Math.max(0.3, ev.radius * 3)); }
      else { sky.ping(ev.ra, ev.dec); sky.add("exam", ev.ra, ev.dec, { name: ev.label }); }
      break;
    case "candidate":
      onCandidate(ev);
      break;
    case "result":
      onResult(ev.item);
      break;
    case "job":
      onJob(ev);
      break;
    case "vote":
      refreshQueueSoon();
      break;
  }
}

function onCandidate(ev) {
  const known = ev.kind.startsWith("known_");
  logLine({ ts: ev.ts, src: "SIGNAL", msg: `${ev.new ? "ACQUIRED" : "UPDATED"} ${ev.id} · ${KIND_LABEL[ev.kind] || ev.kind} · ${ev.title}`, cls: known ? "" : "hit" });
  if (ev.ra != null) sky.ping(ev.ra, ev.dec, known ? "#7d8796" : "#72ca9b");
  if (ev.new && !known) toast(ev);
  refreshQueueSoon();
  refreshStats(800);
}

function toast(ev) {
  const t = document.createElement("div");
  t.className = `toast k-${ev.kind}`;
  t.innerHTML = `<div class="toast-k">SIGNAL ACQUIRED · ${esc((KIND_LABEL[ev.kind] || ev.kind).toUpperCase())}</div><div class="toast-t">${esc(ev.id)} · ${esc(ev.title)}</div><div class="toast-s">${esc(ev.subtitle || "")}</div>`;
  t.onclick = () => select(ev.id);
  $("#toasts").prepend(t);
  while ($("#toasts").childElementCount > 4) $("#toasts").lastChild.remove();
  setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 320); }, 5200);
}

function onResult(it) {
  if (it.kind === "neocp") { S.solar.neocp.push(it); sky.add("neocp", it.ra, it.dec, { name: it.name, popupTitle: it.name, popupDesc: `NEOCP · score ${it.score} · V ${it.vmag}` }); }
  else if (it.kind === "neocp_gone") S.solar.gone.push(it);
  else if (it.kind === "approach") S.solar.approaches.push(it);
  else if (it.kind === "sso") {
    S.solar.sso.push(it);
    sky.add("sso", it.ra, it.dec, { name: it.name, popupTitle: it.name, popupDesc: `${it.class} · V ${it.vmag}` });
    sky.motion(it.ra, it.dec, it.dra_arcsec_h, it.ddec_arcsec_h);
  } else return;
  clearTimeout(onResult.t);
  onResult.t = setTimeout(() => { renderSolar(); $('[data-view="solar"]').classList.toggle("ping", S.view !== "solar"); }, 300);
}

function onJob(ev) {
  if (ev.state === "running") {
    S.jobs.set(ev.job, { id: ev.job, engine: ev.engine, params: ev.params || {}, progress: null });
    if (ev.engine === "solar") {
      const mode = ev.params?.mode || (ev.params?.ra != null ? "field" : "watch");
      if (mode === "field") { S.solar.sso = []; sky.clear("sso"); }
      if (mode === "neocp" || mode === "watch") { S.solar.neocp = []; S.solar.gone = []; sky.clear("neocp"); }
      if (mode === "approaches" || mode === "watch") S.solar.approaches = [];
    }
  } else {
    S.jobs.delete(ev.job);
    sky.endSweep(ev.job);
    if (S.patrol === ev.job) setPatrol(null);
    const s = ev.summary || {};
    logLine({ ts: ev.ts, src: "JOB", msg: `${ev.engine} ${ev.state}${ev.error ? " — " + ev.error : ""} · examined ${s.examined || 0} · candidates ${s.candidates || 0} · ${s.elapsed_s || 0}s`, level: ev.state === "complete" ? "info" : "warn", cls: "sys" });
    refreshStats(300);
    refreshQueueSoon();
  }
  renderJobs();
}

// ── pipeline chips + running jobs ────────────────────────────────────
function setStages(stages) {
  S.stages = stages;
  $("#stages").innerHTML = stages.map((s) => `<span class="sg" data-s="${s}">${s}</span>`).join("");
}
function markStage(name, state) {
  const chips = $$("#stages .sg");
  const i = chips.findIndex((c) => c.dataset.s === name);
  if (i < 0) return;
  if (state === "run") chips.forEach((c, k) => { c.className = "sg" + (k < i ? " ok" : k === i ? " run" : ""); });
  else chips[i].className = `sg ${state}`;
}
function renderJobs() {
  $("#jobs").innerHTML = [...S.jobs.values()].map((j) => {
    const p = j.progress;
    const pct = p && p.total ? Math.round((100 * p.done) / p.total) : null;
    const label = j.params?.patrol ? "PATROL" : j.params?.tic ? `TIC ${j.params.tic}` : j.engine.toUpperCase();
    return `<div class="job"><span>${esc(label)}</span><span class="pb ${pct == null ? "ind" : ""}"><i style="width:${pct ?? 0}%"></i></span><span>${p && p.total ? `${p.done}/${p.total}` : ""}</span><button data-cancel="${j.id}" title="stop">✕</button></div>`;
  }).join("");
}
$("#jobs").addEventListener("click", (e) => { const b = e.target.closest("[data-cancel]"); if (b) post(`/api/jobs/${b.dataset.cancel}/cancel`, {}); });

// ── operations ───────────────────────────────────────────────────────
const FORMS = {
  transit: () => `
    <div class="seg" data-modes="transit">${["star", "sector", "cone"].map((m) => `<button type="button" data-mode="${m}" class="${S.mode.transit === m ? "on" : ""}">${m.toUpperCase()}</button>`).join("")}</div>
    ${S.mode.transit === "star" ? `
      <div class="row"><div class="fld"><label>TIC ID</label><input name="tic" placeholder="261136679" inputmode="numeric"></div>
      <div class="fld"><label>SECTORS</label><select name="sectors"><option>1</option><option>2</option><option selected>3</option><option>4</option></select></div></div>
      <div class="hint">Stitch the newest sectors for one star, then search for transits and periodic variability.</div>`
    : S.mode.transit === "sector" ? `
      <div class="row"><div class="fld"><label>SECTOR</label><input name="sector" placeholder="newest" inputmode="numeric"></div>
      <div class="fld"><label>STARS</label><input name="n" value="30" inputmode="numeric"></div></div>
      <div class="hint">Random stars from one sector's full-frame images. Known planet/TOI/EB hosts are skipped.</div>`
    : `
      <div class="fld"><label>TARGET · name or RA DEC</label><input name="target" placeholder="NGC 2516"></div>
      <div class="row"><div class="fld"><label>RADIUS °</label><input name="radius" value="0.2"></div><div class="fld"><label>MAX STARS</label><input name="n" value="30"></div></div>`}
    <button class="btn btn-run" type="submit">RUN EXO SCAN</button>`,
  stellar: () => `
    <div class="fld"><label>TARGET · name or RA DEC</label><input name="target" placeholder="random high-latitude field"></div>
    <div class="row"><div class="fld"><label>RADIUS °</label><input name="radius" value="1.0"></div><div class="fld"><label>&nbsp;</label><button class="btn" type="button" data-random>RANDOM FIELD</button></div></div>
    <div class="hint">Gaia DR3: high-velocity, nearby, hidden-companion, ultracool and white-dwarf stars that SIMBAD barely knows.</div>
    <button class="btn btn-run" type="submit">RUN STELLAR CENSUS</button>`,
  galaxy: () => `
    <div class="fld"><label>TARGET · name or RA DEC</label><input name="target" placeholder="random high-latitude field"></div>
    <div class="row"><div class="fld"><label>RADIUS °</label><input name="radius" value="0.5"></div><div class="fld"><label>&nbsp;</label><button class="btn" type="button" data-random>RANDOM FIELD</button></div></div>
    <div class="hint">Gaia × WISE quasar and galaxy candidates with no classification in Milliquas, SIMBAD or NED. Southern fields are richest.</div>
    <button class="btn btn-run" type="submit">RUN DEEP SCAN</button>`,
  solar: () => `
    <div class="seg" data-modes="solar">${["watch", "neocp", "approaches", "field"].map((m) => `<button type="button" data-mode="${m}" class="${S.mode.solar === m ? "on" : ""}">${m === "approaches" ? "CAD" : m.toUpperCase()}</button>`).join("")}</div>
    ${S.mode.solar === "field" ? `
      <div class="fld"><label>TARGET · name or RA DEC</label><input name="target" placeholder="0 0"></div>
      <div class="fld"><label>RADIUS °</label><input name="radius" value="1.0"></div>
      <div class="hint">SkyBoT: every known asteroid and comet in the field right now, with one-day motion tracks.</div>`
    : `<div class="hint">${S.mode.solar === "approaches" ? "JPL: asteroids passing within 0.05 au of Earth in the next 60 days." : "MPC NEO Confirmation Page: objects found in the last days that still need follow-up, diffed against your last check."}</div>`}
    <button class="btn btn-run" type="submit">RUN SOLAR</button>`,
};

function renderForm() {
  $$(".et").forEach((b) => b.classList.toggle("active", b.dataset.engine === S.engine));
  $("#eform").innerHTML = FORMS[S.engine]();
}
$(".engine-tabs").addEventListener("click", (e) => {
  const b = e.target.closest(".et");
  if (!b) return;
  S.engine = b.dataset.engine;
  renderForm();
});
$("#eform").addEventListener("click", (e) => {
  const m = e.target.closest("[data-mode]");
  if (m) { S.mode[m.parentElement.dataset.modes] = m.dataset.mode; renderForm(); return; }
  if (e.target.closest("[data-random]")) {
    const [ra, dec] = randomField();
    $("#eform [name=target]").value = `${ra.toFixed(3)} ${dec.toFixed(3)}`;
  }
});
$("#eform").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = Object.fromEntries(new FormData(e.target).entries());
  try {
    if (S.engine === "transit") {
      if (S.mode.transit === "star") {
        if (!f.tic) throw new Error("enter a TIC ID");
        await run("transit", { tic: +f.tic.replace(/\D/g, ""), sectors: +f.sectors });
      } else if (S.mode.transit === "sector") {
        await run("transit", { ...(f.sector ? { sector: +f.sector } : {}), n: +f.n || 30 });
      } else {
        const t = await target(f.target);
        await run("transit", { ra: t.ra, dec: t.dec, radius: +f.radius || 0.2, n: +f.n || 30 });
      }
    } else if (S.engine === "solar") {
      if (S.mode.solar === "field") {
        const t = await target(f.target || "0 0");
        await run("solar", { mode: "field", ra: t.ra, dec: t.dec, radius: +f.radius || 1 });
      } else await run("solar", { mode: S.mode.solar });
      setView("solar");
    } else {
      const t = f.target ? await target(f.target) : (() => { const [ra, dec] = randomField(); return { ra, dec }; })();
      await run(S.engine, { ra: t.ra, dec: t.dec, radius: +f.radius || (S.engine === "stellar" ? 1 : 0.5) });
    }
  } catch (err) {
    logLine({ src: "OPS", msg: err.message, level: "error" });
  }
});

async function run(engine, params) {
  const r = await post("/api/scan", { engine, params });
  logLine({ src: "OPS", msg: `dispatched ${engine.toUpperCase()} ${JSON.stringify(r.params)} → job ${r.job}`, cls: "cmd" });
  return r.job;
}

async function target(s) {
  s = (s || "").trim();
  if (!s) throw new Error("enter a target name or coordinates");
  const r = await api(`/api/resolve?q=${encodeURIComponent(s)}`);
  if (r.ra == null) throw new Error(`'${s}' is not a sky position`);
  if (r.name && r.name !== s) logLine({ src: "SESAME", msg: `${s} → ${r.name} (${num(r.ra, 4)}, ${num(r.dec, 4)})${r.otype ? " · " + r.otype : ""}` });
  return r;
}

function randomField(minB = 30) {
  // uniform on the sphere, rejected near the Galactic plane
  for (;;) {
    const ra = Math.random() * 360;
    const dec = (Math.asin(2 * Math.random() - 1) * 180) / Math.PI;
    const r = Math.PI / 180;
    const sinb = Math.sin(dec * r) * Math.sin(27.12825 * r) + Math.cos(dec * r) * Math.cos(27.12825 * r) * Math.cos((ra - 192.85948) * r);
    if (Math.abs(Math.asin(sinb) / r) >= minB) return [ra, dec];
  }
}

// ── patrol ───────────────────────────────────────────────────────────
function setPatrol(jid) {
  S.patrol = jid;
  $("#patrol").classList.toggle("on", !!jid);
  $("#patrol-label").textContent = jid ? "STOP PATROL" : "START PATROL";
  $("#patrol-sub").textContent = jid
    ? "Patrolling recent TESS sectors. Each ring on the sky is a star being searched right now."
    : "Blind survey of fresh TESS stars. Runs until you stop it; you vet what it flags.";
}
$("#patrol-btn").addEventListener("click", async () => {
  if (S.patrol) { await post(`/api/jobs/${S.patrol}/cancel`, {}); logLine({ src: "PATROL", msg: "stand down requested", cls: "cmd" }); return; }
  try {
    const jid = await run("patrol", { n: 40 });
    setPatrol(jid);
    setView("sky");
    sky.allsky();
  } catch (err) { logLine({ src: "PATROL", msg: err.message, level: "error" }); }
});

// ── queue ────────────────────────────────────────────────────────────
async function loadQueue() {
  const q = new URLSearchParams({ limit: "500", include_known: S.filter.known ? "true" : "false", order: S.filter.order });
  if (S.filter.status) q.set("status", S.filter.status);
  if (S.filter.engine) q.set("engine", S.filter.engine);
  try {
    const prev = new Set(S.cands.map((c) => c.id));
    S.cands = (await api(`/api/candidates?${q}`)).candidates;
    renderQueue(prev);
    sky.ready.then(() => sky.setCandidates(S.cands)).catch(() => {});
  } catch (err) { logLine({ src: "API", msg: err.message, level: "error" }); }
}
function renderQueue(prev = new Set()) {
  $("#queue-count").textContent = S.cands.length;
  const el = $("#qlist");
  if (!S.cands.length) {
    el.innerHTML = `<div class="empty">${S.filter.status === "confirmed" ? "Nothing confirmed yet. Vet the queue and press C on the real ones." : "Queue is clear.<br>Start PATROL or run an operation."}</div>`;
    return;
  }
  el.innerHTML = S.cands.map((c) => `
    <div class="qi k-${c.kind} ${c.id === S.sel ? "sel" : ""} ${prev.size && !prev.has(c.id) ? "fresh" : ""}" data-id="${c.id}">
      <div class="qi-bar"></div>
      <div class="qi-t">${statusDot(c.status)}${esc(c.title)}</div>
      <div class="qi-r"><span class="qi-id">${c.id}</span><span class="score"><i style="width:${Math.round(c.score * 100)}%"></i></span></div>
      <div class="qi-s">${c.engine === "transit" || c.engine === "variable" ? esc(KIND_LABEL[c.kind] || c.kind) + " · " : ""}${esc(c.subtitle)}</div>
    </div>`).join("");
}
const statusDot = (s) => (s === "new" ? "" : `<span class="st-dot" style="background:${{ confirmed: "var(--green)", rejected: "var(--red)", flagged: "var(--gold)" }[s]}"></span>`);
$("#qlist").addEventListener("click", (e) => { const it = e.target.closest(".qi"); if (it) select(it.dataset.id); });
$("#status-chips").addEventListener("click", (e) => {
  const b = e.target.closest(".chip"); if (!b) return;
  $$("#status-chips .chip").forEach((x) => x.classList.toggle("active", x === b));
  S.filter.status = b.dataset.status; loadQueue();
});
$("#engine-chips").addEventListener("click", (e) => {
  const b = e.target.closest(".chip"); if (!b) return;
  $$("#engine-chips .chip").forEach((x) => x.classList.toggle("active", x === b));
  S.filter.engine = b.dataset.engine; loadQueue();
});
$("#show-known").checked = S.filter.known;
$("#q-order").value = S.filter.order;
$("#q-order").addEventListener("change", (e) => { S.filter.order = e.target.value; store.set("order", S.filter.order); loadQueue(); });
$("#show-known").addEventListener("change", (e) => { S.filter.known = e.target.checked; store.set("known", S.filter.known); loadQueue(); });

function move(d) {
  if (!S.cands.length) return;
  const i = S.cands.findIndex((c) => c.id === S.sel);
  const n = S.cands[Math.max(0, Math.min(S.cands.length - 1, i < 0 ? 0 : i + d))];
  if (n) select(n.id);
}

// ── selection + dossier ──────────────────────────────────────────────
async function select(cid) {
  S.sel = cid;
  $$(".qi").forEach((x) => x.classList.toggle("sel", x.dataset.id === cid));
  $(`.qi[data-id="${cid}"]`)?.scrollIntoView({ block: "nearest" });
  let c;
  try { c = await api(`/api/candidates/${cid}`); } catch (err) { logLine({ src: "API", msg: err.message, level: "error" }); return; }
  if (S.sel !== cid) return;
  S.detail = c;
  renderDossier(c);
  if (c.ra != null) {
    sky.lockOn(c.ra, c.dec);
    sky.goto(c.ra, c.dec, c.engine === "transit" || c.engine === "variable" ? 0.25 : 0.08);
  }
  if (c.engine === "transit" || c.engine === "variable") {
    if (S.view !== "sky") setView("signal"); else $('[data-view="signal"]').classList.add("ping");
    renderSignal(c);
  } else {
    if (S.view === "signal") setView("field");
    renderField(c);
  }
}

function kv(rows) {
  return `<dl class="kv">${rows.filter((r) => r && r[1] != null && r[1] !== "—").map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>`;
}

function metricsFor(c) {
  const m = c.metrics || {};
  const st = m.star || {};
  if (c.engine === "transit") return kv([
    ["period", `${num(m.period, 5)} d`],
    ["orbital period", m.orbital_period && Math.abs(m.orbital_period - m.period) > 1e-6 ? `${num(m.orbital_period, 5)} d (alternating depths)` : null],
    ["epoch (BTJD)", num(m.t0, 4)], ["depth", `${num(m.depth_ppm, 0)} ppm`],
    ["duration", `${num(m.duration_h, 2)} h`], ["planet radius", m.rp_earth ? `${num(m.rp_earth, 2)} R⊕` : null],
    ["SNR · SDE", `${num(m.snr, 1)} · ${num(m.sde, 1)}`], ["transits", m.n_transits],
    ["host Tmag", num(st.tmag, 2)], ["host Teff", st.teff ? `${num(st.teff, 0)} K` : null], ["host radius", st.radius ? `${num(st.radius, 2)} R☉` : null],
    ["sectors", (st.sectors || []).join(", ")], ["product", st.provenance],
  ]);
  if (c.engine === "variable") return kv([
    ["period", `${num(m.true_period, 5)} d`], ["type", esc(m.type_label)], ["amplitude", `${num(m.ptp_ppm / 1e3, 2)} ppt p-p`],
    ["LS power", num(m.power, 2)], ["host Tmag", num(st.tmag, 2)], ["host Teff", st.teff ? `${num(st.teff, 0)} K` : null],
    ["sectors", (st.sectors || []).join(", ")],
  ]);
  if (c.engine === "stellar") return kv([
    ["distance", `${num(m.dist_pc, 1)} pc`], ["G", num(m.G, 2)], ["BP−RP", num(m.bp_rp, 2)], ["M_G", num(m.M_G, 2)],
    ["proper motion", `${num(m.pm, 1)} mas/yr`], ["v_tan", `${num(m.v_tan, 0)} km/s`],
    ["v (Galactocentric)", m.v_galactocentric ? `${m.v_galactocentric} km/s` : null], ["RUWE", num(m.ruwe, 2)],
    ["radial velocity", m.rv != null ? `${num(m.rv, 1)} km/s` : null], ["SIMBAD", m.simbad_type ? `${esc(m.simbad_type)} · ${m.simbad_refs} refs` : "none"],
  ]);
  return kv([
    ["P(quasar)", m.p_qso != null ? num(m.p_qso, 3) : null], ["P(galaxy)", m.p_gal != null ? num(m.p_gal, 3) : null],
    ["redshift (Gaia)", m.z_qsoc ?? m.z_gal ? num(m.z_qsoc ?? m.z_gal, 3) + (m.z_qsoc != null && !m.z_qsoc_reliable ? " (flagged, may be aliased)" : "") : null], ["G", num(m.G, 2)],
    ["W1−W2", num(m["W1-W2"], 2)], ["W1 · W2", m.W1 != null ? `${num(m.W1, 2)} · ${num(m.W2, 2)}` : null],
    ["parallax", m.parallax != null ? `${num(m.parallax, 2)} ± ${num(m.parallax_err, 2)} mas` : null],
  ]);
}

function cutoutUrl(survey, ra, dec, fov) {
  if (survey === "LS10") return `https://www.legacysurvey.org/viewer/cutout.jpg?ra=${ra}&dec=${dec}&layer=ls-dr10&pixscale=${((fov * 3600) / 300).toFixed(3)}&size=300`;
  const hips = { DSS: "CDS/P/DSS2/color", PS1: "CDS/P/PanSTARRS/DR1/color-z-zg-g", OLD: "CDS/P/DSS2/red", NEW: "CDS/P/PanSTARRS/DR1/i", "2MASS": "CDS/P/2MASS/color" }[survey];
  return `https://alasky.cds.unistra.fr/hips-image-services/hips2fits?hips=${encodeURIComponent(hips)}&width=300&height=300&fov=${fov}&projection=TAN&coordsys=icrs&ra=${ra}&dec=${dec}&format=jpg`;
}

function extLinks(c) {
  const ra = c.ra, dec = c.dec;
  const pos = `${ra} ${dec}`;
  const L = [];
  const tic = c.metrics?.star?.tic;
  if (tic) L.push(["ExoFOP", `https://exofop.ipac.caltech.edu/tess/target.php?id=${tic}`], ["MAST", `https://mast.stsci.edu/portal/Mashup/Clients/Mast/Portal.html?searchQuery=TIC%20${tic}`]);
  if (c.engine === "transit" || c.engine === "variable") L.push(["VSX", `https://www.aavso.org/vsx/index.php?view=results.get&coords=${encodeURIComponent(`${ra} ${dec}`)}&format=d&size=60&unit=3`]);
  L.push(["SIMBAD", `https://simbad.cds.unistra.fr/simbad/sim-coo?Coord=${encodeURIComponent(pos)}&Radius=10&Radius.unit=arcsec`]);
  if (c.engine === "galaxy") L.push(["NED", `https://ned.ipac.caltech.edu/conesearch?search_type=Near%20Position%20Search&coordinates=${encodeURIComponent(`${ra}d ${dec}d`)}&radius=0.1&in_csys=Equatorial&in_equinox=J2000`]);
  if (c.metrics?.source_id) L.push(["Gaia", `https://vizier.cds.unistra.fr/viz-bin/VizieR-5?-source=I/355/gaiadr3&Source=${c.metrics.source_id}`]);
  L.push(["Legacy", `https://www.legacysurvey.org/viewer?ra=${ra}&dec=${dec}&layer=ls-dr10&zoom=15&mark=${ra},${dec}`]);
  L.push(["ESASky", `https://sky.esa.int/esasky/?target=${encodeURIComponent(pos)}&fov=0.1`]);
  return L.map(([n, u]) => `<a href="${u}" target="_blank" rel="noopener">${n} ↗</a>`).join("");
}

let blinkTimer = null;
function renderDossier(c) {
  clearInterval(blinkTimer);
  $("#d-id").textContent = c.id;
  const flags = (c.flags || []).map((f) => {
    const [sev, txt] = FLAGS[f] || ["info", ""];
    return `<div class="flag" style="--fc:${FC[sev]}"><i></i><div>${f.replace(/_/g, " ").toLowerCase()}${txt ? `<small>${esc(txt)}</small>` : ""}</div></div>`;
  }).join("");
  const known = (c.known || []).map((k) => `<div>${esc(k.kind)} · ${esc(k.label)} ${k.type ? "· " + esc(k.type) : ""} ${k.period ? "· P " + num(k.period, 4) : ""} ${k.match ? `<span class="m">[${esc(k.match)}]</span>` : ""}</div>`).join("");
  const fov = c.engine === "transit" || c.engine === "variable" ? 0.06 : c.engine === "stellar" ? 0.03 : 0.015;
  const tabs = c.engine === "stellar" ? ["BLINK", "DSS", "PS1", "2MASS"] : c.engine === "galaxy" ? ["LS10", "PS1", "DSS"] : ["DSS", "PS1", "2MASS"];
  const r = c.route || ["", ""];
  $("#d-body").innerHTML = `
    <div class="d-head k-${c.kind}">
      <div class="d-title">${esc(c.title)}</div>
      <div class="d-sub">${esc(c.subtitle)}</div>
      <div class="d-tags"><span class="tag">${esc(KIND_LABEL[c.kind] || c.kind)}</span><span class="tag" style="--k:${{ confirmed: "var(--green)", rejected: "var(--red)", flagged: "var(--gold)" }[c.status] || "var(--tx2)"}">${c.status}</span></div>
      <div class="d-score">SCORE <span class="score"><i style="width:0%"></i></span><b>${num(c.score, 2)}</b></div>
    </div>
    <div class="vote">
      <button class="btn v-confirm ${c.status === "confirmed" ? "on" : ""}" data-vote="confirmed">CONFIRM <kbd>C</kbd></button>
      <button class="btn v-flag ${c.status === "flagged" ? "on" : ""}" data-vote="flagged">FLAG <kbd>F</kbd></button>
      <button class="btn v-reject ${c.status === "rejected" ? "on" : ""}" data-vote="rejected">REJECT <kbd>R</kbd></button>
    </div>
    <div class="note"><textarea id="d-note" placeholder="your notes (saved with your vote)">${esc(c.note || "")}</textarea></div>
    ${c.analyst ? `<div class="analyst"><b>ANALYST NOTE</b>${esc(c.analyst)}</div>` : ""}
    <div class="d-sec"><div class="d-sh">MEASUREMENTS</div>${metricsFor(c)}</div>
    ${flags ? `<div class="d-sec"><div class="d-sh">FLAGS</div><div class="flags">${flags}</div></div>` : ""}
    <div class="d-sec"><div class="d-sh">CATALOGUE MATCHES</div><div class="known">${known || '<div class="dim">none — nobody has catalogued this signal</div>'}</div></div>
    ${c.ra != null ? `<div class="d-sec"><div class="d-sh">IMAGING · ${(fov * 60).toFixed(1)}′ field</div><div class="cut">
      <div class="seg cut-tabs">${tabs.map((t, i) => `<button type="button" data-cut="${t}" class="${i ? "" : "on"}">${t}</button>`).join("")}</div>
      <div class="cut-img"><img id="cut-img" alt="sky cutout" loading="lazy"><div class="ret"></div><div class="cut-cap" id="cut-cap"></div></div></div></div>` : ""}
    <div class="d-sec"><div class="d-sh">EXTERNAL</div><div class="ext">${c.ra != null ? extLinks(c) : ""}</div></div>
    <div class="d-sec"><div class="d-sh">WHERE THIS GOES</div><div class="hint" style="padding:2px 14px 10px">${esc(r[0])}</div></div>
    <div class="d-actions" style="padding-top:12px"><button class="btn" id="d-report">REPORT</button>${c.engine === "transit" || c.engine === "variable" ? `<button class="btn" id="d-replay">REPLAY FOLD</button>` : ""}</div>`;
  requestAnimationFrame(() => { const i = $(".d-score .score i"); if (i) i.style.width = `${Math.round(c.score * 100)}%`; });
  if (c.ra != null) showCut(tabs[0], c, fov);
  $$(".cut-tabs button").forEach((b) => b.addEventListener("click", () => {
    $$(".cut-tabs button").forEach((x) => x.classList.toggle("on", x === b));
    showCut(b.dataset.cut, c, fov);
  }));
  $$("[data-vote]").forEach((b) => b.addEventListener("click", () => vote(b.dataset.vote)));
  $("#d-report").addEventListener("click", () => showReport(c.id));
  $("#d-replay")?.addEventListener("click", () => { setView("signal"); S.sig?.replay(); });
}

function showCut(which, c, fov) {
  clearInterval(blinkTimer);
  const img = $("#cut-img"), cap = $("#cut-cap");
  if (!img) return;
  if (which === "BLINK") {
    // the blink comparator: ~1990s photographic plate vs 2010s Pan-STARRS
    const old = new Image(), neu = new Image();
    old.src = cutoutUrl("OLD", c.ra, c.dec, fov);
    neu.src = cutoutUrl("NEW", c.ra, c.dec, fov);
    let on = false;
    img.src = old.src;
    cap.textContent = "DSS2 red · ~1990";
    blinkTimer = setInterval(() => {
      on = !on;
      img.src = on ? neu.src : old.src;
      cap.textContent = on ? "Pan-STARRS i · ~2012" : "DSS2 red · ~1990";
    }, 700);
    return;
  }
  img.src = cutoutUrl(which, c.ra, c.dec, fov);
  cap.textContent = { DSS: "DSS2 colour", PS1: "Pan-STARRS DR1", "2MASS": "2MASS JHK", LS10: "Legacy Surveys DR10" }[which];
}

async function vote(status) {
  if (!S.detail) return;
  const id = S.detail.id;
  const note = $("#d-note")?.value || null;
  try {
    await post(`/api/candidates/${id}/vote`, { status, note });
    logLine({ src: "VET", msg: `${id} → ${status.toUpperCase()}${note ? " · “" + note + "”" : ""}`, cls: status === "confirmed" ? "hit" : "cmd" });
    const i = S.cands.findIndex((c) => c.id === id);
    const next = S.cands[i + 1] || S.cands[i - 1];
    if (S.filter.status && !S.filter.status.split(",").includes(status)) {
      S.cands.splice(i, 1);
      renderQueue();
      if (next) select(next.id);
    } else {
      S.cands[i].status = status;
      renderQueue();
      select(id);
    }
    refreshStats(200);
  } catch (err) { logLine({ src: "VET", msg: err.message, level: "error" }); }
}

async function showReport(id) {
  try {
    const txt = await api(`/api/candidates/${id}/report`);
    $("#modal-title").textContent = `REPORT · ${id}`;
    $("#modal-body").textContent = txt;
    $("#modal").hidden = false;
  } catch (err) { logLine({ src: "API", msg: err.message, level: "error" }); }
}
$("#modal-x").addEventListener("click", () => { $("#modal").hidden = true; });
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") $("#modal").hidden = true; });
$("#modal-copy").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText($("#modal-body").textContent); $("#modal-copy").textContent = "COPIED"; setTimeout(() => { $("#modal-copy").textContent = "COPY"; }, 1200); } catch { /* clipboard blocked */ }
});

// ── signal + field views ─────────────────────────────────────────────
function renderSignal(c) {
  if (!c.plots) { $("#signal-empty").hidden = false; $("#sig").hidden = true; return; }
  $("#signal-empty").hidden = true;
  $("#sig").hidden = false;
  $("#tests").innerHTML = tests(c).map((t) => `
    <div class="tr"><span class="ic ${t.state}">${{ ok: "✓", warn: "!", bad: "✕", na: "·" }[t.state]}</span><span class="tn">${esc(t.name)}</span><span class="tv">${esc(t.value ?? "—")}</span><span class="td">${esc(t.desc)}</span></div>`).join("");
  const els = {
    canvas: { raw: $("#p-raw"), pgram: $("#p-pgram"), fold: $("#p-fold"), zoom: $("#p-zoom") },
    titles: { raw: $("#pt-raw-r"), pgram: $("#pt-pgram"), fold: $("#pt-fold"), zoom: $("#pt-zoom") },
  };
  S.sig = els;
  if (S.view !== "signal") { S.sig.pending = c; return; }
  requestAnimationFrame(() => drawSignal(c, els));
}
function renderField(c) {
  const has = (c.engine === "stellar" && c.plots?.hr) || (c.engine === "galaxy" && c.plots?.wise);
  $("#field-empty").hidden = !!has;
  $("#field-box").hidden = !has;
  if (!has) return;
  S.fieldPending = c;
  if (S.view === "field") requestAnimationFrame(() => drawField(c, $("#p-field"), $("#pt-field"), S.cands.filter((x) => x.job === c.job)));
}
new ResizeObserver(() => {
  if (S.view === "signal" && S.sig?.redraw) S.sig.redraw();
  if (S.view === "field" && S.fieldPending) drawField(S.fieldPending, $("#p-field"), $("#pt-field"), S.cands.filter((x) => x.job === S.fieldPending.job));
}).observe($(".stage"));

// ── views ────────────────────────────────────────────────────────────
function setView(v) {
  S.view = v;
  $$(".vt").forEach((b) => b.classList.toggle("active", b.dataset.view === v));
  $$(".view").forEach((x) => x.classList.toggle("active", x.id === "view-" + v));
  $(`[data-view="${v}"]`).classList.remove("ping");
  $("#view-tools").innerHTML = v === "signal" && S.detail ? `<button class="btn" id="vt-replay">▶ REPLAY <kbd>SPACE</kbd></button>` : "";
  $("#vt-replay")?.addEventListener("click", () => S.sig?.replay());
  if (v === "signal" && S.sig?.pending) { const c = S.sig.pending; S.sig.pending = null; requestAnimationFrame(() => drawSignal(c, S.sig)); }
  else if (v === "signal" && S.sig?.redraw) { const sig = S.sig; requestAnimationFrame(() => sig.redraw?.()); }
  if (v === "field" && S.fieldPending) requestAnimationFrame(() => drawField(S.fieldPending, $("#p-field"), $("#pt-field"), S.cands.filter((x) => x.job === S.fieldPending.job)));
  if (v === "solar") renderSolar();
  if (v === "log") renderLog();
}
$("#views").addEventListener("click", (e) => { const b = e.target.closest(".vt"); if (b) setView(b.dataset.view); });

function renderSolar() {
  const s = S.solar;
  if (!s.neocp.length && !s.approaches.length && !s.sso.length) return;
  const rows = (arr, cols, fn) => `<table class="tbl"><tr>${cols.map((c) => `<th>${c}</th>`).join("")}</tr>${arr.map(fn).join("")}</table>`;
  let html = "";
  if (s.neocp.length) {
    html += `<h3>NEO CONFIRMATION PAGE · ${s.neocp.length} unconfirmed objects · newest discoveries needing follow-up${s.gone.length ? ` · ${s.gone.length} left since last check: ${s.gone.map((g) => esc(g.name)).join(", ")}` : ""}</h3>`;
    html += rows([...s.neocp].sort((a, b) => b.score - a.score), ["DESIG", "NEO SCORE", "V", "OBS", "ARC d", "NOT SEEN d", "RA", "DEC", "H", "NOTE"], (o) => `
      <tr data-ra="${o.ra}" data-dec="${o.dec}"><td class="${o.new_since_last_check ? "new" : ""}">${esc(o.name)}${o.new_since_last_check ? " ●" : ""}</td>
      <td><span class="bar" style="width:${o.score * 0.6}px"></span>${o.score}</td><td>${num(o.vmag, 1)}</td><td>${o.nobs}</td><td>${num(o.arc_days, 2)}</td>
      <td>${num(o.not_seen_days, 2)}</td><td>${num(o.ra, 3)}</td><td>${num(o.dec, 3)}</td><td>${num(o.H, 1)}</td><td>${esc(o.note)}</td></tr>`);
  }
  if (s.approaches.length) {
    html += `<h3>CLOSE APPROACHES · next 60 days · inside 0.05 au</h3>`;
    html += rows(s.approaches, ["OBJECT", "DATE (TDB)", "DISTANCE", "LUNAR DIST", "SPEED", "SIZE (est.)"], (a) => `
      <tr><td>${esc(a.name)}</td><td>${esc(a.date)}</td><td>${num(a.dist_au, 4)} au</td><td><span class="bar" style="width:${Math.min(80, a.dist_ld * 4)}px;background:${a.dist_ld < 1 ? "var(--red)" : "var(--orange)"}"></span>${num(a.dist_ld, 2)}</td>
      <td>${num(a.v_rel_kms, 1)} km/s</td><td>${a.diameter_m ? (a.diameter_m >= 1000 ? (a.diameter_m / 1000).toFixed(1) + " km" : a.diameter_m + " m") : "—"}</td></tr>`);
  }
  if (s.sso.length) {
    html += `<h3>SKYBOT FIELD CENSUS · ${s.sso.length} known bodies</h3>`;
    html += rows([...s.sso].sort((a, b) => (a.vmag ?? 99) - (b.vmag ?? 99)), ["NAME", "CLASS", "V", "MOTION ″/h", "Δ EARTH au", "RA", "DEC"], (o) => `
      <tr data-ra="${o.ra}" data-dec="${o.dec}"><td>${esc(o.name)}</td><td>${esc(o.class)}</td><td>${num(o.vmag, 1)}</td>
      <td>${num(Math.hypot(o.dra_arcsec_h || 0, o.ddec_arcsec_h || 0), 1)}</td><td>${num(o.dist_au, 2)}</td><td>${num(o.ra, 4)}</td><td>${num(o.dec, 4)}</td></tr>`);
  }
  $("#solar").innerHTML = html;
}
$("#solar").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-ra]");
  if (!tr) return;
  setView("sky");
  sky.lockOn(+tr.dataset.ra, +tr.dataset.dec);
  sky.goto(+tr.dataset.ra, +tr.dataset.dec, 0.5);
});

async function renderLog() {
  let conf = [];
  try { conf = (await api("/api/candidates?status=confirmed&limit=1000")).candidates; } catch { /* offline */ }
  const st = S.stats, c = st.candidates || {};
  $("#logv").innerHTML = `
    <div class="mission">
      <div class="ms"><div class="ms-v">${(st.examined_total || 0).toLocaleString()}</div><div class="ms-k">TARGETS EXAMINED</div></div>
      <div class="ms"><div class="ms-v">${Object.values(c).reduce((a, b) => a + b, 0).toLocaleString()}</div><div class="ms-k">SIGNALS LOGGED</div></div>
      <div class="ms"><div class="ms-v g">${(c.confirmed || 0).toLocaleString()}</div><div class="ms-k">CONFIRMED BY YOU</div></div>
      <div class="ms"><div class="ms-v">${(c.rejected || 0).toLocaleString()}</div><div class="ms-k">FALSE POSITIVES CAUGHT</div></div>
    </div>
    <div class="logv-h"><span>DISCOVERY LOG · confirmed candidates</span><span><a class="btn" href="/api/export?status=confirmed&format=csv">CSV</a> <a class="btn" href="/api/export?status=confirmed&format=json">JSON</a></span></div>
    <div class="logv-note">“Confirmed” means you vetted it and believe it is real. It becomes a discovery when it is reported and followed up: open a candidate and press REPORT for the submission text and where it goes (ExoFOP CTOI for planets, AAVSO VSX for variables, spectroscopic follow-up for quasars).</div>
    ${conf.length ? `<table class="tbl"><tr><th>ID</th><th>KIND</th><th>OBJECT</th><th>DETAIL</th><th>SCORE</th><th>NOTE</th></tr>${conf.map((x) => `
      <tr data-id="${x.id}"><td>${x.id}</td><td><span class="tag k-${x.kind}">${esc(KIND_LABEL[x.kind] || x.kind)}</span></td><td>${esc(x.title)}</td><td>${esc(x.subtitle)}</td><td>${num(x.score, 2)}</td><td>${esc(x.note || "")}</td></tr>`).join("")}</table>`
    : `<div class="empty">No confirmed candidates yet.</div>`}`;
}
$("#logv").addEventListener("click", (e) => { const tr = e.target.closest("tr[data-id]"); if (tr) select(tr.dataset.id); });

// ── console commands ─────────────────────────────────────────────────
const COMMANDS = [
  ["help", "list commands"],
  ["tic <id> [sectors=3]", "transit + variability search on one star"],
  ["sector [<n>] [n=30]", "random fresh stars from a TESS sector"],
  ["cone <target> [r=0.2]", "every TESS light curve in a sky cone"],
  ["patrol [stop] [n=40]", "continuous blind TESS survey"],
  ["stars <target> [r=1]", "Gaia DR3 stellar census of a field"],
  ["deep <target> [r=0.5]", "quasar / galaxy candidates in a field"],
  ["random stars|deep", "census of a random high-latitude field"],
  ["solar [watch|neocp|approaches]", "live Solar System feeds"],
  ["solar field <target> [r=1]", "known asteroids/comets in a field now"],
  ["goto <target> [fov=1]", "point the sky atlas"],
  ["survey dss|ps1|2mass|wise|gaia", "change sky imagery"],
  ["open <id>", "open a candidate"],
  ["confirm|flag|reject [note]", "vet the open candidate"],
  ["queue tovet|confirmed|rejected|all", "filter the queue"],
  ["view sky|signal|field|solar|log", "switch workspace view"],
  ["stop [all]", "stop running jobs"],
  ["export [csv|json]", "download confirmed discoveries"],
  ["clear", "clear the console"],
];

function parseArgs(rest) {
  const opts = {}, words = [];
  for (const w of rest) { const m = w.match(/^(\w+)=(.+)$/); if (m) opts[m[1]] = m[2]; else words.push(w); }
  return { opts, text: words.join(" ") };
}

async function exec(line) {
  const parts = line.trim().split(/\s+/);
  const cmd = (parts.shift() || "").toLowerCase();
  const { opts, text } = parseArgs(parts);
  logLine({ src: "YOU", msg: line, cls: "cmd" });
  switch (cmd) {
    case "help": case "?":
      for (const [c, d] of COMMANDS) logLine({ src: "HELP", msg: `${c.padEnd(36)} ${d}` });
      return;
    case "tic": return run("transit", { tic: +text.replace(/\D/g, ""), sectors: +(opts.sectors || 3) });
    case "sector": return run("transit", { ...(text ? { sector: +text } : {}), n: +(opts.n || 30) });
    case "cone": { const t = await target(text); return run("transit", { ra: t.ra, dec: t.dec, radius: +(opts.r || 0.2), n: +(opts.n || 30) }); }
    case "patrol":
      if (text === "stop") { if (S.patrol) await post(`/api/jobs/${S.patrol}/cancel`, {}); return; }
      if (S.patrol) return logLine({ src: "PATROL", msg: "already patrolling (patrol stop to end)" });
      setPatrol(await run("patrol", { n: +(opts.n || 40) }));
      setView("sky");
      return sky.allsky();
    case "stars": case "deep": {
      const t = await target(text);
      return run(cmd === "stars" ? "stellar" : "galaxy", { ra: t.ra, dec: t.dec, radius: +(opts.r || (cmd === "stars" ? 1 : 0.5)) });
    }
    case "random": {
      const [ra, dec] = randomField();
      const eng = text === "deep" ? "galaxy" : "stellar";
      return run(eng, { ra, dec, radius: eng === "stellar" ? 1 : 0.5 });
    }
    case "solar": {
      const [mode, ...tw] = text.split(" ");
      setView("solar");
      if (mode === "field") { const t = await target(tw.join(" ") || "0 0"); return run("solar", { mode: "field", ra: t.ra, dec: t.dec, radius: +(opts.r || 1) }); }
      return run("solar", { mode: mode || "watch" });
    }
    case "goto": { const t = await target(text); setView("sky"); sky.lockOn(t.ra, t.dec); return sky.goto(t.ra, t.dec, +(opts.fov || 1)); }
    case "survey": {
      const b = $$("#sky-surveys button").find((x) => x.textContent.toLowerCase() === text.toLowerCase());
      if (b) b.click(); else logLine({ src: "SKY", msg: "surveys: dss ps1 2mass wise gaia", level: "warn" });
      return;
    }
    case "open": return select(text.toUpperCase());
    case "confirm": case "flag": case "reject":
      if (text && $("#d-note")) $("#d-note").value = text;
      return vote({ confirm: "confirmed", flag: "flagged", reject: "rejected" }[cmd]);
    case "queue": {
      const map = { tovet: "new,flagged", confirmed: "confirmed", rejected: "rejected", all: "" };
      const st = map[text] ?? "new,flagged";
      $$("#status-chips .chip").forEach((x) => x.classList.toggle("active", x.dataset.status === st));
      S.filter.status = st;
      return loadQueue();
    }
    case "view": return setView(text);
    case "stop":
      for (const j of S.jobs.keys()) if (text === "all" || !text || text === j) await post(`/api/jobs/${j}/cancel`, {});
      return;
    case "export": window.location.href = `/api/export?status=confirmed&format=${text || "csv"}`; return;
    case "clear": logEl.innerHTML = ""; return;
    case "": return;
    default:
      logLine({ src: "SYS", msg: `unknown command '${cmd}' — try help`, level: "warn" });
  }
}

const cmdEl = $("#cmd"), sugEl = $("#suggest");
let hIdx = -1, sIdx = -1;
function suggestions() {
  const v = cmdEl.value.trim().toLowerCase();
  if (!v || v.includes(" ")) return [];
  return COMMANDS.filter(([c]) => c.startsWith(v) || c.split(/[ |]/)[0].startsWith(v)).slice(0, 7);
}
function renderSuggest() {
  const s = suggestions();
  sugEl.hidden = !s.length;
  sugEl.innerHTML = s.map(([c, d], i) => `<div class="${i === sIdx ? "on" : ""}" data-c="${esc(c.split(" ")[0].split("|")[0])}">${esc(c)}<small>${esc(d)}</small></div>`).join("");
}
cmdEl.addEventListener("input", () => { sIdx = -1; renderSuggest(); });
cmdEl.addEventListener("blur", () => setTimeout(() => { sugEl.hidden = true; }, 150));
sugEl.addEventListener("mousedown", (e) => { const d = e.target.closest("[data-c]"); if (d) { cmdEl.value = d.dataset.c + " "; sugEl.hidden = true; cmdEl.focus(); } });
cmdEl.addEventListener("keydown", (e) => {
  const s = suggestions();
  if (e.key === "Tab" && s.length) { e.preventDefault(); cmdEl.value = s[Math.max(0, sIdx)][0].split(" ")[0].split("|")[0] + " "; sugEl.hidden = true; return; }
  if (e.key === "ArrowUp" || e.key === "ArrowDown") {
    e.preventDefault();
    if (!sugEl.hidden && s.length) { sIdx = (sIdx + (e.key === "ArrowDown" ? 1 : -1) + s.length) % s.length; renderSuggest(); return; }
    hIdx = Math.max(-1, Math.min(S.history.length - 1, hIdx + (e.key === "ArrowUp" ? 1 : -1)));
    cmdEl.value = hIdx >= 0 ? S.history[S.history.length - 1 - hIdx] : "";
  }
  if (e.key === "Escape") { cmdEl.blur(); sugEl.hidden = true; }
});
$("#prompt").addEventListener("submit", async (e) => {
  e.preventDefault();
  const line = cmdEl.value;
  cmdEl.value = "";
  sugEl.hidden = true;
  hIdx = -1;
  if (!line.trim()) return;
  S.history.push(line);
  S.history = S.history.slice(-100);
  store.set("history", S.history);
  try { await exec(line); } catch (err) { logLine({ src: "SYS", msg: err.message, level: "error" }); }
});

// ── keyboard ─────────────────────────────────────────────────────────
document.addEventListener("keydown", (e) => {
  const tag = document.activeElement?.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
    if (e.key === "Escape") document.activeElement.blur();
    return;
  }
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const k = e.key.toLowerCase();
  if (k === "j" || e.key === "ArrowDown") { e.preventDefault(); move(1); }
  else if (k === "k" || e.key === "ArrowUp") { e.preventDefault(); move(-1); }
  else if (k === "c") vote("confirmed");
  else if (k === "f") vote("flagged");
  else if (k === "r") vote("rejected");
  else if (k === "/") { e.preventDefault(); cmdEl.focus(); }
  else if (k === " " && S.view === "signal") { e.preventDefault(); S.sig?.replay?.(); }
  else if ("12345".includes(k) && k) setView(["sky", "signal", "field", "solar", "log"][+k - 1]);
  else if (e.key === "Escape") $("#modal").hidden = true;
});

// ── start ────────────────────────────────────────────────────────────
renderForm();
setStages(["FETCH", "CLEAN", "VARIABILITY", "DETREND", "BLS", "VET", "XMATCH"]);
boot().then(() => {
  connect();
  loadQueue();
  refreshStats();
  logLine({ src: "SYS", msg: "type help for commands · J/K move through the queue · C/F/R vet", cls: "sys" });
});
