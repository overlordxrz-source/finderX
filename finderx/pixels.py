"""Pixel-level vetting: which star is actually dimming?

TESS pixels are 21″ wide, so a transit-like dip in a light curve can come
from any star within a pixel or two. This module pulls an 11×11-pixel
TESScut cutout from the full-frame images, builds a *difference image*
(average out-of-transit frames minus in-transit frames, with a local
baseline around each event), and fits a simple PSF at the position of
every Gaia star in the cutout. The star whose position best explains the
difference image is the source of the dip.
"""

from __future__ import annotations

import io
import math
import zipfile

import numpy as np

from . import config, net

TESSCUT = "https://mast.stsci.edu/tesscut/api/v0.1"
SIZE = 11
PSF_SIGMA = 0.75          # px; TESS PSF core is ~1.5–2 px FWHM
BTJD_YEAR0 = 2014.9363     # BTJD 0 = BJD 2457000 = 2014-12-08


def available_sectors(ra: float, dec: float) -> list[int]:
    r = net.get(f"{TESSCUT}/sector", params={"ra": ra, "dec": dec, "radius": "1m"}, timeout=60)
    return sorted({int(x["sector"]) for x in r.json().get("results", [])})


def cutout(ra: float, dec: float, sector: int, size: int = SIZE) -> dict:
    from astropy.io import fits

    r = net.get(f"{TESSCUT}/astrocut", params={"ra": ra, "dec": dec, "y": size, "x": size, "sector": sector}, timeout=300)
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        name = next(n for n in z.namelist() if n.endswith(".fits"))
        raw = z.read(name)
    with fits.open(io.BytesIO(raw), memmap=False) as h:
        d = h[1].data
        t = np.asarray(d["TIME"], float)
        flux = np.asarray(d["FLUX"], float)
        q = np.asarray(d["QUALITY"], int)
        wcs_header = h[2].header.copy()
    ok = np.isfinite(t) & (q == 0) & np.all(np.isfinite(flux.reshape(len(t), -1)), axis=1)
    t, flux = t[ok], flux[ok]
    # remove the frame-to-frame sky level (scattered light) before differencing
    sky = np.percentile(flux.reshape(len(t), -1), 30, axis=1)
    flux = flux - sky[:, None, None]
    return {"sector": sector, "time": t, "flux": flux, "wcs": wcs_header}


def _event_diffs(t, flux, P, t0, dur, phase_offset) -> list[np.ndarray]:
    out = []
    k0 = math.ceil((t[0] - t0) / P - phase_offset - 0.5)
    for k in range(k0, k0 + int((t[-1] - t[0]) / P) + 2):
        tk = t0 + (k + phase_offset) * P
        dt = np.abs(t - tk)
        inn = dt < 0.35 * dur
        oot = (dt > 0.75 * dur) & (dt < 2.25 * dur)
        if inn.sum() >= 3 and oot.sum() >= 6:
            out.append(np.median(flux[oot], 0) - np.median(flux[inn], 0))
    return out


def difference_image(cut: dict, P: float, t0: float, dur: float) -> dict | None:
    t, flux = cut["time"], cut["flux"]
    diffs = _event_diffs(t, flux, P, t0, dur, 0.0)
    if not diffs:
        return None
    # the same measurement at phases with no eclipse gives the noise per pixel
    nulls = [d for off in (0.25, 0.4, 0.6, 0.75) for d in _event_diffs(t, flux, P, t0, dur, off)]
    diff = np.mean(diffs, 0)
    if len(nulls) >= 4:
        sig = np.std(nulls, 0) / math.sqrt(len(diffs))
    else:
        sig = np.full_like(diff, np.std(diff))
    sig = np.maximum(sig, 1e-6)
    return {"diff": diff, "sig": sig, "snr": diff / sig, "direct": np.median(flux, 0), "n_events": len(diffs)}


def _psf(x: float, y: float, n: int = SIZE) -> np.ndarray:
    yy, xx = np.mgrid[0:n, 0:n]
    g = np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * PSF_SIGMA**2))
    s = g.sum()
    return g / s if s > 0 else g


