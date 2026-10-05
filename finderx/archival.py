"""Archival photometry check: catch the eclipsing star in old survey data.

Pan-STARRS1 (2010–2014, Dec > −30°) and Gaia DR3 epoch photometry (published
variables, whole sky) observed every star around a TESS candidate dozens of
times at ~1″ resolution. Folding each star's measurements on the TESS
ephemeris shows which star was ever caught mid-eclipse — or proves a star
was at full brightness when it should have been eclipsing.
"""

from __future__ import annotations

import math

import numpy as np

from . import config, net

PS1_DETECTIONS = "https://catalogs.mast.stsci.edu/api/v0.1/panstarrs/dr2/detection.csv"
GAIA_EPOCHS = "https://gea.esac.esa.int/data-server/data"
MJD_TO_BTJD = 2400000.5 - 2457000.0
GAIA_TO_BTJD = 2455197.5 - 2457000.0
SYS_FLOOR = 0.01            # 1 % calibration floor added to every survey point


def ps1_lightcurves(ra: float, dec: float, radius_arcsec: float = 60.0) -> dict[str, dict]:
    if dec < -31:
        return {}
    r = net.get(
        PS1_DETECTIONS,
        params={
            "ra": ra, "dec": dec, "radius": radius_arcsec / 3600, "pagesize": 50000,
            "columns": "[objID,obsTime,filterID,psfFlux,psfFluxErr,ra,dec]",
        },
        timeout=120,
    )
    rows = net.parse_csv(r.text)
    by: dict[str, list[dict]] = {}
    for row in rows:
        f, e = row.get("psfFlux"), row.get("psfFluxErr")
        if f is None or e is None or f <= 0 or e <= 0:
            continue
        by.setdefault(str(row["objID"]), []).append(row)
    # each star's flux relative to its own median, per band
    rel: dict[str, list[tuple[float, int, float, float]]] = {}
    for oid, pts in by.items():
        for fid in {p["filterID"] for p in pts}:
            band = [p for p in pts if p["filterID"] == fid]
            if len(band) < 3:
                continue
            med = float(np.median([p["psfFlux"] for p in band]))
            for p in band:
                rel.setdefault(oid, []).append((p["obsTime"], fid, p["psfFlux"] / med, p["psfFluxErr"] / med))
    # ensemble correction: the median star in each exposure sets its zero point
    expo: dict[tuple[float, int], list[float]] = {}
    for pts in rel.values():
        for t, fid, f, _ in pts:
            expo.setdefault((round(t, 5), fid), []).append(f)
    zp = {k: float(np.median(v)) for k, v in expo.items() if len(v) >= 5}
    out = {}
    for oid, pts in by.items():
        t, fl, er = [], [], []
        for tt, fid, f, e in rel.get(oid, []):
            z = zp.get((round(tt, 5), fid))
            if z is None or z <= 0:
                continue
            t.append(tt + MJD_TO_BTJD)
            fl.append(f / z)
            er.append(math.hypot(e, SYS_FLOOR))
        if len(t) >= 6:
            out[oid] = {
                "survey": "Pan-STARRS1",
                "id": f"PS1 {oid}",
                "ra": float(np.median([p["ra"] for p in pts])),
                "dec": float(np.median([p["dec"] for p in pts])),
                "t": np.array(t), "f": np.array(fl), "e": np.array(er),
            }
    return out


def gaia_epoch_lightcurves(ra: float, dec: float, radius_arcsec: float = 60.0) -> dict[str, dict]:
    """Gaia DR3 epoch G photometry for published variables near the target."""
    q = (
        f'SELECT Source, RA_ICRS, DE_ICRS FROM "I/358/vclassre" '
        f"WHERE {net.cone('RA_ICRS', 'DE_ICRS', ra, dec, radius_arcsec / 3600)}"
    )
    out = {}
    for s in net.tap(config.VIZIER_TAP, q, timeout=60)[:12]:
        try:
            r = net.get(GAIA_EPOCHS, params={"RETRIEVAL_TYPE": "EPOCH_PHOTOMETRY", "ID": s["Source"], "FORMAT": "CSV", "VALID_DATA": "1"}, timeout=60)
        except net.ArchiveError:
            continue
        rows = [x for x in net.parse_csv(r.text) if x.get("g_transit_flux") and str(x.get("variability_flag_g_reject")).lower() in ("false", "0", "none", "")]
        if len(rows) < 6:
            continue
        f = np.array([x["g_transit_flux"] for x in rows], float)
        e = np.array([x["g_transit_flux_error"] for x in rows], float)
        med = float(np.median(f))
        out[str(s["Source"])] = {
            "survey": "Gaia DR3 epochs",
            "id": f"Gaia DR3 {s['Source']}",
            "gaia": str(s["Source"]),
            "ra": s["RA_ICRS"], "dec": s["DE_ICRS"],
            "t": np.array([x["g_transit_time"] for x in rows], float) + GAIA_TO_BTJD,
            "f": f / med,
            "e": np.hypot(e / med, 0.003),
        }
    return out


def ephemeris_sigma(period: float, duration: float, snr: float, n_transits: int, baseline: float) -> tuple[float, float]:
    """Rough 1σ uncertainties on (t_ref, P) from the TESS detection."""
    per_event = duration / max(2.0, snr / math.sqrt(max(n_transits, 1)))
    sigma_t0 = per_event / math.sqrt(max(n_transits, 1))
    cycles = max(1.0, baseline / period)
    sigma_p = per_event * math.sqrt(12.0 / max(n_transits, 2)) / cycles
    return sigma_t0, sigma_p


