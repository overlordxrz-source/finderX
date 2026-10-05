"""Night planner: when can *you* watch a candidate dim?

Given an observing site, predicts every transit/eclipse of the queued
candidates over the next nights and grades it by darkness, altitude and
the Moon. For blends found by the pixel check it points at the star that
actually varies. Also ranks NEO Confirmation Page objects visible tonight.
"""

from __future__ import annotations

import math
import warnings
from datetime import datetime, timezone

import numpy as np

SITES = {
    "lco-ctio": ("LCO · Cerro Tololo, Chile", -30.1673, -70.8048, 2198),
    "lco-saao": ("LCO · Sutherland, South Africa", -32.3806, 20.8101, 1460),
    "lco-ssp": ("LCO · Siding Spring, Australia", -31.2733, 149.0711, 1116),
    "lco-mcd": ("LCO · McDonald, Texas", 30.6800, -104.0151, 2070),
    "lco-tfn": ("LCO · Teide, Tenerife", 28.3004, -16.5117, 2390),
    "lco-ogg": ("LCO · Haleakalā, Hawaii", 20.7069, -156.2575, 3055),
    "itel-chile": ("iTelescope · Deep Sky Chile", -30.5262, -70.8531, 1710),
    "itel-ssp": ("iTelescope · Siding Spring", -31.2733, 149.0644, 1122),
    "itel-utah": ("iTelescope · Utah Desert", 37.7004, -113.7141, 1580),
}

MIN_ALT = 25.0          # deg, target altitude for a usable light curve
DARK_SUN = -12.0        # deg, nautical twilight
BASELINE_H = 0.5        # out-of-eclipse baseline wanted on each side


def _astropy():
    import astropy.units as u
    from astropy.coordinates import AltAz, EarthLocation, SkyCoord, get_body, get_sun
    from astropy.time import Time
    from astropy.utils import iers

    iers.conf.auto_download = False          # bundled tables are plenty for planning
    iers.conf.auto_max_age = None
    return u, AltAz, EarthLocation, SkyCoord, get_body, get_sun, Time


def site(observer: dict) -> tuple:
    u, _, EarthLocation, *_ = _astropy()
    return EarthLocation(lat=observer["lat"] * u.deg, lon=observer["lon"] * u.deg, height=(observer.get("elev") or 0) * u.m)


def _alt(loc, coord, jd: np.ndarray) -> np.ndarray:
    u, AltAz, _, _, _, _, Time = _astropy()
    t = Time(jd, format="jd", scale="utc")
    return coord.transform_to(AltAz(obstime=t, location=loc)).alt.deg


def _sun_alt(loc, jd: np.ndarray) -> np.ndarray:
    u, AltAz, _, _, _, get_sun, Time = _astropy()
    t = Time(jd, format="jd", scale="utc")
    return get_sun(t).transform_to(AltAz(obstime=t, location=loc)).alt.deg


def _moon(loc, coord, jd: float) -> tuple[float, float]:
    """(separation in deg, illuminated fraction)."""
    u, AltAz, _, SkyCoord, get_body, get_sun, Time = _astropy()
    t = Time(jd, format="jd", scale="utc")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # GCRS↔ICRS separation nuance is irrelevant at degree level
        moon = get_body("moon", t, loc)
        sun = get_sun(t)
        sep = float(moon.separation(coord).deg)
        elong = float(moon.separation(sun).rad)
    illum = (1 - math.cos(elong)) / 2
    return sep, illum


