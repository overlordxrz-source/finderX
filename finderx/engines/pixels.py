"""PIXELS engine — locate the source of a candidate's dip in TESS pixels."""

from __future__ import annotations

from .. import pixels as px
from ..jobs import JobContext

STAGES = ["TESSCUT", "DIFF-IMAGE", "PSF-FIT", "ARCHIVAL", "VERDICT"]

_FLAG = {"on_target": "PIXELS_ON_TARGET", "off_target": "PIXELS_OFF_TARGET", "ambiguous": "PIXELS_AMBIGUOUS", "inconclusive": "PIXELS_INCONCLUSIVE"}


def run(ctx: JobContext) -> dict:
    cid = ctx.params["cid"]
    c = ctx.db.candidate(cid)
    if not c:
        raise ValueError(f"no candidate {cid}")
    if c["engine"] not in ("transit", "variable"):
        raise ValueError("pixel checks need a TESS light-curve candidate")
    ctx.emit("pipeline", stages=STAGES)
    result = check_candidate(ctx, c)
    apply(ctx, c, result)
    return {"verdict": result["verdict"]}


def check_candidate(ctx: JobContext, c: dict) -> dict:
    m = c["metrics"]
    star = m.get("star") or {}
    if c["engine"] == "variable":
        raise ValueError("pixel checks for sinusoidal variables are not supported yet — use an eclipse candidate")
    period, t0, duration, depth = m["period"], m["t0"], m["duration"], m["depth"]
    ctx.emit("target", ra=c["ra"], dec=c["dec"], label=c["target"])
    ctx.stage("TESSCUT", "run", c["target"])

    def log(msg: str) -> None:
        ctx.log(f"{c['id']} {msg}", src="PIXELS")
        if "downloading" in msg:
            ctx.stage("TESSCUT", "run", c["target"])
        elif "events ·" in msg:
            ctx.stage("PSF-FIT", "ok", c["target"])

    result = px.check(c["ra"], c["dec"], period, t0, duration, depth, sectors=star.get("sectors"), tmag=star.get("tmag"), log=log)
    ctx.stage("ARCHIVAL", "run", c["target"])
    result["archival"] = archival_check(c, log, ctx.db.payload_get(c["payload"]) if c.get("payload") else None)
    if result["verdict"] == "off_target":
        result["source"] = source_check(result, period, log)
    ctx.stage("VERDICT", "ok", c["target"])
    return result


_SOURCE_SAYS = {
    "known": "already catalogued with this period",
    "period_differs": "catalogued, but with a period TESS contradicts",
    "no_period": "catalogued as variable, no period published",
    "uncatalogued": "not in VSX or the Gaia DR3 variability catalogue",
}


def source_check(result: dict, period: float, log) -> dict | None:
    """Look up the star the pixels blamed in VSX and Gaia DR3 variability."""
    from .. import catalogs

    lead = next((s for s in result.get("sectors", []) if s["sector"] == result.get("lead_sector")), None)
    best = (lead or {}).get("best")
    if not best:
        return None
    try:
        src = catalogs.identify_source(best["gaia"], period)
    except Exception as exc:
        log(f"catalogue lookup of Gaia DR3 {best['gaia']} failed ({exc})")
        return None
    src.update(G=best["G"], needed_depth=best.get("needed_depth"))
    named = ", ".join(f"{e['label']} {e['type']} P={e['period']}" for e in src["entries"]) or "nothing"
    log(f"source Gaia DR3 {best['gaia']}: {_SOURCE_SAYS[src['status']]} ({named})")
    return src


