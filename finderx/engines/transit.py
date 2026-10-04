"""EXO engine — hunt TESS light curves for transiting planets and variables.

Modes (``params``):
  tic=<id>                      one star, up to ``max_sectors`` newest sectors
  sector=<n>, n=<count>         random sample of FFI light curves in a sector
  ra, dec, radius               every TESS light curve in a sky cone
  patrol=true                   keep sampling recent sectors until stopped

Per star: FETCH → CLEAN → VARIABILITY → DETREND → BLS → VET → CROSSMATCH.
"""

from __future__ import annotations

import math
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

from .. import analysis as A
from .. import catalogs, config, net
from .. import lightcurve as L
from ..jobs import Cancelled, JobContext

STAGES = ["FETCH", "CLEAN", "VARIABILITY", "DETREND", "BLS", "VET", "XMATCH"]


def run(ctx: JobContext) -> dict:
    p = ctx.params
    workers = int(p.get("workers", 4))
    ctx.emit("pipeline", stages=STAGES)
    ctx.log("loading reference catalogs (TOI · CTOI · confirmed planets · TESS EBs)", src="CAT")
    catalogs.warm(lambda m: ctx.log(m, src="CAT"))

    if p.get("tic"):
        tic = int(str(p["tic"]).replace("TIC", "").strip())
        prods = L.products_for_tic(tic)
        if not prods:
            ctx.log(f"TIC {tic}: no TESS light curves at MAST", level="warn", src="MAST")
            return {"stars": 0}
        nsec = int(p.get("sectors", config.TRANSIT["max_sectors"]))
        use = prods[:nsec]
        ctx.log(
            f"TIC {tic}: {len(prods)} sectors available, using {', '.join('S%d' % r['sequence_number'] for r in use)}",
            src="MAST",
        )
        _process(ctx, tic, use, include_known=True)
        return {"stars": 1}

    if p.get("ra") is not None and p.get("dec") is not None:
        return _cone(ctx, float(p["ra"]), float(p["dec"]), float(p.get("radius", 0.2)), int(p.get("n", 40)), workers)

    if p.get("patrol"):
        return _patrol(ctx, int(p.get("n", 40)), workers, p.get("sector"))

    sector = int(p.get("sector") or random.choice(_recent_sectors(ctx))[0])
    return _batch(ctx, sector, int(p.get("n", 25)), workers)


# ── target selection ───────────────────────────────────────────────────────

def _recent_sectors(ctx: JobContext) -> list[tuple[int, int, str]]:
    cached = ctx.db.meta_get("ffi_sectors_v2")
    if cached and time.time() - cached["at"] < 12 * 3600:
        return [tuple(x) for x in cached["sectors"]]
    ctx.log("probing MAST for the newest sectors with FFI light curves …", src="MAST")
    secs = L.available_ffi_sectors(limit=6)
    if not secs:
        raise RuntimeError("MAST returned no recent FFI sectors")
    ctx.db.meta_set("ffi_sectors_v2", {"at": time.time(), "sectors": secs})
    ctx.log("recent FFI sectors: " + ", ".join(f"S{s} ({n:,} {prov})" for s, n, prov in secs), src="MAST")
    return secs


def _pick_provenance(sector: int) -> tuple[str, int]:
    for prov in ("QLP", "TESS-SPOC"):
        n = L.sector_population(sector, prov)
        if n:
            return prov, n
    raise RuntimeError(f"no FFI light curves for sector {sector} at MAST")


def _fresh_targets(ctx: JobContext, sector: int, n: int, provenance: str, population: int | None = None) -> list[dict]:
    excluded = catalogs.excluded_tics()
    seen = ctx.db.examined_set("transit")
    rows = L.sample_sector(sector, n * 2 + 10, provenance=provenance, population=population)
    out = []
    skipped_known = 0
    for r in rows:
        tic = int(r["target_name"])
        if tic in excluded:
            skipped_known += 1
            continue
        if str(tic) in seen:
            continue
        out.append(r)
        if len(out) >= n:
            break
    ctx.log(
        f"S{sector} {provenance}: drew {len(rows)} light curves → {len(out)} fresh targets "
        f"({skipped_known} skipped as already-known TOI/planet/EB hosts)",
        src="MAST",
    )
    return out


def _batch(ctx: JobContext, sector: int, n: int, workers: int) -> dict:
    prov, pop = _pick_provenance(sector)
    targets = _fresh_targets(ctx, sector, n, prov, pop)
    _run_many(ctx, [(int(r["target_name"]), [r]) for r in targets], workers)
    return {"sector": sector, "stars": len(targets)}


