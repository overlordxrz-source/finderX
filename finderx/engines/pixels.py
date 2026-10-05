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
    result["archival"] = archival_check(c, log)
    ctx.stage("VERDICT", "ok", c["target"])
    return result


def archival_check(c: dict, log) -> dict:
    """Pan-STARRS1 / Gaia epoch photometry folded on the TESS ephemeris."""
    import math

    from .. import archival as ar

    m = c["metrics"]
    tt = m.get("transit_times") or [m["t0"]]
    k = round((sum(tt) / len(tt) - m["t0"]) / m["period"])
    t_ref = m["t0"] + k * m["period"]
    sigma_t0, sigma_p = ar.ephemeris_sigma(m["period"], m["duration"], m.get("snr") or 10, m.get("n_transits") or 2, (max(tt) - min(tt)) if len(tt) > 1 else 27.0)
    try:
        stars = px.gaia_field(c["ra"], c["dec"], gmax=max(17.0, min(20.5, ((m.get("star") or {}).get("tmag") or 13) + 7)), radius_arcsec=70)
    except Exception as exc:
        return {"verdict": "no_data", "reason": f"Gaia lookup failed ({exc})", "stars": []}
    target = min(stars, key=lambda s: (s["RA_ICRS"] - c["ra"]) ** 2 * math.cos(math.radians(c["dec"])) ** 2 + (s["DE_ICRS"] - c["dec"]) ** 2) if stars else None
    res = ar.check(c["ra"], c["dec"], m["period"], t_ref, m["duration"], m["depth"], stars, str(target["Source"]) if target else None, sigma_t0, sigma_p, log=log)
    log(f"archival photometry: {res['verdict'].replace('_', ' ')} — {res['reason']}")
    return res


def apply(ctx: JobContext, c: dict, result: dict) -> None:
    flags = [f for f in (c.get("flags") or []) if not f.startswith(("PIXELS_", "ARCHIVE_"))]
    flags.append(_FLAG[result["verdict"]])
    kind, score = c["kind"], float(c.get("score") or 0)
    if result["verdict"] == "on_target":
        score = min(1.0, score + 0.15)
    elif result["verdict"] == "off_target":
        score *= 0.3
        if kind == "planet_candidate":
            kind = "blend"
    metrics = {**c["metrics"], "pixels": result}
    ctx.db.candidate_update(c["id"], kind=kind, flags=flags, score=round(score, 3), metrics=metrics)
    arc = result.get("archival") or {}
    if arc.get("verdict") in ("caught_on_target", "caught_on_neighbour", "target_ruled_out"):
        flags.append({"caught_on_target": "ARCHIVE_CAUGHT_TARGET", "caught_on_neighbour": "ARCHIVE_CAUGHT_NEIGHBOUR", "target_ruled_out": "ARCHIVE_TARGET_EXCLUDED"}[arc["verdict"]])
        metrics["pixels"] = result
        ctx.db.candidate_update(c["id"], flags=flags, metrics=metrics)
    ctx.log(f"{c['id']} pixel verdict: {result['verdict'].replace('_', ' ').upper()} — {result['reason']}", src="PIXELS")
    ctx.emit("vote", id=c["id"], status="pixels")