def archival_check(c: dict, log, payload: dict | None = None) -> dict:
    """Pan-STARRS1 / Gaia epoch photometry folded on the TESS ephemeris."""
    import math

    import numpy as np

    from .. import archival as ar

    m = c["metrics"]
    # the orbital period, so primary and secondary eclipses are each modelled
    P = m.get("orbital_period") or m["period"]
    tt = m.get("transit_times") or [m["t0"]]
    k = round((sum(tt) / len(tt) - m["t0"]) / P)
    t_ref = m["t0"] + k * P
    n_orb = max(1, round((m.get("n_transits") or 2) * m["period"] / P))
    sigma_t0, sigma_p = ar.ephemeris_sigma(P, m["duration"], m.get("snr") or 10, n_orb, (max(tt) - min(tt)) if len(tt) > 1 else 27.0)
    template = None
    if payload and payload.get("detr"):
        t = np.asarray(payload["detr"]["t"], float)
        f = np.asarray(payload["detr"]["f"], float)
        ok = np.isfinite(t) & np.isfinite(f)
        if ok.sum() > 200:
            template = ar.eclipse_template(t[ok], f[ok], P, t_ref, m["duration"])
    try:
        stars = px.gaia_field(c["ra"], c["dec"], gmax=max(17.0, min(20.5, ((m.get("star") or {}).get("tmag") or 13) + 7)), radius_arcsec=70)
    except Exception as exc:
        return {"verdict": "no_data", "reason": f"Gaia lookup failed ({exc})", "stars": []}
    target = min(stars, key=lambda s: (s["RA_ICRS"] - c["ra"]) ** 2 * math.cos(math.radians(c["dec"])) ** 2 + (s["DE_ICRS"] - c["dec"]) ** 2) if stars else None
    res = ar.check(c["ra"], c["dec"], P, t_ref, m["duration"], m["depth"], stars, str(target["Source"]) if target else None, sigma_t0, sigma_p, log=log, template=template)
    log(f"archival photometry: {res['verdict'].replace('_', ' ')} — {res['reason']}")
    return res


_SOURCE_FLAG = {"known": "SOURCE_CATALOGUED", "period_differs": "SOURCE_PERIOD_DIFFERS", "no_period": "SOURCE_LACKS_PERIOD", "uncatalogued": "SOURCE_UNCATALOGUED"}


def apply(ctx: JobContext, c: dict, result: dict) -> None:
    flags = [f for f in (c.get("flags") or []) if not f.startswith(("PIXELS_", "ARCHIVE_", "SOURCE_"))]
    flags.append(_FLAG[result["verdict"]])
    m = c["metrics"]
    # always start from the light-curve verdict so re-runs do not compound
    kind = m.get("pre_pixel_kind") or c["kind"]
    score = float(m.get("pre_pixel_score", c.get("score") or 0))
    known = [k for k in (c.get("known") or []) if not k.get("source")]
    if result["verdict"] == "on_target":
        score = min(1.0, score + 0.15)
    elif result["verdict"] == "off_target":
        if kind == "planet_candidate":
            kind, score = "blend", score * 0.3
        else:
            score *= 0.8          # still a binary, just a fainter star than the TIC target
        src = result.get("source")
        if src:
            flags.append(_SOURCE_FLAG[src["status"]])
            known += [{**e, "source": True} for e in src["entries"]]
            if src["status"] == "known":
                kind = "known_eb" if src["is_eb"] or kind in ("eclipsing_binary", "blend", "known_eb") else "known_variable"
                score *= 0.25
    arc = result.get("archival") or {}
    if arc.get("verdict") in ("caught_on_target", "caught_on_neighbour", "target_ruled_out"):
        flags.append({"caught_on_target": "ARCHIVE_CAUGHT_TARGET", "caught_on_neighbour": "ARCHIVE_CAUGHT_NEIGHBOUR", "target_ruled_out": "ARCHIVE_TARGET_EXCLUDED"}[arc["verdict"]])
    metrics = {**m, "pixels": result, "pre_pixel_kind": m.get("pre_pixel_kind") or c["kind"], "pre_pixel_score": m.get("pre_pixel_score", c.get("score") or 0)}
    ctx.db.candidate_update(c["id"], kind=kind, flags=flags, score=round(score, 3), metrics=metrics, known=known)
    ctx.log(f"{c['id']} pixel verdict: {result['verdict'].replace('_', ' ').upper()} — {result['reason']}", src="PIXELS")
    if result.get("source"):
        ctx.log(f"{c['id']} source star Gaia DR3 {result['source']['gaia']}: {_SOURCE_SAYS[result['source']['status']]}", src="PIXELS")
    ctx.emit("vote", id=c["id"], status="pixels")