def predict(cands: list[dict], observer: dict, days: float = 14.0, now: datetime | None = None) -> list[dict]:
    """Observable events for candidates that carry a transit ephemeris."""
    from . import archival as ar

    u, AltAz, _, SkyCoord, *_ , Time = _astropy()
    loc = site(observer)
    now = now or datetime.now(timezone.utc)
    jd0 = Time(now).jd
    jd1 = jd0 + days
    out = []
    for c in cands:
        m = c.get("metrics") or {}
        if c.get("engine") != "transit" or not m.get("period") or m.get("t0") is None:
            continue
        P = m["period"] * (2 if "ODD_EVEN_MISMATCH" in (c.get("flags") or []) and c["kind"] != "planet_candidate" else 1)
        dur = m.get("duration") or 0.1
        tt = m.get("transit_times") or [m["t0"]]
        k = round((sum(tt) / len(tt) - m["t0"]) / m["period"])
        t_ref = m["t0"] + k * m["period"] + 2457000.0            # BJD ≈ JD for planning
        s_t0, s_p = ar.ephemeris_sigma(m["period"], dur, m.get("snr") or 10, m.get("n_transits") or 2, (max(tt) - min(tt)) if len(tt) > 1 else 27.0)

        # which star to point at: the pixel check may have found the real source
        ra, dec, star_label, best = c["ra"], c["dec"], c["target"], None
        pix = m.get("pixels") or {}
        if pix.get("verdict") == "off_target":
            lead = next((s for s in pix.get("sectors", []) if s["sector"] == pix.get("lead_sector")), None)
            best = (lead or {}).get("best")
            if best:
                g = _gaia_position(best["gaia"])
                if g:
                    ra, dec = g
                    star_label = f"Gaia DR3 {best['gaia']} (G {best['G']})"
        coord = SkyCoord(ra * u.deg, dec * u.deg)

        n0 = math.ceil((jd0 - t_ref) / P)
        n1 = math.floor((jd1 - t_ref) / P)
        for n in range(n0, n1 + 1):
            mid = t_ref + n * P
            sig = math.hypot(s_t0, abs(n) * s_p * (P / m["period"]))
            if sig > dur:              # prediction too uncertain to plan around
                continue
            half = dur / 2 + BASELINE_H / 24
            grid = np.linspace(mid - half - sig, mid + half + sig, 9)
            alts = _alt(loc, coord, grid)
            suns = _sun_alt(loc, grid)
            core = slice(2, 7)
            if (suns[core] > DARK_SUN).any() or (alts[core] < MIN_ALT).all():
                continue
            full = bool((suns < DARK_SUN).all() and (alts > MIN_ALT).all())
            moon_sep, illum = _moon(loc, coord, mid)
            depth = m.get("depth") or 0
            if pix.get("verdict") == "off_target" and best:
                depth = best.get("needed_depth") or depth
            out.append({
                "id": c["id"], "title": c["title"], "kind": c["kind"], "status": c.get("status"),
                "star": star_label, "ra": round(ra, 6), "dec": round(dec, 6),
                "mid_utc": Time(mid, format="jd").to_datetime(timezone.utc).strftime("%Y-%m-%d %H:%M"),
                "mid_jd": round(mid, 5),
                "start_utc": Time(mid - dur / 2, format="jd").to_datetime(timezone.utc).strftime("%H:%M"),
                "end_utc": Time(mid + dur / 2, format="jd").to_datetime(timezone.utc).strftime("%H:%M"),
                "duration_h": round(dur * 24, 2),
                "uncertainty_min": round(sig * 1440),
                "depth_pct": round(depth * 100, 2),
                "alt": [round(float(a)) for a in alts],
                "sun": [round(float(s)) for s in suns],
                "moon_sep": round(moon_sep), "moon_illum": round(illum, 2),
                "quality": "full" if full else "partial",
            })
    out.sort(key=lambda e: e["mid_jd"])
    return out


_gaia_cache: dict[str, tuple[float, float] | None] = {}


def _gaia_position(source: str) -> tuple[float, float] | None:
    from . import config, net

    if source not in _gaia_cache:
        try:
            r = net.tap(config.VIZIER_TAP, f'SELECT RA_ICRS, DE_ICRS FROM "{config.VZ_GAIA}" WHERE Source = {int(source)}', timeout=30)
            _gaia_cache[source] = (r[0]["RA_ICRS"], r[0]["DE_ICRS"]) if r else None
        except Exception:
            _gaia_cache[source] = None
    return _gaia_cache[source]


