"""Signal search and vetting on conditioned light curves.

Pure numpy/astropy — no network — so it is unit-testable with synthetic
data. ``transit_search`` runs an iterative Box Least Squares search and a
battery of standard false-positive tests; ``variability_search`` runs a
Lomb-Scargle periodogram and a light-touch variable-type heuristic.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np

from . import config
from .lightcurve import bin_lc, fold, robust_std, segments

R_EARTH_PER_R_SUN = 109.1
# TESS orbits last ~12.5–14.5 d; momentum dumps and orbit-boundary ramps
# alias to these periods and their halves.
TESS_ORBIT_ALIASES = ((12.4, 14.6),)


@dataclass
class Star:
    radius: float | None = None   # R_sun
    mass: float | None = None     # M_sun
    teff: float | None = None
    logg: float | None = None
    tmag: float | None = None
    crowdsap: float | None = None

    @classmethod
    def from_meta(cls, meta: dict) -> "Star":
        r, logg = meta.get("radius"), meta.get("logg")
        if r is not None and not (0.05 < r < 500):
            r = None  # TIC placeholders / bad fits
        m = None
        if r and logg:
            m = 10 ** (logg - 4.438) * r * r
            if not (0.05 < m < 20):
                m = None
        return cls(radius=r, mass=m, teff=meta.get("teff"), logg=logg, tmag=meta.get("tmag"), crowdsap=meta.get("crowdsap"))

    @property
    def density(self) -> float | None:
        if self.radius and self.mass:
            return self.mass / self.radius**3
        return None


@dataclass
class TransitSignal:
    index: int
    period: float
    t0: float
    duration: float              # days
    depth: float                 # fractional
    depth_err: float
    snr: float
    sde: float
    n_transits: int
    odd_even_sigma: float
    secondary_snr: float
    secondary_depth: float
    harmonic_dll: float
    single_event_frac: float
    shape_ratio: float | None
    edge_frac: float
    centroid_sigma: float | None
    centroid_shift_px: float | None
    rp_earth: float | None
    duration_ratio: float | None
    flags: list[str] = field(default_factory=list)
    kind: str = "planet_candidate"
    score: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: (round(v, 7) if isinstance(v, float) else v) for k, v in d.items()}


@dataclass
class VariableSignal:
    period: float
    power: float
    fap: float
    amplitude_ppm: float          # semi-amplitude of the best sinusoid
    ptp_ppm: float                # peak-to-peak of the phase-binned curve
    noise_ppm: float
    skew: float
    type_guess: str
    type_label: str
    true_period: float            # after EW / EA doubling heuristic
    score: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: (round(v, 7) if isinstance(v, float) else v) for k, v in d.items()}


# ── BLS ────────────────────────────────────────────────────────────────────

def _frequency_grid(baseline: float, pmin: float, pmax: float, qmin_d: float, max_n: int) -> np.ndarray:
    fmin, fmax = 1.0 / pmax, 1.0 / pmin
    df = qmin_d / (baseline**2) / 2.0  # 2x oversampled in phase drift
    n = int((fmax - fmin) / df) + 1
    if n > max_n:
        n = max_n
    return np.linspace(fmin, fmax, n)


def _running_median(y: np.ndarray, width: int) -> np.ndarray:
    from scipy.ndimage import median_filter

    if len(y) <= width:
        return np.full_like(y, np.median(y))
    step = max(1, len(y) // 20000)
    ys = median_filter(y[::step], size=max(3, width // step), mode="nearest")
    return np.interp(np.arange(len(y)), np.arange(0, len(y), step)[: len(ys)], ys)


def _downsample_max(x: np.ndarray, y: np.ndarray, n: int) -> tuple[list, list]:
    if len(x) <= n:
        return x.tolist(), y.tolist()
    edges = np.linspace(0, len(x), n + 1).astype(int)
    xs, ys = [], []
    for a, b in zip(edges[:-1], edges[1:]):
        if b <= a:
            continue
        k = a + int(np.argmax(y[a:b]))
        xs.append(float(x[k]))
        ys.append(float(y[k]))
    return xs, ys


def transit_search(
    t: np.ndarray,
    f: np.ndarray,
    star: Star | None = None,
    centroids: tuple[np.ndarray, np.ndarray] | None = None,
    cfg: dict | None = None,
    log=lambda m: None,
) -> tuple[list[TransitSignal], dict]:
    """Iterative BLS on a detrended light curve.

    Returns detected signals (strongest first) and the first periodogram
    (downsampled) for display.
    """
    from astropy.timeseries import BoxLeastSquares

    cfg = {**config.TRANSIT, **(cfg or {})}
    star = star or Star()
    tb, fb, _ = bin_lc(t, f, cfg["bin_minutes"] / 1440.0)
    baseline = float(tb[-1] - tb[0]) if len(tb) > 1 else 0.0
    if baseline < 2.0 or len(tb) < 200:
        return [], {}
    pmax = min(cfg["max_period_cap_d"], baseline / cfg["min_transits"])
    pmin = cfg["min_period_d"]
    durations = [d for d in cfg["durations_d"] if d < pmin * 0.5]

    signals: list[TransitSignal] = []
    periodogram: dict = {}
    keep = np.ones(len(tb), bool)
    keep_full = np.ones(len(t), bool)

    for idx in range(cfg["max_signals"]):
        tt, ff = tb[keep], fb[keep]
        sigma = robust_std(ff - np.median(ff))
        if not np.isfinite(sigma) or sigma <= 0:
            break
        bls = BoxLeastSquares(tt, ff, dy=np.full_like(ff, sigma))
        freqs = _frequency_grid(baseline, pmin, pmax, min(durations), cfg["max_frequencies"])
        periods = np.sort(1.0 / freqs)
        res = bls.power(periods, durations, objective="likelihood", oversample=5)
        power = np.asarray(res.power, float)
        trend = _running_median(power, max(51, len(power) // 200))
        resid = power - trend
        sd = np.std(resid)
        if not np.isfinite(sd) or sd == 0:
            break
        k = int(np.argmax(resid))
        sde = float((resid[k] - np.mean(resid)) / sd)
        P = float(res.period[k])
        dur = float(res.duration[k])
        t0 = float(res.transit_time[k])
        if idx == 0:
            px, py = _downsample_max(periods, resid / sd, 1600)
            periodogram = {"p": [round(v, 6) for v in px], "power": [round(v, 3) for v in py]}

        # refine duration on a fine grid at fixed period
        fine = np.linspace(max(0.4 * dur, 0.02), min(1.8 * dur, 0.3 * P), 24)
        r2 = bls.power([P], fine, objective="likelihood", oversample=20)
        dur = float(r2.duration[0])
        t0 = float(r2.transit_time[0])

        st = bls.compute_stats(P, dur, t0)
        depth, depth_err = (float(st["depth"][0]), float(st["depth"][1]))
        counts = np.asarray(st["per_transit_count"])
        ll = np.asarray(st["per_transit_log_likelihood"])
        n_tr = int((counts > 0).sum())
        snr_white = depth / depth_err if depth_err > 0 else 0.0
        snr = min(snr_white, _red_noise_snr(tt, ff, P, t0, dur, depth, n_tr))
        pos = ll[ll > 0].sum()
        single_frac = float(ll.max() / pos) if pos > 0 else 1.0

        if any(_same_period(P, prev.period) for prev in signals):
            log(f"signal {idx + 1}: P={P:.4f} d repeats an earlier signal — stopping")
            break
        if sde < cfg["sde_threshold"] or snr < cfg["snr_threshold"] or n_tr < cfg["min_transits"] or depth <= 0:
            log(f"signal {idx + 1}: none above threshold (SDE {sde:.1f}, SNR {snr:.1f})")
            break

        d_odd, e_odd = st["depth_odd"]
        d_even, e_even = st["depth_even"]
        oe = abs(d_odd - d_even) / math.hypot(e_odd, e_even) if (e_odd > 0 and e_even > 0) else 0.0
        sec, sec_err = st["depth_phased"]
        sec_snr = float(sec / sec_err) if sec_err > 0 else 0.0

        sig = TransitSignal(
            index=idx + 1,
            period=P,
            t0=t0,
            duration=dur,
            depth=depth,
            depth_err=depth_err,
            snr=float(snr),
            sde=sde,
            n_transits=n_tr,
            odd_even_sigma=float(oe),
            secondary_snr=sec_snr,
            secondary_depth=float(sec),
            harmonic_dll=float(st["harmonic_delta_log_likelihood"]),
            single_event_frac=single_frac,
            shape_ratio=_shape_ratio(t[keep_full], f[keep_full], P, t0, dur),
            edge_frac=_edge_fraction(t[keep_full], P, t0, dur),
            centroid_sigma=None,
            centroid_shift_px=None,
            rp_earth=None,
            duration_ratio=None,
        )
        if centroids is not None and centroids[0] is not None:
            cs = _centroid_test(t, centroids[0], centroids[1], P, t0, dur)
            if cs:
                sig.centroid_sigma, sig.centroid_shift_px = cs
        _physical(sig, star)
        _classify(sig, star)
        signals.append(sig)
        log(
            f"signal {idx + 1}: P={P:.5f} d  depth={depth * 1e6:.0f} ppm  SNR={snr:.1f}  SDE={sde:.1f}"
            f"  → {sig.kind}{' [' + ','.join(sig.flags) + ']' if sig.flags else ''}"
        )
        if sig.kind != "planet_candidate":
            break  # EB / sinusoid harmonics would dominate further passes
        # mask this signal and look again
        keep &= np.abs(fold(tb, P, t0) * P) > 0.75 * dur
        keep_full &= np.abs(fold(t, P, t0) * P) > 0.75 * dur
        if keep.sum() < 200:
            break
    return signals, periodogram


def _same_period(p: float, q: float, tol: float = 0.02) -> bool:
    """p and q within tol of each other or of a 2:1 / 3:1 harmonic."""
    return any(abs(p / (q * k) - 1) < tol for k in (1, 2, 0.5, 3, 1 / 3))


def _red_noise_snr(t, f, P, t0, dur, depth, n_tr) -> float:
    """Depth over the scatter of duration-long bins (Pont et al. 2006).

    White-noise SNR overstates significance when the light curve carries
    correlated noise (spots, granulation, scattered light); binning the
    out-of-transit data on the transit timescale captures it.
    """
    oot = np.abs(fold(t, P, t0) * P) > dur
    if oot.sum() < 20 or n_tr < 1:
        return 0.0
    _, fb, cnt = bin_lc(t[oot], f[oot], dur)
    fb = fb[cnt >= max(3, int(cnt.max() * 0.5))]
    if len(fb) < 8:
        return 0.0
    sigma = robust_std(fb)
    return float(depth / (sigma / math.sqrt(n_tr))) if sigma > 0 else 0.0


def prewhiten(t: np.ndarray, f: np.ndarray, period: float, harmonics: int = 3) -> np.ndarray:
    """Remove a multi-harmonic sinusoid (fast rotation / pulsation) from f."""
    w = 2 * math.pi / period
    cols = [np.ones_like(t)]
    for k in range(1, harmonics + 1):
        cols += [np.sin(k * w * t), np.cos(k * w * t)]
    X = np.vstack(cols).T
    coef, *_ = np.linalg.lstsq(X, f, rcond=None)
    return f - X @ coef + coef[0]


def _shape_ratio(t, f, P, t0, dur) -> float | None:
    dt = np.abs(fold(t, P, t0) * P)
    inner = f[dt < 0.2 * dur]
    outer = f[(dt > 0.3 * dur) & (dt < 0.45 * dur)]
    base = f[(dt > 1.0 * dur) & (dt < 3.0 * dur)]
    if len(inner) < 10 or len(outer) < 10 or len(base) < 10:
        return None
    b = np.median(base)
    di, do = b - np.mean(inner), b - np.mean(outer)
    if di <= 0:
        return None
    return float(do / di)


def _edge_fraction(t, P, t0, dur) -> float:
    """Fraction of in-transit points within 0.3 d of a data gap/edge."""
    intr = np.abs(fold(t, P, t0) * P) < 0.5 * dur
    if intr.sum() == 0:
        return 0.0
    near = np.zeros(len(t), bool)
    for seg in segments(t):
        ts = t[seg]
        near[seg] = (ts - ts[0] < 0.3) | (ts[-1] - ts < 0.3)
    return float((near & intr).sum() / intr.sum())


def _centroid_test(t, cx, cy, P, t0, dur):
    ok = np.isfinite(cx) & np.isfinite(cy)
    if ok.sum() < 100:
        return None
    t, cx, cy = t[ok], cx[ok], cy[ok]
    dt = np.abs(fold(t, P, t0) * P)
    intr = dt < 0.4 * dur
    oot = (dt > 0.75 * dur) & (dt < 2.5 * dur)
    if intr.sum() < 5 or oot.sum() < 10:
        return None
    zs, shift2 = [], 0.0
    for c in (cx, cy):
        d = np.mean(c[intr]) - np.mean(c[oot])
        err = robust_std(c[oot]) * math.sqrt(1 / intr.sum() + 1 / oot.sum())
        zs.append(d / err if err > 0 else 0.0)
        shift2 += d * d
    return float(math.hypot(*zs)), float(math.sqrt(shift2))


def _physical(sig: TransitSignal, star: Star) -> None:
    depth = sig.depth
    if star.crowdsap and 0.05 < star.crowdsap < 1.0:
        depth = depth / star.crowdsap
    if star.radius:
        sig.rp_earth = float(math.sqrt(max(depth, 0)) * star.radius * R_EARTH_PER_R_SUN)
    rho = star.density
    if rho and rho > 0:
        t_exp_h = 13.0 * (sig.period / 365.25) ** (1 / 3) * rho ** (-1 / 3)
        sig.duration_ratio = float(sig.duration * 24 / t_exp_h)


def _classify(sig: TransitSignal, star: Star) -> None:
    flags: list[str] = []
    hard_eb = False
    if sig.secondary_snr > 5 and sig.secondary_depth > 0.1 * sig.depth:
        flags.append("SECONDARY_ECLIPSE")
        hard_eb = True
    if sig.odd_even_sigma > 4:
        flags.append("ODD_EVEN_MISMATCH")
        hard_eb = True
    if sig.rp_earth and sig.rp_earth > 24:
        flags.append("TOO_LARGE_FOR_PLANET")
        hard_eb = True
    if sig.shape_ratio is not None and sig.shape_ratio < 0.45 and sig.snr > 15:
        flags.append("V_SHAPED")
        hard_eb = True
    if sig.harmonic_dll > 0:
        flags.append("SINUSOID_PREFERRED")
    if sig.centroid_sigma is not None and sig.centroid_sigma > 5:
        flags.append("CENTROID_SHIFT")
    if sig.duration_ratio is not None and sig.duration_ratio > 1.8:
        flags.append("LONG_DURATION")
    if sig.single_event_frac > 0.65:
        flags.append("SINGLE_EVENT_DOMINATED")
    if sig.edge_frac > 0.5:
        flags.append("NEAR_DATA_GAPS")
    if any(lo <= sig.period <= hi for lo, hi in TESS_ORBIT_ALIASES):
        flags.append("TESS_ORBIT_ALIAS")
    if star.crowdsap is not None and star.crowdsap < 0.8:
        flags.append("CROWDED_APERTURE")
    if star.radius and star.radius > 3:
        flags.append("EVOLVED_HOST")
    sig.flags = flags

    if sig.edge_frac > 0.8 or ("TESS_ORBIT_ALIAS" in flags and sig.edge_frac > 0.5):
        sig.kind = "systematic"
    elif hard_eb:
        sig.kind = "eclipsing_binary"
    elif "SINUSOID_PREFERRED" in flags:
        sig.kind = "variability"
    else:
        sig.kind = "planet_candidate"

    s = 0.45 * min(1.0, (sig.snr - 7) / 20) + 0.3 * min(1.0, (sig.sde - 8) / 12) + 0.25 * min(1.0, (sig.n_transits - 1) / 4)
    soft = {"CENTROID_SHIFT", "LONG_DURATION", "SINGLE_EVENT_DOMINATED", "NEAR_DATA_GAPS", "TESS_ORBIT_ALIAS", "CROWDED_APERTURE", "EVOLVED_HOST"}
    s -= 0.15 * sum(1 for fl in flags if fl in soft)
    sig.score = float(max(0.0, min(1.0, s)))


# ── Lomb-Scargle ───────────────────────────────────────────────────────────

def variability_search(t: np.ndarray, f: np.ndarray, star: Star | None = None, cfg: dict | None = None) -> tuple[VariableSignal | None, dict]:
    from astropy.timeseries import LombScargle

    cfg = {**config.VARIABLE, **(cfg or {})}
    star = star or Star()
    tb, fb, _ = bin_lc(t, f, 30 / 1440.0)
    if len(tb) < 100:
        return None, {}
    baseline = tb[-1] - tb[0]
    pmax = min(cfg["max_period_d"], baseline / 3)
    ls = LombScargle(tb, fb)
    freq, power = ls.autopower(minimum_frequency=1 / pmax, maximum_frequency=1 / cfg["min_period_d"], samples_per_peak=10)
    k = int(np.argmax(power))
    P = float(1 / freq[k])
    pw = float(power[k])
    px, py = _downsample_max((1 / freq)[::-1], power[::-1], 1200)
    pgram = {"p": [round(v, 6) for v in px], "power": [round(v, 4) for v in py]}

    theta = ls.model_parameters(freq[k])
    amp = float(math.hypot(theta[1], theta[2])) if len(theta) >= 3 else 0.0
    resid = fb - ls.model(tb, freq[k])
    noise = robust_std(resid)
    try:
        fap = float(ls.false_alarm_probability(pw, minimum_frequency=1 / pmax, maximum_frequency=1 / cfg["min_period_d"]))
    except Exception:
        fap = float("nan")

    _, prof = _profile(tb, fb, P, 50, tb[np.argmin(fb)])
    ptp = float(prof.max() - prof.min()) if len(prof) else 0.0
    skew = _skew(prof) if len(prof) > 5 else 0.0

    detected = pw >= cfg["min_power"] and amp * 1e6 >= cfg["min_amp_ppm"] and amp >= cfg["min_amp_sigma"] * noise / math.sqrt(max(len(tb) / 50, 1))
    if not detected:
        return None, pgram

    gtype, label, true_p = _guess_type(P, amp, ptp, skew, star, tb, fb)
    score = min(1.0, 0.5 * pw + 0.5 * min(1.0, amp / max(noise, 1e-6) / 10))
    return VariableSignal(P, pw, fap, amp * 1e6, ptp * 1e6, noise * 1e6, skew, gtype, label, true_p, score), pgram


def _skew(x: np.ndarray) -> float:
    x = x - x.mean()
    s = x.std()
    return float((x**3).mean() / s**3) if s > 0 else 0.0


def _profile(t: np.ndarray, f: np.ndarray, P: float, nb: int, t0: float) -> tuple[np.ndarray, np.ndarray]:
    """Phase-binned mean profile, phase 0 at t0; empty bins dropped."""
    ph = fold(t, P, t0)
    edges = np.linspace(-0.5, 0.5, nb + 1)
    idx = np.clip(np.digitize(ph, edges) - 1, 0, nb - 1)
    cnt = np.bincount(idx, minlength=nb)
    prof = np.bincount(idx, weights=f, minlength=nb) / np.maximum(cnt, 1)
    centers = 0.5 * (edges[1:] + edges[:-1])
    return centers[cnt > 0], prof[cnt > 0]


def _flat_fraction(prof: np.ndarray) -> float:
    """Share of the cycle spent near maximum light (detached EBs ≫ pulsators)."""
    if len(prof) < 5:
        return 0.0
    hi, lo = np.percentile(prof, 98), prof.min()
    return float(np.mean(prof > hi - 0.15 * (hi - lo)))


def _guess_type(P, amp, ptp, skew, star: Star, t, f) -> tuple[str, str, float]:
    """Coarse VSX-style class from period, amplitude, shape and Teff."""
    teff = star.teff or 0
    t0 = t[np.argmin(f)]
    _, p1 = _profile(t, f, P, 60, t0)
    flat1 = _flat_fraction(p1)
    # RRab: large amplitude, fast rise / slow decline, never flat
    if 0.25 < P < 1.2 and ptp > 0.1 and skew > 0.25 and flat1 < 0.35:
        return "RRAB", "RR Lyrae (fundamental mode) candidate", P

    # Eclipses: fold at 2P — Lomb-Scargle often locks onto half the orbit
    c2, p2 = _profile(t, f, 2 * P, 120, t0)
    if len(p2) > 20:
        base = np.percentile(p2, 90)
        d1 = base - p2[np.abs(c2) < 0.04].min() if (np.abs(c2) < 0.04).any() else 0.0
        d2 = base - p2[np.abs(np.abs(c2) - 0.5) < 0.04].min() if (np.abs(np.abs(c2) - 0.5) < 0.04).any() else 0.0
        flat2 = _flat_fraction(p2)
        if flat2 > 0.35 and d1 > 0.002:
            if d2 > 0.08 * d1:
                return "EA", "detached eclipsing binary", 2 * P
            return "EA", "detached eclipsing binary (single eclipse per cycle)", P
        # A cool dwarf cannot rotate or pulsate this fast with this amplitude:
        # a short, near-sinusoidal signal there is a contact binary at 2P.
        cool_fast = P < 0.5 and 0.2 <= 2 * P <= 1.0 and ptp > 0.005 and (not teff or teff < 6300)
        if cool_fast or (P < 0.6 and skew < -0.3 and ptp > 0.003):
            if d1 > 0 and d2 > 0 and abs(d1 - d2) / max(d1, d2) > 0.15:
                return "EB", "β Lyrae-type eclipsing binary (unequal minima)", 2 * P
            return "EW", "contact binary (W UMa type)", 2 * P
    if P < 0.3 and 6300 < teff < 9000:
        return "DSCT", "δ Scuti pulsator", P
    if 0.3 <= P < 3.5 and 6800 < teff < 7600:
        return "GDOR", "γ Doradus pulsator", P
    if teff and teff < 6500 and P >= 0.3:
        return "ROT", "spotted rotator (starspot modulation)", P
    if teff and teff > 10000:
        return "SPB/ROT", "hot-star variability (SPB / rotation)", P
    return "VAR", "periodic variable (unclassified)", P
