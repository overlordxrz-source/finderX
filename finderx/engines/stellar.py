"""STELLAR engine — Gaia DR3 census of a sky field.

Hunts four kinds of interesting stars in a cone and asks SIMBAD (via CDS
X-Match, positions back-propagated to J2000) whether anyone has studied them:

  HIGH_VELOCITY     tangential velocity > 400 km/s (halo / runaway stars)
  NEARBY            within 50 pc
  HIDDEN_COMPANION  within 100 pc with RUWE > 2 (astrometric wobble → unseen companion)
  ULTRACOOL         M_G > 15 and very red (late-M / L dwarfs) within 100 pc
  WHITE_DWARF       below the main sequence; checked against Gentile Fusillo+21
"""

from __future__ import annotations

import math

import numpy as np

from .. import config, net
from ..jobs import JobContext

STAGES = ["GAIA", "SELECT", "SIMBAD", "WD-CAT", "SCORE"]

_COLS = 'Source, RA_ICRS, DE_ICRS, Plx, e_Plx, pmRA, pmDE, Gmag, "BP-RP", RUWE, RV'
_BINARY_TYPES = ("**", "SB*", "EB*", "El*", "*i*", "BD*")


def run(ctx: JobContext) -> dict:
    p = ctx.params
    ra, dec = float(p["ra"]), float(p["dec"])
    radius = min(float(p.get("radius", 1.0)), 3.0)
    include_known = bool(p.get("include_known", False))
    ctx.emit("pipeline", stages=STAGES)
    ctx.emit("target", ra=ra, dec=dec, radius=radius, label="Gaia cone")

    ctx.stage("GAIA", "run")
    q = (
        f'SELECT TOP 40000 {_COLS} FROM "{config.VZ_GAIA}" '
        f"WHERE {net.cone('RA_ICRS', 'DE_ICRS', ra, dec, radius)} AND Plx > 1.0 AND Plx > 5*e_Plx"
    )
    ctx.log(f"Gaia DR3 cone {ra:.4f} {dec:+.4f} r={radius}° (ϖ > 1 mas, ϖ/σ > 5) …", src="GAIA")
    rows = net.tap(config.VIZIER_TAP, q, timeout=180)
    ctx.log(f"{len(rows):,} stars within ~1 kpc retrieved", src="GAIA")
    ctx.stage("GAIA", "ok")
    if not rows:
        return {"stars": 0}

    ctx.stage("SELECT", "run")
    picks: list[tuple[dict, list[str], dict]] = []
    hr_bg = []
    for r in rows:
        plx, e_plx = r.get("Plx"), r.get("e_Plx")
        g, bprp, ruwe = r.get("Gmag"), r.get("BP-RP"), r.get("RUWE")
        if plx is None or g is None:
            continue
        mg = g + 5 * math.log10(plx / 100.0)
        dist = 1000.0 / plx
        pm = math.hypot(r.get("pmRA") or 0, r.get("pmDE") or 0)
        vtan = 4.74047 * pm / plx
        d = {"M_G": round(mg, 3), "dist_pc": round(dist, 2), "pm": round(pm, 2), "v_tan": round(vtan, 1)}
        if bprp is not None and len(hr_bg) < 6000:
            hr_bg.append((round(bprp, 3), round(mg, 3)))
        tags = []
        if vtan > 400 and plx / e_plx > 8 and (ruwe or 9) < 1.4:
            tags.append("HIGH_VELOCITY")
        if plx > 20 and plx / e_plx > 10:
            tags.append("NEARBY")
        if plx > 10 and (ruwe or 0) > 2.0 and g < 18:
            tags.append("HIDDEN_COMPANION")
        if plx > 10 and mg > 15 and (bprp is None or bprp > 3.0):
            tags.append("ULTRACOOL")
        if bprp is not None and mg > 9 and mg > 3.1 * bprp + 9.2 and plx / e_plx > 8:
            tags.append("WHITE_DWARF")
        if tags:
            picks.append((r, tags, d))
    ctx.log(f"{len(picks)} stars pass a hunt criterion", src="SELECT")
    ctx.stage("SELECT", "ok")
    if not picks:
        ctx.result({"kind": "summary", "stars": len(rows), "picks": 0})
        return {"stars": len(rows), "picks": 0}

    ctx.stage("SIMBAD", "run")
    pos2000 = []
    for r, _, _ in picks:
        dt = -16.0  # Gaia DR3 epoch 2016.0 → 2000.0
        cosd = max(math.cos(math.radians(r["DE_ICRS"])), 1e-6)
        pos2000.append((
            r["RA_ICRS"] + (r.get("pmRA") or 0) * dt / 3.6e6 / cosd,
            r["DE_ICRS"] + (r.get("pmDE") or 0) * dt / 3.6e6,
        ))
    simbad: dict[int, dict] = {}
    try:
        for m in net.xmatch(pos2000, "simbad", 5.0):
            i = int(m["row"])
            if i not in simbad or m["angDist"] < simbad[i]["angDist"]:
                simbad[i] = m
        ctx.log(f"SIMBAD: {len(simbad)}/{len(picks)} have an entry", src="CDS")
    except Exception as exc:
        ctx.log(f"SIMBAD X-Match failed: {exc}", level="warn", src="CDS")
    ctx.stage("SIMBAD", "ok")

    gf21: set[int] = set()
    wd_idx = [i for i, (_, tags, _) in enumerate(picks) if "WHITE_DWARF" in tags]
    if wd_idx:
        ctx.stage("WD-CAT", "run")
        try:
            pos = [(picks[i][0]["RA_ICRS"], picks[i][0]["DE_ICRS"]) for i in wd_idx]
            for m in net.xmatch(pos, "vizier:J/MNRAS/508/3877/maincat", 3.0):
                gf21.add(wd_idx[int(m["row"])])
            ctx.log(f"Gentile Fusillo+21 WD catalog: {len(gf21)}/{len(wd_idx)} already listed", src="CDS")
        except Exception as exc:
            ctx.log(f"WD catalog X-Match failed: {exc}", level="warn", src="CDS")
        ctx.stage("WD-CAT", "ok")

    ctx.stage("SCORE", "run")
    pid = f"stellar:{ctx.id}"
    ctx.db.payload_put(pid, {"hr": hr_bg, "field": {"ra": ra, "dec": dec, "radius": radius, "n": len(rows)}})
    emitted = 0
    for i, (r, tags, d) in enumerate(picks):
        sb = simbad.get(i)
        otype = (sb or {}).get("otype") or ""
        nbref = int((sb or {}).get("nbref") or 0)
        tags = list(tags)
        if "HIDDEN_COMPANION" in tags and any(b in otype for b in _BINARY_TYPES):
            tags.remove("HIDDEN_COMPANION")
        if "WHITE_DWARF" in tags and i in gf21 and sb:
            tags.remove("WHITE_DWARF")
        if not tags:
            continue
        flags = list(tags)
        if not sb:
            flags.append("NOT_IN_SIMBAD")
            novelty = 1.0
        elif nbref <= 2:
            flags.append("BARELY_STUDIED")
            novelty = 0.6
        else:
            novelty = 0.0
        if "WHITE_DWARF" in tags and i not in gf21 and "WD" not in otype:
            flags.append("NOT_IN_WD_CATALOG")
            novelty = max(novelty, 0.8)
        if novelty == 0 and not include_known:
            continue
        weight = {"HIGH_VELOCITY": 0.35, "HIDDEN_COMPANION": 0.25, "ULTRACOOL": 0.3, "WHITE_DWARF": 0.25, "NEARBY": 0.15}
        score = min(1.0, 0.5 * novelty + max(weight[t] for t in tags) + 0.05 * (len(tags) - 1))
        kind = tags[0].lower()
        src = str(r["Source"])
        known = [{"kind": "simbad", "label": str(sb.get("main_id")), "type": otype, "refs": nbref, "match": "position"}] if sb else []
        ctx.candidate(
            {
                "engine": "stellar",
                "kind": kind,
                "target": f"Gaia DR3 {src}",
                "dedupe": f"stellar:{src}",
                "ra": r["RA_ICRS"],
                "dec": r["DE_ICRS"],
                "title": f"Gaia DR3 {src}",
                "subtitle": f"{' · '.join(t.replace('_', ' ').lower() for t in tags)} · {d['dist_pc']:.0f} pc · G {r['Gmag']:.1f}",
                "score": round(score, 3),
                "known": known,
                "flags": flags,
                "payload": pid,
                "metrics": {
                    "source_id": src,
                    "parallax": r.get("Plx"),
                    "parallax_err": r.get("e_Plx"),
                    "pmra": r.get("pmRA"),
                    "pmdec": r.get("pmDE"),
                    "G": r.get("Gmag"),
                    "bp_rp": r.get("BP-RP"),
                    "ruwe": r.get("RUWE"),
                    "rv": r.get("RV"),
                    **d,
                    "simbad_type": otype or None,
                    "simbad_refs": nbref if sb else None,
                },
            }
        )
        emitted += 1
    ctx.stage("SCORE", "ok")
    ctx.log(f"{emitted} stellar candidates logged", src="STELLAR")
    ctx.result({"kind": "summary", "stars": len(rows), "picks": len(picks), "candidates": emitted})
    return {"stars": len(rows), "picks": len(picks), "candidates": emitted}