def tonight_window(observer: dict, now: datetime | None = None) -> tuple[float, float] | None:
    """JD span of astronomical darkness for the coming night."""
    _, _, _, _, _, _, Time = _astropy()
    loc = site(observer)
    now = now or datetime.now(timezone.utc)
    jd = Time(now).jd + np.linspace(0, 1.0, 289)
    sun = _sun_alt(loc, jd)
    dark = sun < DARK_SUN
    if not dark.any():
        return None
    i0 = int(np.argmax(dark))
    i1 = i0 + int(np.argmax(~dark[i0:])) if (~dark[i0:]).any() else len(jd) - 1
    return float(jd[i0]), float(jd[max(i1 - 1, i0)])


def neocp_tonight(objs: list[dict], observer: dict, vmax: float = 20.5) -> list[dict]:
    """NEOCP objects that rise above 30° during tonight's darkness."""
    u, _, _, SkyCoord, *_ , Time = _astropy()
    win = tonight_window(observer)
    if not win:
        return []
    loc = site(observer)
    jd = np.linspace(win[0], win[1], 13)
    out = []
    for o in objs:
        if o.get("vmag") is None or o["vmag"] > vmax:
            continue
        alt = _alt(loc, SkyCoord(o["ra"] * u.deg, o["dec"] * u.deg), jd)
        k = int(np.argmax(alt))
        if alt[k] < 30:
            continue
        out.append({**o, "best_alt": round(float(alt[k])), "best_utc": Time(jd[k], format="jd").to_datetime(timezone.utc).strftime("%H:%M"),
                    "hours_up": round(float((alt > 30).sum() * (jd[1] - jd[0]) * 24), 1)})
    out.sort(key=lambda o: (-o["score"], o["vmag"]))
    return out


def darkness_label(win: tuple[float, float] | None) -> str:
    if not win:
        return "no astronomical darkness tonight"
    _, _, _, _, _, _, Time = _astropy()
    a = Time(win[0], format="jd").to_datetime(timezone.utc)
    b = Time(win[1], format="jd").to_datetime(timezone.utc)
    return f"dark {a:%H:%M}–{b:%H:%M} UTC ({(b - a).total_seconds() / 3600:.1f} h)"



DEFAULT_OBSERVER = {"name": SITES["lco-ctio"][0], "lat": SITES["lco-ctio"][1], "lon": SITES["lco-ctio"][2], "elev": SITES["lco-ctio"][3], "key": "lco-ctio"}


def observer_from(body: dict) -> dict:
    key = body.get("key")
    if key in SITES:
        name, lat, lon, elev = SITES[key]
        return {"key": key, "name": name, "lat": lat, "lon": lon, "elev": elev}
    lat, lon = float(body["lat"]), float(body["lon"])
    if not (-90 <= lat <= 90 and -180 <= lon <= 360):
        raise ValueError("latitude/longitude out of range")
    return {"key": "custom", "name": body.get("name") or f"{lat:.2f}, {lon:.2f}", "lat": lat, "lon": lon, "elev": float(body.get("elev") or 0)}


def plan(db, observer: dict, days: float = 14.0, with_neocp: bool = True) -> dict:
    from . import config, net
    from .engines.solar import parse_neocp

    cands = [c for c in db.candidates(engine="transit", status="new,flagged,confirmed", limit=1000)
             if not c["kind"].startswith("known_") and c["kind"] != "systematic"]
    out = {"observer": observer, "darkness": darkness_label(tonight_window(observer)), "events": predict(cands, observer, days)}
    if with_neocp:
        try:
            out["neocp"] = neocp_tonight(parse_neocp(net.get(config.NEOCP_TXT, timeout=30).text), observer)
        except Exception as exc:
            out["neocp"], out["neocp_error"] = [], str(exc)
    return out
