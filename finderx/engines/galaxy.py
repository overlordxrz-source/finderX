"""DEEP engine — quasar and galaxy candidates nobody has catalogued.

Inputs come from two independent all-sky surveys:
  * Gaia DR3 extragalactic classifier (QSO / galaxy probabilities, QSOC redshift)
  * AllWISE mid-infrared colours (W1−W2 ≥ 0.8 → AGN, Stern et al. 2012)

A quasar candidate is strong when both agree and Gaia astrometry is
consistent with zero motion. Each candidate is checked against Milliquas,
SIMBAD and NED; only sources with no extragalactic classification survive.
"""

from __future__ import annotations

import math

import numpy as np

from .. import config, net
from ..jobs import JobContext

STAGES = ["GAIA-QSO", "GAIA-GAL", "WISE", "FUSE", "MILLIQUAS", "QUAIA", "SIMBAD", "NED", "SCORE"]
_EXTRAGAL_SIMBAD = ("QSO", "AGN", "Sy", "Bla", "BLL", "LIN", "G", "rG", "EmG", "SBG", "LSB", "bCG", "IG", "PaG", "GiC", "BiC", "GiG", "GiP", "H2G", "LeG", "LeQ")
_EXTRAGAL_NED = ("G", "QSO", "GPair", "GTrpl", "GGroup", "GClstr", "QGroup", "Q_Lens", "G_Lens", "AbLS", "RadioS")


def _sep(ra1, dec1, ra2, dec2):
    """Angular separation in arcsec (small-angle, vectorised)."""
    dra = (np.asarray(ra2) - ra1) * np.cos(np.radians(dec1))
    return 3600.0 * np.hypot(dra, np.asarray(dec2) - dec1)