def eclipse_test(lc: dict, period: float, t_ref: float, duration: float, depth: float, sigma_t0: float, sigma_p: float) -> dict:
    """Δχ² between 'this star eclipses by `depth`' and 'constant'."""
    t, f, e = lc["t"], lc["f"], lc["e"]
    n_cyc = (t - t_ref) / period
    dt = (n_cyc - np.round(n_cyc)) * period          # time from the nearest predicted mid-eclipse
    sig_t = np.sqrt(sigma_t0**2 + (np.abs(n_cyc) * sigma_p) ** 2)
    usable = sig_t < duration * 1.5
    inn = usable & (np.abs(dt) < duration / 2 + 1.0 * sig_t)
    model = np.where(inn, 1.0 - depth, 1.0)
    chi_const = np.sum(((f - 1.0) / e)[usable] ** 2)
    chi_ecl = np.sum(((f - model) / e)[usable] ** 2)
    return {
        "n": int(usable.sum()),
        "n_in": int(inn.sum()),
        "dchi2": round(float(chi_const - chi_ecl), 1),
        "in_points": [
            {"t": round(float(a), 3), "f": round(float(b), 4), "e": round(float(c), 4)}
            for a, b, c in zip(t[inn], f[inn], e[inn])
        ][:12],
        "timing_sigma_h": round(float(np.median(sig_t[usable]) * 24), 2) if usable.any() else None,
    }


def check(ra: float, dec: float, period: float, t_ref: float, duration: float, depth: float,
          stars: list[dict], target_gaia: str | None, sigma_t0: float, sigma_p: float, log=lambda m: None) -> dict:
    """Run the archival test for every star near the candidate."""
    lcs: dict[str, dict] = {}
    try:
        ps1 = ps1_lightcurves(ra, dec)
        lcs.update(ps1)
        if dec >= -31:
            log(f"Pan-STARRS1: {len(ps1)} stars with multi-epoch photometry within 60″")
    except Exception as exc:
        log(f"Pan-STARRS1 query failed ({exc})")
    try:
        gaia = gaia_epoch_lightcurves(ra, dec)
        lcs.update(gaia)
        log(f"Gaia DR3 epoch photometry: {len(gaia)} published variables within 60″")
    except Exception as exc:
        log(f"Gaia epoch photometry failed ({exc})")
    if not lcs:
        return {"verdict": "no_data", "reason": "no archival multi-epoch photometry covers this field", "stars": []}

    g_ra = np.array([s["RA_ICRS"] for s in stars]) if stars else np.zeros(0)
    g_de = np.array([s["DE_ICRS"] for s in stars]) if stars else np.zeros(0)
    target_g = next((s["Gmag"] for s in stars if str(s["Source"]) == str(target_gaia)), None)
    rows = []
    for key, lc in lcs.items():
        gaia_id, gmag = lc.get("gaia"), None
        if len(g_ra):
            sep = 3600 * np.hypot((g_ra - lc["ra"]) * math.cos(math.radians(lc["dec"])), g_de - lc["dec"])
            k = int(np.argmin(sep))
            if sep[k] < 1.5:
                gaia_id, gmag = gaia_id or str(stars[k]["Source"]), stars[k]["Gmag"]
        if lc["survey"] == "Pan-STARRS1" and gmag is not None and gmag < 14.0:
            continue  # saturated in PS1 exposures: photometry is unreliable
        is_target = gaia_id is not None and str(gaia_id) == str(target_gaia)
        if is_target:
            need = depth
        elif gmag is not None and target_g is not None:
            need = depth * (1 + 10 ** (0.4 * (gmag - target_g)))
        else:
            continue
        if need >= 1.0:
            continue
        res = eclipse_test(lc, period, t_ref, duration, need, sigma_t0, sigma_p)
        if res["n_in"] == 0:
            continue
        sep_t = 3600 * math.hypot((lc["ra"] - ra) * math.cos(math.radians(dec)), lc["dec"] - dec)
        rows.append({"id": lc["id"], "survey": lc["survey"], "gaia": gaia_id, "G": gmag, "is_target": is_target,
                     "sep_arcsec": round(sep_t, 1), "needed_depth": round(need, 4), **res})
    if not rows:
        return {"verdict": "no_coverage", "reason": "no archival epoch fell inside a predicted eclipse", "stars": []}
    rows.sort(key=lambda r: -r["dchi2"])
    best = rows[0]
    target_row = next((r for r in rows if r["is_target"]), None)
    if best["dchi2"] >= 9 and (len(rows) == 1 or rows[1]["dchi2"] < best["dchi2"] - 6):
        who = "the target" if best["is_target"] else f"{best['id']} ({best['sep_arcsec']:.0f}″ away)"
        verdict = "caught_on_target" if best["is_target"] else "caught_on_neighbour"
        reason = f"{best['survey']} caught {who} {best['n_in']}× inside a predicted eclipse, dimmer as expected (Δχ² {best['dchi2']:.0f})"
    elif target_row and target_row["dchi2"] <= -9:
        verdict = "target_ruled_out"
        reason = f"the target was at full brightness during {target_row['n_in']} predicted eclipse(s) (Δχ² {target_row['dchi2']:.0f})"
    elif any(r["dchi2"] <= -9 and not r["is_target"] for r in rows):
        ruled = [r for r in rows if r["dchi2"] <= -9 and not r["is_target"]]
        verdict = "neighbours_excluded"
        reason = f"{len(ruled)} neighbour(s) were at full brightness mid-eclipse, so they are not the source"
    else:
        verdict = "inconclusive"
        reason = "archival points inside predicted eclipses do not single out a star"
    for r in rows:
        r["status"] = "caught" if r["dchi2"] >= 9 else "excluded" if r["dchi2"] <= -9 else "—"
    rows.sort(key=lambda r: (-(r["dchi2"] >= 9), -abs(r["dchi2"])))
    return {"verdict": verdict, "reason": reason, "stars": rows[:12]}