def _cone(ctx: JobContext, ra: float, dec: float, radius: float, n: int, workers: int) -> dict:
    ctx.emit("target", ra=ra, dec=dec, radius=radius, label="cone")
    out = net.mast(
        "Mast.Caom.Filtered.Position",
        {
            "columns": "obsid,target_name,sequence_number,provenance_name,t_exptime,dataURL,s_ra,s_dec",
            "filters": [
                {"paramName": "obs_collection", "values": ["TESS", "HLSP"]},
                {"paramName": "dataproduct_type", "values": ["timeseries"]},
                {"paramName": "provenance_name", "values": ["SPOC", "TESS-SPOC", "QLP"]},
            ],
            "position": f"{ra}, {dec}, {radius}",
        },
    )
    by_tic: dict[int, dict] = {}
    rank = {"SPOC": 0, "TESS-SPOC": 1, "QLP": 2}
    for r in out.get("data", []):
        if not L._is_lc_product(r.get("dataURL", "")) or r.get("sequence_number") is None:
            continue
        tic = int(r["target_name"])
        cur = by_tic.get(tic)
        key = (r["sequence_number"], -rank.get(r["provenance_name"], 9))
        if cur is None or key > (cur["sequence_number"], -rank.get(cur["provenance_name"], 9)):
            by_tic[tic] = r
    ctx.log(f"cone {ra:.4f} {dec:+.4f} r={radius}°: {len(by_tic)} stars with TESS light curves", src="MAST")
    items = sorted(by_tic.items(), key=lambda kv: math.hypot(kv[1]["s_ra"] - ra, kv[1]["s_dec"] - dec))[:n]
    _run_many(ctx, [(tic, [r]) for tic, r in items], workers, include_known=True)
    return {"stars": len(items)}


def _patrol(ctx: JobContext, batch: int, workers: int, sector: int | None) -> dict:
    rounds = 0
    max_rounds = int(ctx.params.get("rounds") or 0)
    minutes = float(ctx.params.get("minutes") or 0)
    ctx.log("PATROL engaged — sampling fresh stars from recent sectors until stopped", src="PATROL")
    while not ctx.cancelled:
        if (max_rounds and rounds >= max_rounds) or (minutes and time.time() - ctx.started > minutes * 60):
            break
        if sector:
            s = int(sector)
            prov, pop = _pick_provenance(s)
        else:
            s, pop, prov = random.choice(_recent_sectors(ctx))
        rounds += 1
        ctx.log(f"round {rounds}: sector {s} ({prov})", src="PATROL")
        targets = _fresh_targets(ctx, s, batch, prov, pop)
        _run_many(ctx, [(int(r["target_name"]), [r]) for r in targets], workers)
    return {"rounds": rounds}


def _run_many(ctx: JobContext, jobs: list[tuple[int, list[dict]]], workers: int, include_known: bool = False) -> None:
    total = len(jobs)
    done = 0
    ctx.progress(0, total)
    with ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="star") as ex:
        futs = {ex.submit(_process, ctx, tic, prods, include_known): tic for tic, prods in jobs}
        for fut in as_completed(futs):
            done += 1
            ctx.progress(done, total)
            try:
                fut.result()
            except Cancelled:
                for f in futs:
                    f.cancel()
                raise
            except Exception as exc:
                ctx.log(f"TIC {futs[fut]}: {type(exc).__name__}: {exc}", level="warn")
            if ctx.cancelled:
                for f in futs:
                    f.cancel()
                raise Cancelled()


# ── per-star pipeline ──────────────────────────────────────────────────────