def run(ctx: JobContext) -> dict:
    p = ctx.params
    ra, dec = float(p["ra"]), float(p["dec"])
    radius = min(float(p.get("radius", 0.5)), 1.0)
    include_known = bool(p.get("include_known", False))
    ctx.emit("pipeline", stages=STAGES)
    ctx.emit("target", ra=ra, dec=dec, radius=radius, label="deep field")
    cone = lambda rc, dc: net.cone(rc, dc, ra, dec, radius)  # noqa: E731

    ctx.stage("GAIA-QSO", "run")
    qso = net.tap(config.VIZIER_TAP, f'SELECT Source, RA_ICRS, DE_ICRS, PQSO, PGal, z, flagsQSOC, ClassDSCC FROM "{config.VZ_GAIA_QSO}" WHERE {cone("RA_ICRS", "DE_ICRS")} AND PQSO > 0.5', timeout=120)
    ctx.log(f"Gaia DR3 QSO candidates (P>0.5): {len(qso)}", src="GAIA")
    ctx.stage("GAIA-QSO", "ok")

    ctx.stage("GAIA-GAL", "run")
    gal = net.tap(config.VIZIER_TAP, f'SELECT Source, RA_ICRS, DE_ICRS, PGal, PQSO, z, RadS, ClassDSCC FROM "{config.VZ_GAIA_GAL}" WHERE {cone("RA_ICRS", "DE_ICRS")} AND PGal > 0.9', timeout=120)
    ctx.log(f"Gaia DR3 galaxy candidates (P>0.9): {len(gal)}", src="GAIA")
    ctx.stage("GAIA-GAL", "ok")

    ctx.stage("WISE", "run")
    wise = net.tap(config.VIZIER_TAP, f'SELECT TOP 60000 AllWISE, RAJ2000, DEJ2000, W1mag, W2mag, W3mag, ccf, ex FROM "{config.VZ_ALLWISE}" WHERE {cone("RAJ2000", "DEJ2000")} AND W2mag < 16', timeout=180)
    ctx.log(f"AllWISE sources (W2<16): {len(wise):,}", src="WISE")
    ctx.stage("WISE", "ok")

    ctx.stage("FUSE", "run")
    w_ra = np.array([w["RAJ2000"] for w in wise]) if wise else np.zeros(0)
    w_dec = np.array([w["DEJ2000"] for w in wise]) if wise else np.zeros(0)

    def wise_at(r_, d_, tol=2.0):
        if len(w_ra) == 0:
            return None
        s = _sep(r_, d_, w_ra, w_dec)
        k = int(np.argmin(s))
        return wise[k] if s[k] < tol else None

    # Gaia astrometry for QSO candidates (zero parallax / proper motion test)
    astro: dict[int, dict] = {}
    if qso:
        ids = ",".join(str(q["Source"]) for q in qso[:2000])
        try:
            for r in net.tap(config.VIZIER_TAP, f'SELECT Source, Plx, e_Plx, pmRA, e_pmRA, pmDE, e_pmDE, Gmag FROM "{config.VZ_GAIA}" WHERE Source IN ({ids})', timeout=120):
                astro[int(r["Source"])] = r
        except Exception as exc:
            ctx.log(f"Gaia astrometry lookup failed: {exc}", level="warn", src="GAIA")

    cands: list[dict] = []
    for q in qso:
        w = wise_at(q["RA_ICRS"], q["DE_ICRS"])
        a = astro.get(int(q["Source"]), {})
        w12 = (w["W1mag"] - w["W2mag"]) if w and w.get("W1mag") is not None and w.get("W2mag") is not None else None
        zero_motion = _zero_motion(a)
        tags = ["GAIA_QSO"]
        if w12 is not None and w12 >= 0.8:
            tags.append("WISE_AGN_COLOURS")
        if zero_motion:
            tags.append("ZERO_ASTROMETRIC_MOTION")
        elif zero_motion is False:
            tags.append("MOVES_LIKE_A_STAR")
        # QSOC redshifts with any flag bit set are often aliased (Gaia DR3 docs)
        z_ok = q.get("z") is not None and not (q.get("flagsQSOC") or 0)
        if z_ok and q["z"] > 3.5 and "WISE_AGN_COLOURS" in tags:
            tags.append("HIGH_REDSHIFT")
        strength = (q.get("PQSO") or 0) * 0.45 + (0.25 if "WISE_AGN_COLOURS" in tags else 0) + (0.1 if zero_motion else 0) + (0.2 if "HIGH_REDSHIFT" in tags else 0)
        if "MOVES_LIKE_A_STAR" in tags:
            strength *= 0.3
        cands.append({"kind": "quasar", "ra": q["RA_ICRS"], "dec": q["DE_ICRS"], "id": f"Gaia DR3 {q['Source']}", "tags": tags, "strength": strength,
                      "m": {"source_id": str(q["Source"]), "p_qso": q.get("PQSO"), "p_gal": q.get("PGal"), "z_qsoc": q.get("z"), "z_qsoc_reliable": z_ok, "G": a.get("Gmag"), "W1-W2": round(w12, 3) if w12 is not None else None, "W1": w.get("W1mag") if w else None, "W2": w.get("W2mag") if w else None, "W3": w.get("W3mag") if w else None, "parallax": a.get("Plx"), "parallax_err": a.get("e_Plx"), "pmra": a.get("pmRA"), "pmdec": a.get("pmDE")}})
    for g in gal:
        w = wise_at(g["RA_ICRS"], g["DE_ICRS"], 3.0)
        w12 = (w["W1mag"] - w["W2mag"]) if w and w.get("W1mag") is not None and w.get("W2mag") is not None else None
        tags = ["GAIA_GALAXY"]
        if w12 is not None and w12 >= 0.8:
            tags.append("WISE_AGN_COLOURS")
        cands.append({"kind": "galaxy", "ra": g["RA_ICRS"], "dec": g["DE_ICRS"], "id": f"Gaia DR3 {g['Source']}", "tags": tags, "strength": (g.get("PGal") or 0) * 0.6,
                      "m": {"source_id": str(g["Source"]), "p_gal": g.get("PGal"), "p_qso": g.get("PQSO"), "z_gal": g.get("z"), "radius_mas": g.get("RadS"), "W1-W2": round(w12, 3) if w12 is not None else None}})
    # mid-IR-only AGN (dust-obscured, often invisible to Gaia)
    gaia_ra = np.array([c["ra"] for c in cands]) if cands else np.zeros(0)
    gaia_dec = np.array([c["dec"] for c in cands]) if cands else np.zeros(0)
    for w in wise:
        if w.get("W1mag") is None or w.get("W2mag") is None or (w.get("ccf") or "0000").strip("0"):
            continue
        w12 = w["W1mag"] - w["W2mag"]
        if w12 < 0.8 or w["W2mag"] > 15.05:
            continue
        if len(gaia_ra) and _sep(w["RAJ2000"], w["DEJ2000"], gaia_ra, gaia_dec).min() < 2.0:
            continue
        cands.append({"kind": "obscured_agn", "ra": w["RAJ2000"], "dec": w["DEJ2000"], "id": f"AllWISE {w['AllWISE']}", "tags": ["WISE_AGN_COLOURS", "NO_GAIA_COUNTERPART"], "strength": min(0.75, 0.35 + 0.25 * (w12 - 0.8)),
                      "m": {"W1": w["W1mag"], "W2": w["W2mag"], "W3": w.get("W3mag"), "W1-W2": round(w12, 3), "extended": w.get("ex")}})
    ctx.log(f"{len(cands)} candidates after fusing Gaia + WISE", src="FUSE")
    ctx.stage("FUSE", "ok")
    if not cands:
        return {"candidates": 0}

    c_ra = np.array([c["ra"] for c in cands])
    c_dec = np.array([c["dec"] for c in cands])

    ctx.stage("MILLIQUAS", "run")
    try:
        mq = net.tap(config.VIZIER_TAP, f'SELECT Name, Type, z, RAJ2000, DEJ2000 FROM "{config.VZ_MILLIQUAS}" WHERE {cone("RAJ2000", "DEJ2000")}', timeout=120)
        for m in mq:
            s = _sep(m["RAJ2000"], m["DEJ2000"], c_ra, c_dec)
            for k in np.where(s < 3.0)[0]:
                cands[k].setdefault("known", []).append({"kind": "milliquas", "label": str(m["Name"]).strip(), "type": str(m.get("Type", "")).strip(), "z": m.get("z"), "match": "position"})
        ctx.log(f"Milliquas: {len(mq)} catalogued quasars in field", src="CDS")
    except Exception as exc:
        ctx.log(f"Milliquas lookup failed: {exc}", level="warn", src="CDS")
    ctx.stage("MILLIQUAS", "ok")

    ctx.stage("QUAIA", "run")
    try:
        quaia = {int(q["GaiaDR3"]): q for q in net.tap(config.VIZIER_TAP, f'SELECT GaiaDR3, objID, zQuaia FROM "{config.VZ_QUAIA}" WHERE {cone("RA_ICRS", "DE_ICRS")}', timeout=120)}
        n = 0
        for c in cands:
            sid = c["m"].get("source_id")
            if sid and int(sid) in quaia:
                q = quaia[int(sid)]
                c.setdefault("known", []).append({"kind": "quaia", "label": f"Quaia {q['objID']}", "type": "QSO (photometric)", "z": q.get("zQuaia"), "match": "source_id", "extragalactic": True})
                n += 1
        ctx.log(f"Quaia (Gaia × unWISE quasars, G<20): {len(quaia)} in field, {n} of our candidates already listed", src="CDS")
    except Exception as exc:
        ctx.log(f"Quaia lookup failed: {exc}", level="warn", src="CDS")
    ctx.stage("QUAIA", "ok")

    ctx.stage("SIMBAD", "run")
    try:
        for m in net.xmatch(list(zip(c_ra, c_dec)), "simbad", 3.0):
            k = int(m["row"])
            ot = str(m.get("otype") or "")
            cands[k].setdefault("known", []).append({"kind": "simbad", "label": str(m.get("main_id")), "type": ot, "refs": m.get("nbref"), "match": "position", "extragalactic": ot.startswith(_EXTRAGAL_SIMBAD)})
        ctx.log("SIMBAD cross-match done", src="CDS")
    except Exception as exc:
        ctx.log(f"SIMBAD X-Match failed: {exc}", level="warn", src="CDS")
    ctx.stage("SIMBAD", "ok")

    ctx.stage("NED", "run")
    # only ask NED about sources nobody else has classified; OR-ed 3" cones
    open_idx = [k for k, c in enumerate(cands) if not any(x["kind"] == "milliquas" or x.get("extragalactic") for x in c.get("known", []))]
    hits = 0
    try:
        for chunk in range(0, len(open_idx), 25):
            ks = open_idx[chunk:chunk + 25]
            where = " OR ".join(net.cone("ra", "dec", cands[k]["ra"], cands[k]["dec"], 3 / 3600, "J2000") for k in ks)
            for n in net.tap(config.NED_TAP, f"SELECT prefname, ra, dec, prefphytype, z FROM objdir WHERE {where}", timeout=120):
                if n.get("ra") is None:
                    continue
                s_ = _sep(n["ra"], n["dec"], c_ra[ks], c_dec[ks])
                for j in np.where(s_ < 3.0)[0]:
                    t = str(n.get("prefphytype") or "").strip()
                    cands[ks[j]].setdefault("known", []).append({"kind": "ned", "label": str(n["prefname"]).strip(), "type": t, "z": n.get("z"), "match": "position", "extragalactic": t in _EXTRAGAL_NED or n.get("z") is not None})
                    hits += 1
        ctx.log(f"NED: checked {len(open_idx)} unclassified sources, {hits} NED entries found", src="NED")
    except Exception as exc:
        ctx.log(f"NED lookup failed: {exc}", level="warn", src="NED")
    ctx.stage("NED", "ok")

    ctx.stage("SCORE", "run")
    wise_plot = [(round(w["W2mag"] - w["W3mag"], 3), round(w["W1mag"] - w["W2mag"], 3)) for w in wise[:8000] if w.get("W1mag") is not None and w.get("W2mag") is not None and w.get("W3mag") is not None]
    pid = f"galaxy:{ctx.id}"
    ctx.db.payload_put(pid, {"wise": wise_plot, "field": {"ra": ra, "dec": dec, "radius": radius}})
    emitted = 0
    for c in sorted(cands, key=lambda c: -c["strength"]):
        known = c.get("known", [])
        classified = [k for k in known if k["kind"] == "milliquas" or k.get("extragalactic")]
        # a re-scan that now finds a catalogue entry must update the old row
        if classified and not include_known and not ctx.db.has_candidate(f"galaxy:{c['id']}"):
            continue
        flags = list(c["tags"])
        if not known:
            flags.append("NO_CATALOGUE_ENTRY")
        elif not classified:
            flags.append("NOT_CLASSIFIED_EXTRAGALACTIC")
        score = c["strength"] * (0.25 if classified else 1.0)
        if c["kind"] == "obscured_agn" and emitted > 60:
            continue
        m = c["m"]
        if c["kind"] == "quasar":
            sub = f"quasar candidate · P={m.get('p_qso') or 0:.2f}"
            sub += f" · z≈{m['z_qsoc']:.2f}{'' if m.get('z_qsoc_reliable') else '?'}" if m.get("z_qsoc") else ""
            sub += f" · W1−W2 {m['W1-W2']:.2f}" if m.get("W1-W2") is not None else ""
        elif c["kind"] == "galaxy":
            sub = f"galaxy candidate · P={m.get('p_gal') or 0:.2f}" + (f" · z≈{m['z_gal']:.3f}" if m.get("z_gal") else "")
        else:
            sub = f"mid-IR AGN, no optical counterpart · W1−W2 {m['W1-W2']:.2f}"
        ctx.candidate(
            {
                "engine": "galaxy",
                "kind": c["kind"] if not classified else f"known_{c['kind']}",
                "target": c["id"],
                "dedupe": f"galaxy:{c['id']}",
                "ra": c["ra"],
                "dec": c["dec"],
                "title": c["id"],
                "subtitle": sub,
                "score": round(min(1.0, score), 3),
                "known": known,
                "flags": flags,
                "payload": pid,
                "metrics": m,
            }
        )
        emitted += 1
    ctx.stage("SCORE", "ok")
    ctx.log(f"{emitted} extragalactic candidates logged", src="DEEP")
    return {"candidates": emitted, "gaia_qso": len(qso), "gaia_gal": len(gal), "wise": len(wise)}


def _zero_motion(a: dict) -> bool | None:
    try:
        plx_z = abs(a["Plx"] / a["e_Plx"])
        pm_z = math.hypot(a["pmRA"] / a["e_pmRA"], a["pmDE"] / a["e_pmDE"])
    except (KeyError, TypeError, ZeroDivisionError):
        return None
    return plx_z < 4 and pm_z < 5
