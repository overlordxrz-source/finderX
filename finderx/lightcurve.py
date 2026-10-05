"""TESS light-curve discovery, download and conditioning.

Products, in order of preference per sector:
  SPOC 2-min  →  TESS-SPOC FFI (200/600/1800 s)  →  QLP FFI

``LightCurve`` holds one sector; ``stitch`` joins sectors after per-sector
normalisation. ``detrend`` is a gap-aware sliding robust median evaluated on
a coarse grid and interpolated, which is fast and preserves transits shorter
than ~1/3 of the window.
"""

from __future__ import annotations

import io
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

from . import config, net

_PREFERENCE = {"SPOC": 0, "TESS-SPOC": 1, "QLP": 2}


@dataclass
class LightCurve:
    tic: int
    sector: int
    provenance: str
    cadence_s: float
    time: np.ndarray            # BTJD
    flux: np.ndarray            # normalised to median 1
    flux_err: np.ndarray
    centroid_col: np.ndarray | None = None
    centroid_row: np.ndarray | None = None
    meta: dict = field(default_factory=dict)

    @property
    def baseline(self) -> float:
        return float(self.time[-1] - self.time[0]) if len(self.time) > 1 else 0.0


# ── Product discovery ──────────────────────────────────────────────────────

def _is_lc_product(url: str) -> bool:
    url = url or ""
    return (url.endswith("s_lc.fits") or url.endswith("_lc.fits") or url.endswith("_llc.fits")) and (
        "fast-lc" not in url and "_dvt" not in url
    )


def products_for_tic(tic: int) -> list[dict]:
    """Best light-curve product per sector for one TIC, newest sector first."""
    out = net.mast(
        "Mast.Caom.Filtered",
        {
            "columns": "obsid,target_name,sequence_number,provenance_name,t_exptime,dataURL,s_ra,s_dec",
            "filters": [
                {"paramName": "obs_collection", "values": ["TESS", "HLSP"]},
                {"paramName": "dataproduct_type", "values": ["timeseries"]},
                {"paramName": "target_name", "values": [str(tic)]},
                {"paramName": "provenance_name", "values": list(_PREFERENCE)},
            ],
        },
    )
    best: dict[int, dict] = {}
    for row in out.get("data", []):
        url = row.get("dataURL") or ""
        if not _is_lc_product(url):
            continue
        sec = row.get("sequence_number")
        if sec is None:
            continue
        rank = _PREFERENCE.get(row.get("provenance_name"), 9)
        cur = best.get(sec)
        if cur is None or rank < _PREFERENCE.get(cur["provenance_name"], 9):
            best[sec] = row
    return [best[s] for s in sorted(best, reverse=True)]


def sector_population(sector: int, provenance: str = "TESS-SPOC") -> int:
    out = net.mast(
        "Mast.Caom.Filtered",
        {"columns": "COUNT_BIG(*)", "filters": _sector_filters(sector, provenance)},
    )
    data = out.get("data") or [{}]
    return int(next(iter(data[0].values()), 0) or 0)


def _sector_filters(sector: int, provenance: str) -> list[dict]:
    return [
        {"paramName": "obs_collection", "values": ["HLSP"] if provenance != "SPOC" else ["TESS"]},
        {"paramName": "dataproduct_type", "values": ["timeseries"]},
        {"paramName": "sequence_number", "values": [int(sector)]},
        {"paramName": "provenance_name", "values": [provenance]},
    ]