def _process(ctx: JobContext, tic: int, products: list[dict], include_known: bool = False) -> None:
    ctx.check()
    label = f"TIC {tic}"
    ctx.stage("FETCH", "run", label)
    lcs = []
    for prod in products:
        try:
            lcs.append(L.fetch(prod))
        except Exception as exc:
            ctx.log(f"{label} S{prod.get('sequence_number')}: download failed ({exc})", level="warn", src="MAST")
    if not lcs:
        ctx.stage("FETCH", "fail", label)
        ctx.mark(str(tic), "no-data")
        return
    lc = L.stitch(lcs)
    meta = lc.meta
    if meta.get("ra") is not None:
        ctx.emit("target", ra=meta["ra"], dec=meta["dec"], label=label)
    secs = meta.get("sectors") or [lc.sector]
    ctx.stage("CLEAN", "run", label)
    keep = L.clip_upper(lc.flux)
    t, f = lc.time[keep], lc.flux[keep]
    cen = (lc.centroid_col[keep], lc.centroid_row[keep]) if lc.centroid_col is not None else None
    star = A.Star.from_meta(meta)
    if len(t) < 300:
        ctx.mark(str(tic), "too-short")
        return

    ctx.stage("VARIABILITY", "run", label)
    # QLP light curves are spline-detrended; only short periods survive there
    var_cfg = {"max_period_d": 1.0} if "QLP" in lc.provenance else None
    var, ls_pgram = A.variability_search(t, f, star, var_cfg)

    ctx.stage("DETREND", "run", label)
    f_in = f
    if var and var.period < 2.0 and var.amplitude_ppm > 2000 and var.type_guess not in ("EB", "EW"):
        f_in = A.prewhiten(t, f, var.period)
    trend = L.detrend(t, f_in, config.TRANSIT["detrend_window_d"])
    fd = f_in / trend

    ctx.stage("BLS", "run", label)
    signals, bls_pgram = A.transit_search(t, fd, star, cen, log=lambda m: ctx.log(f"{label} {m}", level="debug", src="BLS"))

    ctx.stage("VET", "run", label)
    reportable = [s for s in signals if s.kind == "planet_candidate" or (s.kind == "eclipsing_binary" and s.snr >= 10)]
    if var and any(s.kind == "eclipsing_binary" and catalogs.period_match(var.true_period, s.period, 0.02) for s in signals):
        var = None  # the EB itself; already reported by the transit search

    outcome = "quiet"
    if not reportable and not var:
        ctx.mark(str(tic), outcome)
        ctx.stage("VET", "ok", label)
        return

    ctx.stage("XMATCH", "run", label)
    known_bulk = catalogs.known_for_tic(tic)
    vsx: list[dict] = []
    gaia_var: list[dict] = []
    if meta.get("ra") is not None:
        try:
            vsx = catalogs.vsx_cone(meta["ra"], meta["dec"])
        except Exception as exc:
            ctx.log(f"{label} VSX lookup failed ({exc})", level="warn", src="CDS")
        if var:
            try:
                gaia_var = catalogs.gaia_variability_cone(meta["ra"], meta["dec"], 10)
            except Exception:
                pass

    payload = _payload(lc, t, f, trend, fd, bls_pgram, ls_pgram, secs)
    pid = f"transit:{tic}:{'-'.join(map(str, secs))}"
    base = {
        "target": label,
        "ra": meta.get("ra"),
        "dec": meta.get("dec"),
        "payload": pid,
    }
    star_info = {
        "tic": tic,
        "tmag": meta.get("tmag"),
        "teff": meta.get("teff"),
        "radius": meta.get("radius"),
        "logg": meta.get("logg"),
        "mass": round(star.mass, 3) if star.mass else None,
        "crowdsap": meta.get("crowdsap"),
        "sectors": secs,
        "provenance": lc.provenance,
        "cadence_s": round(lc.cadence_s),
        "n_points": int(len(t)),
    }
    emitted = 0
    payload_saved = False

    for sig in reportable:
        known = []
        for k in known_bulk:
            rel = catalogs.period_match(sig.period, k.get("period"), 0.01)
            known.append({**k, "match": rel})
        for v in vsx:
            rel = catalogs.period_match(sig.period, v.get("Period"), 0.01)
            if rel or "E" in str(v.get("Type", "")):
                known.append({"kind": "vsx", "label": str(v.get("Name", "")).strip(), "type": str(v.get("Type", "")).strip(), "period": v.get("Period"), "match": rel})
        matched = [k for k in known if k.get("match")]
        if not include_known and matched:
            continue
        contaminants = []
        if sig.kind == "planet_candidate" and meta.get("ra") is not None:
            contaminants = _contaminants(meta["ra"], meta["dec"], meta.get("tmag"), sig.depth)
            if contaminants:
                sig.flags.append("NEARBY_CONTAMINANT")
                sig.score = max(0.0, sig.score - 0.15)
        kind = sig.kind
        if matched:
            kind = "known_" + ("planet" if any(k["kind"] in ("planet", "toi", "ctoi") for k in matched) else "eb")
        score = sig.score * (0.25 if matched else 1.0)
        rp = f" · {sig.rp_earth:.1f} R⊕" if sig.rp_earth else ""
        if not payload_saved:
            ctx.db.payload_put(pid, payload)
            payload_saved = True
        ctx.candidate(
            {
                **base,
                "engine": "transit",
                "kind": kind,
                "dedupe": f"transit:{tic}:{sig.kind}:{sig.period:.3f}",
                "title": f"{label} · P {sig.period:.4f} d",
                "subtitle": f"{sig.depth * 1e6:,.0f} ppm{rp} · SNR {sig.snr:.1f} · {sig.n_transits} transits",
                "score": round(score, 3),
                "known": known,
                "flags": sig.flags,
                "metrics": {**sig.to_dict(), "depth_ppm": round(sig.depth * 1e6, 1), "duration_h": round(sig.duration * 24, 3), "star": star_info, "contaminants": contaminants},
            }
        )
        emitted += 1
        outcome = kind

    if var:
        known = []
        for v in vsx:
            rel = catalogs.period_match(var.true_period, v.get("Period"), 0.02) or catalogs.period_match(var.period, v.get("Period"), 0.02)
            known.append({"kind": "vsx", "label": str(v.get("Name", "")).strip(), "type": str(v.get("Type", "")).strip(), "period": v.get("Period"), "match": rel or ("no-period" if not v.get("Period") else None)})
        for g in gaia_var:
            known.append({"kind": "gaia_var", "label": f"Gaia DR3 {g.get('Source')}", "type": g.get("Class"), "period": None, "match": "position"})
        matched = [k for k in known if k.get("match") and k.get("match") != "no-period"]
        if include_known or not matched:
            flags = []
            if any(k.get("match") == "no-period" for k in known):
                flags.append("VSX_ENTRY_LACKS_PERIOD")
            if star.crowdsap is not None and star.crowdsap < 0.8:
                flags.append("CROWDED_APERTURE")
            if not payload_saved:
                ctx.db.payload_put(pid, payload)
                payload_saved = True
            ctx.candidate(
                {
                    **base,
                    "engine": "variable",
                    "kind": "known_variable" if matched else "variable",
                    "dedupe": f"variable:{tic}:{var.true_period:.3f}",
                    "title": f"{label} · {var.type_guess} · {var.true_period:.4f} d",
                    "subtitle": f"{var.type_label} · amp {var.amplitude_ppm / 1e3:.1f} ppt",
                    "score": round(var.score * (0.25 if matched else 1.0), 3),
                    "known": known,
                    "flags": flags,
                    "metrics": {**var.to_dict(), "star": star_info},
                }
            )
            emitted += 1
            outcome = outcome if outcome != "quiet" else "variable"

    ctx.stage("XMATCH", "ok", label)
    ctx.mark(str(tic), outcome)