def gaia_field(ra: float, dec: float, gmax: float, radius_arcsec: float = 170) -> list[dict]:
    q = (
        f'SELECT Source, RA_ICRS, DE_ICRS, Gmag, pmRA, pmDE FROM "{config.VZ_GAIA}" '
        f"WHERE {net.cone('RA_ICRS', 'DE_ICRS', ra, dec, radius_arcsec / 3600)} AND Gmag < {gmax:.2f}"
    )
    return net.tap(config.VIZIER_TAP, q, timeout=60)


def locate(cut: dict, img: dict, stars: list[dict], target_source: int | None, target_ra: float, target_dec: float, depth: float) -> dict:
    """Fit a PSF at every Gaia star; the best χ² marks the dimming star."""
    from astropy.wcs import WCS

    w = WCS(cut["wcs"])
    year = BTJD_YEAR0 + float(np.median(cut["time"])) / 365.25
    dt = year - 2016.0
    diff, sig = img["diff"], img["sig"]
    wts = 1.0 / sig**2
    tx, ty = (float(v) for v in w.world_to_pixel_values(target_ra, target_dec))

    target_g = None
    fits_ = []
    for s in stars:
        cosd = max(math.cos(math.radians(s["DE_ICRS"])), 1e-6)
        ra = s["RA_ICRS"] + (s.get("pmRA") or 0) * dt / 3.6e6 / cosd
        de = s["DE_ICRS"] + (s.get("pmDE") or 0) * dt / 3.6e6
        x, y = (float(v) for v in w.world_to_pixel_values(ra, de))
        if not (-1.5 < x < SIZE + 0.5 and -1.5 < y < SIZE + 0.5):
            continue
        g = _psf(x, y)
        a = float(np.sum(wts * g * diff) / np.sum(wts * g * g))
        chi2 = float(np.sum(wts * (diff - max(a, 0.0) * g) ** 2))
        is_target = (target_source is not None and int(s["Source"]) == int(target_source))
        if is_target:
            target_g = s["Gmag"]
        fits_.append({"gaia": str(s["Source"]), "G": round(s["Gmag"], 2), "x": round(x, 2), "y": round(y, 2),
                      "amp": round(a, 2), "chi2": round(chi2, 1), "is_target": is_target})
    if not fits_:
        return {"verdict": "inconclusive", "reason": "no Gaia stars in cutout"}
    if target_g is None:  # fall back to the star nearest the TIC position
        near = min(fits_, key=lambda f: (f["x"] - tx) ** 2 + (f["y"] - ty) ** 2)
        near["is_target"] = True
        target_g = near["G"]

    # how deep would each star's own eclipse have to be to produce the dip?
    for f in fits_:
        ratio = 10 ** (-0.4 * (f["G"] - target_g))
        f["needed_depth"] = round(depth * (1 + 1 / ratio) if not f["is_target"] else depth, 4)
        f["possible"] = f["needed_depth"] < 1.0 and f["amp"] > 0

    ranked = sorted((f for f in fits_ if f["possible"]), key=lambda f: f["chi2"]) or sorted(fits_, key=lambda f: f["chi2"])
    best = ranked[0]
    target = next(f for f in fits_ if f["is_target"])
    peak = _peak_snr(img["snr"], best["x"], best["y"])

    if peak < 4:
        verdict, reason = "inconclusive", f"dip too weak in the pixels (peak SNR {peak:.1f})"
    elif best["is_target"]:
        rival = next((f for f in ranked[1:] if math.hypot(f["x"] - best["x"], f["y"] - best["y"]) > 0.5), None)
        margin = (rival["chi2"] - best["chi2"]) if rival else 99.0
        verdict = "on_target" if margin >= 6 else "ambiguous"
        reason = "the difference image is centred on the target" if verdict == "on_target" else f"target and Gaia {rival['gaia']} fit almost equally (Δχ² {margin:.1f})"
    else:
        margin = target["chi2"] - best["chi2"]
        sep = math.hypot(best["x"] - target["x"], best["y"] - target["y"]) * config.TESS_PIXEL_ARCSEC
        verdict = "off_target" if margin >= 6 else "ambiguous"
        reason = (
            f"the dip sits on Gaia DR3 {best['gaia']} (G {best['G']}, {sep:.0f}″ away), which would need a {best['needed_depth'] * 100:.0f}% eclipse"
            if verdict == "off_target"
            else f"Gaia DR3 {best['gaia']} fits slightly better than the target (Δχ² {margin:.1f})"
        )
    fits_.sort(key=lambda f: f["chi2"])
    return {
        "verdict": verdict,
        "reason": reason,
        "best": best,
        "target": target,
        "peak_snr": round(peak, 1),
        "stars": fits_[:25],
        "target_px": [round(tx, 2), round(ty, 2)],
    }