def sample_sector(
    sector: int,
    n: int,
    provenance: str = "TESS-SPOC",
    rng: random.Random | None = None,
    population: int | None = None,
    pagesize: int = 200,
) -> list[dict]:
    """Random light-curve products from one sector (random MAST pages)."""
    rng = rng or random.Random()
    population = population or sector_population(sector, provenance)
    if population == 0:
        return []
    pages = max(1, math.ceil(population / pagesize))
    seen: set[str] = set()
    out: list[dict] = []
    tries = 0
    while len(out) < n and tries < max(4, n // pagesize * 3 + 4):
        tries += 1
        res = net.mast(
            "Mast.Caom.Filtered",
            {
                "columns": "obsid,target_name,sequence_number,provenance_name,t_exptime,dataURL,s_ra,s_dec",
                "filters": _sector_filters(sector, provenance),
            },
            page=rng.randint(1, pages),
            pagesize=pagesize,
        )
        rows = [r for r in res.get("data", []) if _is_lc_product(r.get("dataURL", ""))]
        rng.shuffle(rows)
        for r in rows:
            if r["target_name"] not in seen:
                seen.add(r["target_name"])
                out.append(r)
    return out[:n]


def estimate_latest_sector(now: datetime | None = None) -> int:
    """Rough current TESS sector from the date (27.4 d cadence since S1)."""
    now = now or datetime.now(timezone.utc)
    s1 = datetime(2018, 7, 25, tzinfo=timezone.utc)
    return int((now - s1).days / 27.4) + 1


def available_ffi_sectors(limit: int = 6, provenance: str = "QLP", span: int = 36) -> list[tuple[int, int, str]]:
    """Newest sectors that already have FFI light curves at MAST.

    HLSP products lag the spacecraft by months, so probe a window of recent
    sector numbers in parallel and keep the newest populated ones.
    """
    from concurrent.futures import ThreadPoolExecutor

    top = estimate_latest_sector() + 2
    probe = list(range(top, max(0, top - span), -1))
    with ThreadPoolExecutor(max_workers=8) as ex:
        counts = list(ex.map(lambda s: _safe_population(s, provenance), probe))
    found = [(s, n, provenance) for s, n in zip(probe, counts) if n > 0]
    return found[:limit]


def _safe_population(sector: int, provenance: str) -> int:
    try:
        return sector_population(sector, provenance)
    except Exception:
        return 0


# ── FITS loading ───────────────────────────────────────────────────────────

def load(fits_bytes: bytes, provenance: str = "") -> LightCurve:
    from astropy.io import fits

    with fits.open(io.BytesIO(fits_bytes), memmap=False) as h:
        hdr0 = h[0].header
        hdr1 = h[1].header
        d = h[1].data
        cols = set(d.columns.names)
        t = np.asarray(d["TIME"], dtype=float)
        if "PDCSAP_FLUX" in cols:
            f = np.asarray(d["PDCSAP_FLUX"], dtype=float)
            fe = np.asarray(d["PDCSAP_FLUX_ERR"], dtype=float)
        elif "KSPSAP_FLUX" in cols:
            f = np.asarray(d["KSPSAP_FLUX"], dtype=float)
            fe = np.asarray(d["KSPSAP_FLUX_ERR"], dtype=float)
        elif "DET_FLUX" in cols:
            f = np.asarray(d["DET_FLUX"], dtype=float)
            fe = np.asarray(d["DET_FLUX_ERR"], dtype=float)
        else:
            f = np.asarray(d["SAP_FLUX"], dtype=float)
            fe = np.full_like(f, np.nan)
        q = np.asarray(d["QUALITY"], dtype=int) if "QUALITY" in cols else np.zeros(len(t), int)
        cc = np.asarray(d["MOM_CENTR1"], dtype=float) if "MOM_CENTR1" in cols else None
        cr = np.asarray(d["MOM_CENTR2"], dtype=float) if "MOM_CENTR2" in cols else None
        if cc is None and "SAP_X" in cols:
            cc = np.asarray(d["SAP_X"], dtype=float)
            cr = np.asarray(d["SAP_Y"], dtype=float)

        tic = int(hdr0.get("TICID") or str(hdr0.get("OBJECT", "TIC 0")).split()[-1])
        meta = {
            "tic": tic,
            "ra": _f(hdr0.get("RA_OBJ")),
            "dec": _f(hdr0.get("DEC_OBJ")),
            "tmag": _f(hdr0.get("TESSMAG")),
            "teff": _f(hdr0.get("TEFF")),
            "logg": _f(hdr0.get("LOGG")),
            "radius": _f(hdr0.get("RADIUS")),
            "mh": _f(hdr0.get("MH")),
            "crowdsap": _f(hdr1.get("CROWDSAP")),
        }
        sector = int(hdr0.get("SECTOR") or 0)
        cad = _f(hdr1.get("TIMEDEL"))
        cadence_s = cad * 86400.0 if cad else float(np.nanmedian(np.diff(t)) * 86400.0)

    good = np.isfinite(t) & np.isfinite(f) & (q == 0)
    if good.sum() < 100:  # some FFI products flag scattered light aggressively
        good = np.isfinite(t) & np.isfinite(f)
    t, f, fe = t[good], f[good], fe[good]
    cc = cc[good] if cc is not None else None
    cr = cr[good] if cr is not None else None
    order = np.argsort(t)
    t, f, fe = t[order], f[order], fe[order]
    cc = cc[order] if cc is not None else None
    cr = cr[order] if cr is not None else None
    med = np.nanmedian(f)
    if not np.isfinite(med) or med <= 0:
        raise ValueError("light curve has no usable flux")
    if robust_std(f / med) > 0.25:
        raise ValueError("light curve too noisy to search (faint or background-dominated)")
    f = f / med
    fe = fe / med
    if not np.isfinite(fe).any():
        fe = np.full_like(f, robust_std(np.diff(f)) / math.sqrt(2))
    fe = np.where(np.isfinite(fe), fe, np.nanmedian(fe))
    return LightCurve(tic, sector, provenance, cadence_s, t, f, fe, cc, cr, meta)


def fetch(product: dict, cache: bool = False) -> LightCurve:
    raw = net.mast_download(product["dataURL"], cache=cache)
    return load(raw, product.get("provenance_name", ""))


def _f(v) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


# ── Conditioning ───────────────────────────────────────────────────────────

def robust_std(x: np.ndarray) -> float:
    x = x[np.isfinite(x)]
    if len(x) == 0:
        return float("nan")
    return float(1.4826 * np.median(np.abs(x - np.median(x))))


def stitch(lcs: list[LightCurve]) -> LightCurve:
    if len(lcs) == 1:
        return lcs[0]
    lcs = sorted(lcs, key=lambda lc: lc.time[0])

    def cat(attr):
        parts = [getattr(lc, attr) for lc in lcs]
        if any(p is None for p in parts):
            return None
        # centroids are per-sector pixel coordinates: remove each sector's median
        if attr.startswith("centroid"):
            parts = [p - np.nanmedian(p) for p in parts]
        return np.concatenate(parts)

    base = lcs[-1]
    return LightCurve(
        tic=base.tic,
        sector=base.sector,
        provenance="+".join(sorted({lc.provenance for lc in lcs})),
        cadence_s=max(lc.cadence_s for lc in lcs),
        time=cat("time"),
        flux=cat("flux"),
        flux_err=cat("flux_err"),
        centroid_col=cat("centroid_col"),
        centroid_row=cat("centroid_row"),
        meta={**base.meta, "sectors": [lc.sector for lc in lcs]},
    )


def segments(t: np.ndarray, gap: float = 0.5) -> list[slice]:
    if len(t) == 0:
        return []
    breaks = np.where(np.diff(t) > gap)[0] + 1
    edges = np.concatenate([[0], breaks, [len(t)]])
    return [slice(int(a), int(b)) for a, b in zip(edges[:-1], edges[1:])]


def detrend(t: np.ndarray, f: np.ndarray, window: float, step: float | None = None) -> np.ndarray:
    """Gap-aware sliding robust (sigma-clipped) median, evaluated on a grid."""
    step = step or window / 8.0
    trend = np.empty_like(f)
    for seg in segments(t):
        ts, fs = t[seg], f[seg]
        if len(ts) < 10:
            trend[seg] = np.median(fs)
            continue
        nodes = np.arange(ts[0], ts[-1] + step, step)
        lo = np.searchsorted(ts, nodes - window / 2)
        hi = np.searchsorted(ts, nodes + window / 2)
        vals = np.full(len(nodes), np.nan)
        for k, (a, b) in enumerate(zip(lo, hi)):
            if b - a < 5:
                continue
            w = fs[a:b]
            m = np.median(w)
            s = 1.4826 * np.median(np.abs(w - m)) + 1e-12
            keep = np.abs(w - m) < 3 * s
            vals[k] = np.median(w[keep]) if keep.sum() >= 3 else m
        ok = np.isfinite(vals)
        trend[seg] = np.interp(ts, nodes[ok], vals[ok]) if ok.sum() >= 2 else np.median(fs)
    return trend


def clip_upper(f: np.ndarray, sigma: float = 4.0) -> np.ndarray:
    """Mask positive outliers (flares, cosmic rays) but keep dips."""
    med = np.median(f)
    s = robust_std(f)
    return f < med + sigma * s


def trim_edges(t: np.ndarray, width: float = 0.25) -> np.ndarray:
    """Mask the first/last `width` days of every segment (thermal and
    scattered-light ramps after downlinks and momentum dumps)."""
    keep = np.ones(len(t), bool)
    for seg in segments(t):
        ts = t[seg]
        keep[seg] = (ts - ts[0] > width) & (ts[-1] - ts > width)
    return keep


def clip_isolated_dips(f: np.ndarray, sigma: float = 5.0) -> np.ndarray:
    """Mask single-cadence negative spikes; real transits span many cadences."""
    from scipy.ndimage import median_filter

    if len(f) < 10:
        return np.ones(len(f), bool)
    d = f - median_filter(f, size=5, mode="nearest")
    s = robust_std(d)
    low = d < -sigma * s
    nb = np.zeros(len(f), bool)
    nb[1:] |= d[:-1] < -0.5 * sigma * s
    nb[:-1] |= d[1:] < -0.5 * sigma * s
    return ~(low & ~nb)


def bin_lc(t: np.ndarray, f: np.ndarray, width: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Bin by time; returns (t, f, n) for non-empty bins."""
    if len(t) == 0:
        return t, f, np.zeros(0, int)
    idx = np.floor((t - t[0]) / width).astype(np.int64)
    uniq, inv, counts = np.unique(idx, return_inverse=True, return_counts=True)
    tb = np.bincount(inv, weights=t) / counts
    fb = np.bincount(inv, weights=f) / counts
    return tb, fb, counts


def fold(t: np.ndarray, period: float, t0: float) -> np.ndarray:
    """Phase in [-0.5, 0.5) with transit at 0."""
    return ((t - t0) / period + 0.5) % 1.0 - 0.5


def bin_phase(phase: np.ndarray, f: np.ndarray, nbins: int, lo: float = -0.5, hi: float = 0.5):
    edges = np.linspace(lo, hi, nbins + 1)
    sel = (phase >= lo) & (phase < hi)
    idx = np.clip(np.digitize(phase[sel], edges) - 1, 0, nbins - 1)
    counts = np.bincount(idx, minlength=nbins)
    sums = np.bincount(idx, weights=f[sel], minlength=nbins)
    centers = 0.5 * (edges[1:] + edges[:-1])
    with np.errstate(invalid="ignore", divide="ignore"):
        means = sums / counts
    ok = counts > 0
    return centers[ok], means[ok]