def _contaminants(ra: float, dec: float, tmag: float | None, depth: float) -> list[dict]:
    """Gaia neighbours within 2 TESS pixels bright enough to mimic the dip."""
    try:
        rows = net.tap(
            config.VIZIER_TAP,
            f'SELECT Source, RA_ICRS, DE_ICRS, Gmag FROM "{config.VZ_GAIA}" '
            f"WHERE {net.cone('RA_ICRS', 'DE_ICRS', ra, dec, 2 * config.TESS_PIXEL_ARCSEC / 3600)}",
            timeout=40,
        )
    except Exception:
        return []
    rows = [r for r in rows if r.get("Gmag") is not None]
    if not rows:
        return []
    rows.sort(key=lambda r: math.hypot((r["RA_ICRS"] - ra) * math.cos(math.radians(dec)), r["DE_ICRS"] - dec))
    target = rows[0]
    out = []
    for r in rows[1:]:
        sep = 3600 * math.hypot((r["RA_ICRS"] - ra) * math.cos(math.radians(dec)), r["DE_ICRS"] - dec)
        ratio = 10 ** (-0.4 * (r["Gmag"] - target["Gmag"]))
        needed = depth * (1 + 1 / ratio) if ratio > 0 else float("inf")
        if needed < 0.5:
            out.append({"gaia": str(r["Source"]), "sep_arcsec": round(sep, 1), "dG": round(r["Gmag"] - target["Gmag"], 2), "needed_depth": round(needed, 4)})
    return out[:8]


def _r(a, nd=6):
    return [round(float(x), nd) for x in a]


def _payload(lc, t, f, trend, fd, bls_pgram, ls_pgram, sectors) -> dict:
    tb, fb, _ = L.bin_lc(t, f, 30 / 1440.0)
    trb = np.interp(tb, t, trend)
    width = 10 / 1440.0 if len(sectors) <= 2 else 20 / 1440.0
    td, fdb, _ = L.bin_lc(t, fd, width)
    return {
        "tic": lc.tic,
        "sectors": sectors,
        "raw": {"t": _r(tb, 4), "f": _r(fb), "trend": _r(trb)},
        "detr": {"t": _r(td, 4), "f": _r(fdb)},
        "bls": bls_pgram,
        "ls": ls_pgram,
    }