def _peak_snr(snr: np.ndarray, x: float, y: float) -> float:
    xi, yi = int(round(x)), int(round(y))
    ys, xs = slice(max(0, yi - 1), yi + 2), slice(max(0, xi - 1), xi + 2)
    win = snr[ys, xs]
    return float(np.max(win)) if win.size else float(np.max(snr))


def check(ra: float, dec: float, period: float, t0: float, duration: float, depth: float,
          sectors: list[int] | None = None, max_sectors: int = 2, tmag: float | None = None,
          log=lambda m: None) -> dict:
    """Run the full pixel check over up to ``max_sectors`` sectors."""
    avail = available_sectors(ra, dec)
    use = [s for s in (sectors or []) if s in avail] or avail
    use = sorted(use, reverse=True)[:max_sectors]
    if not use:
        return {"verdict": "inconclusive", "reason": "no TESS full-frame cutouts cover this position", "sectors": []}
    log(f"TESScut sectors available: {', '.join(map(str, avail))} — using {', '.join(map(str, use))}")

    stars_all = gaia_field(ra, dec, gmax=max(17.0, min(20.5, (tmag or 13) + 7)))
    target = min(stars_all, key=lambda s: (s["RA_ICRS"] - ra) ** 2 * math.cos(math.radians(dec)) ** 2 + (s["DE_ICRS"] - dec) ** 2) if stars_all else None
    log(f"{len(stars_all)} Gaia stars within 170″")

    per = []
    for sec in use:
        log(f"S{sec}: downloading 11×11-pixel cutout …")
        try:
            cut = cutout(ra, dec, sec)
        except Exception as exc:
            log(f"S{sec}: cutout failed ({exc})")
            continue
        img = difference_image(cut, period, t0, duration)
        if img is None:
            log(f"S{sec}: no complete events in this sector")
            continue
        loc = locate(cut, img, stars_all, target["Source"] if target else None, ra, dec, depth)
        log(f"S{sec}: {img['n_events']} events · {loc['verdict'].replace('_', ' ')} — {loc['reason']}")
        direct = img["direct"]
        norm = float(np.percentile(direct, 99.5)) or 1.0
        per.append({
            "sector": sec,
            "n_events": img["n_events"],
            "direct": np.round(direct / norm, 3).tolist(),
            "diff": np.round(img["diff"], 3).tolist(),
            "snr": np.round(img["snr"], 2).tolist(),
            **loc,
        })
    if not per:
        return {"verdict": "inconclusive", "reason": "no usable sector", "sectors": []}

    conclusive = [p for p in per if p["verdict"] in ("on_target", "off_target")]
    lead = max(conclusive or per, key=lambda p: p.get("peak_snr", 0))
    votes = {v: sum(1 for p in conclusive if p["verdict"] == v) for v in ("on_target", "off_target")}
    if conclusive and votes["on_target"] and votes["off_target"]:
        verdict, reason = "ambiguous", "sectors disagree about the source"
    else:
        verdict, reason = lead["verdict"], lead["reason"]
    return {"verdict": verdict, "reason": reason, "lead_sector": lead["sector"], "sectors": per}
