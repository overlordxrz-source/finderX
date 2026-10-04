"""SOLAR engine — what is moving through the Solar System right now.

  mode=field       SkyBoT: every known asteroid/comet in a cone at an epoch,
                   with on-sky motion vectors
  mode=neocp       MPC NEO Confirmation Page: freshly discovered objects that
                   still need follow-up; diffed against the previous check
  mode=approaches  JPL close approaches within 0.05 au over the next 60 days
  mode=watch       neocp + approaches (default)

Finding a brand-new asteroid needs your own images, so this engine reports
live objects rather than discovery candidates.
"""

from __future__ import annotations

import time

from .. import config, net
from ..jobs import JobContext

STAGES = ["SKYBOT", "NEOCP", "CAD"]


def run(ctx: JobContext) -> dict:
    p = ctx.params
    mode = p.get("mode") or ("field" if p.get("ra") is not None else "watch")
    ctx.emit("pipeline", stages=STAGES)
    out: dict = {"mode": mode}
    if mode == "field":
        out["field"] = _field(ctx, float(p["ra"]), float(p["dec"]), float(p.get("radius", 1.0)), p.get("epoch"))
    if mode in ("neocp", "watch"):
        out["neocp"] = _neocp(ctx)
    if mode in ("approaches", "watch"):
        out["approaches"] = _approaches(ctx, float(p.get("days", 60)))
    return out


def _hms(s: str) -> float:
    h, m, sec = (float(x) for x in s.split())
    return 15.0 * (h + m / 60 + sec / 3600)


def _dms(s: str) -> float:
    parts = s.split()
    sign = -1.0 if parts[0].startswith("-") else 1.0
    d, m, sec = (abs(float(x)) for x in parts)
    return sign * (d + m / 60 + sec / 3600)


def _field(ctx: JobContext, ra: float, dec: float, radius: float, epoch: str | None) -> int:
    ctx.stage("SKYBOT", "run")
    ctx.emit("target", ra=ra, dec=dec, radius=radius, label="SkyBoT cone")
    ep = epoch or "now"
    r = net.get(
        config.SKYBOT,
        params={"-ep": ep, "-ra": ra, "-dec": dec, "-rd": min(radius, 10), "-mime": "json", "-output": "basic", "-loc": "500", "-from": "finderX"},
        timeout=120,
    )
    try:
        data = r.json()
    except ValueError:
        data = []
    if isinstance(data, dict):  # error envelope
        ctx.log(f"SkyBoT: {str(data.get('message', ''))[:120]}", level="warn", src="IMCCE")
        ctx.stage("SKYBOT", "fail")
        return 0
    classes: dict[str, int] = {}
    for o in data:
        try:
            ora, odec = _hms(o["RA (hms)"]), _dms(o["DEC (dms)"])
        except (KeyError, ValueError):
            continue
        cls = o.get("Class", "?")
        classes[cls.split(">")[0]] = classes.get(cls.split(">")[0], 0) + 1
        ctx.result({
            "kind": "sso",
            "name": o.get("Name"),
            "class": cls,
            "ra": ora,
            "dec": odec,
            "vmag": o.get("VMag (mag)"),
            "err_arcsec": o.get("Err (arcsec)"),
            "dra_arcsec_h": o.get("dRA (arcsec/h)"),
            "ddec_arcsec_h": o.get("dDEC (arcsec/h)"),
            "dist_au": o.get("dg (ua)"),
            "helio_au": o.get("dh (ua)"),
        })
    ctx.log(
        f"SkyBoT: {len(data)} known bodies in field — " + ", ".join(f"{k} {v}" for k, v in sorted(classes.items(), key=lambda kv: -kv[1])),
        src="IMCCE",
    )
    ctx.stage("SKYBOT", "ok")
    return len(data)


def parse_neocp(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        tok = line.split()
        if len(tok) < 12:
            continue
        try:
            desig, score = tok[0], int(tok[1])
            year, month, day = int(tok[2]), int(tok[3]), float(tok[4])
            ra_h, dec, vmag = float(tok[5]), float(tok[6]), float(tok[7])
            nobs, arc, h, notseen = int(tok[-4]), float(tok[-3]), float(tok[-2]), float(tok[-1])
        except ValueError:
            continue
        note = " ".join(tok[8:-4])
        out.append({
            "kind": "neocp",
            "name": desig,
            "score": score,
            "discovered": f"{year:04d}-{month:02d}-{day:04.1f}",
            "ra": ra_h * 15.0,
            "dec": dec,
            "vmag": vmag,
            "note": note,
            "nobs": nobs,
            "arc_days": arc,
            "H": h,
            "not_seen_days": notseen,
        })
    return out


def _neocp(ctx: JobContext) -> int:
    ctx.stage("NEOCP", "run")
    objs = parse_neocp(net.get(config.NEOCP_TXT, timeout=40).text)
    prev = ctx.db.meta_get("neocp_last", {"at": 0, "names": []})
    prev_names = set(prev["names"])
    names = {o["name"] for o in objs}
    gone = sorted(prev_names - names)
    for o in sorted(objs, key=lambda o: -o["score"]):
        o["new_since_last_check"] = bool(prev_names) and o["name"] not in prev_names
        ctx.result(o)
    likely_neo = sum(1 for o in objs if o["score"] >= 80)
    ctx.log(f"NEOCP: {len(objs)} unconfirmed objects awaiting follow-up · {likely_neo} with NEO score ≥ 80", src="MPC")
    if prev["at"]:
        mins = (time.time() - prev["at"]) / 60
        new = len(names - prev_names)
        ctx.log(f"since last check ({mins:.0f} min ago): {new} new · {len(gone)} left the page (designated or retired)", src="MPC")
        for g in gone[:12]:
            ctx.result({"kind": "neocp_gone", "name": g})
    ctx.db.meta_set("neocp_last", {"at": time.time(), "names": sorted(names)})
    ctx.stage("NEOCP", "ok")
    return len(objs)


def _approaches(ctx: JobContext, days: float) -> int:
    ctx.stage("CAD", "run")
    r = net.get(
        config.JPL_CAD,
        params={"date-min": "now", "date-max": f"+{int(days)}", "dist-max": "0.05", "sort": "dist", "fullname": "true"},
        timeout=40,
    )
    d = r.json()
    fields = d.get("fields", [])
    n = 0
    for row in d.get("data", []) or []:
        rec = dict(zip(fields, row))
        dist_au = float(rec["dist"])
        ctx.result({
            "kind": "approach",
            "name": (rec.get("fullname") or rec.get("des") or "").strip(),
            "date": rec.get("cd"),
            "dist_au": dist_au,
            "dist_ld": round(dist_au * 389.17, 2),
            "v_rel_kms": float(rec["v_rel"]),
            "H": float(rec["h"]) if rec.get("h") else None,
            "diameter_m": _diameter_m(float(rec["h"])) if rec.get("h") else None,
        })
        n += 1
    ctx.log(f"JPL: {n} close approaches inside 0.05 au over the next {int(days)} days", src="JPL")
    ctx.stage("CAD", "ok")
    return n


def _diameter_m(h: float, albedo: float = 0.14) -> int:
    return int(1329e3 / albedo**0.5 * 10 ** (-h / 5))

