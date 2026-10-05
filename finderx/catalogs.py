"""Reference catalogs used to decide whether a signal is already known.

Bulk lists (TOIs, community TOIs, confirmed planets, TESS eclipsing binaries)
are downloaded once, cached on disk for ``CATALOG_TTL_HOURS`` and indexed by
TIC. Per-position catalogs (VSX, Gaia DR3 variability, SIMBAD) are queried
by cone on demand.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from . import config, net

_lock = threading.Lock()
_mem: dict[str, dict[int, list[dict]]] = {}


def _cache_path(name: str) -> Path:
    return config.CACHE_DIR / f"{name}.json"


def _fresh(path: Path) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < config.CATALOG_TTL_HOURS * 3600


def _load(name: str, builder) -> dict[int, list[dict]]:
    with _lock:
        if name in _mem:
            return _mem[name]
        path = _cache_path(name)
        index: dict[int, list[dict]] | None = None
        if _fresh(path):
            index = {int(k): v for k, v in json.loads(path.read_text()).items()}
        else:
            try:
                index = builder()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(index))
            except Exception:
                if path.exists():  # stale beats nothing
                    index = {int(k): v for k, v in json.loads(path.read_text()).items()}
                else:
                    raise
        _mem[name] = index
        return index


def _add(index: dict[int, list[dict]], tic, rec: dict) -> None:
    try:
        tic = int(tic)
    except (TypeError, ValueError):
        return
    index.setdefault(tic, []).append(rec)


def _build_toi() -> dict[int, list[dict]]:
    rows = net.parse_csv(net.get(config.TOI_CSV, timeout=120).text)
    idx: dict[int, list[dict]] = {}
    for r in rows:
        _add(idx, r.get("TIC ID"), {
            "label": f"TOI-{r.get('TOI')}",
            "period": r.get("Period (days)"),
            "disposition": r.get("TFOPWG Disposition") or r.get("TESS Disposition"),
            "depth_ppm": r.get("Depth (ppm)"),
        })
    return idx


def _build_ctoi() -> dict[int, list[dict]]:
    rows = net.parse_csv(net.get(config.CTOI_CSV, timeout=120).text)
    idx: dict[int, list[dict]] = {}
    for r in rows:
        _add(idx, r.get("TIC ID"), {
            "label": f"CTOI {r.get('CTOI')}",
            "period": r.get("Period (days)"),
            "disposition": r.get("User Disposition") or r.get("TFOPWG Disposition"),
        })
    return idx


def _build_planets() -> dict[int, list[dict]]:
    rows = net.tap(
        config.EXOARCHIVE_TAP,
        "select pl_name, tic_id, pl_orbper from pscomppars where tic_id is not null",
    )
    idx: dict[int, list[dict]] = {}
    for r in rows:
        tic = str(r.get("tic_id") or "").replace("TIC", "").strip()
        _add(idx, tic, {"label": r.get("pl_name"), "period": r.get("pl_orbper"), "disposition": "CONFIRMED"})
    return idx


def _build_ebs() -> dict[int, list[dict]]:
    rows = net.tap(config.VIZIER_TAP, f'SELECT TIC, Per, Morph FROM "{config.VZ_TESS_EB}"', max_rec=50000)
    idx: dict[int, list[dict]] = {}
    for r in rows:
        _add(idx, r.get("TIC"), {"label": f"TESS EB {r.get('TIC')}", "period": r.get("Per"), "morph": r.get("Morph")})
    return idx


def toi() -> dict[int, list[dict]]:
    return _load("toi", _build_toi)


def ctoi() -> dict[int, list[dict]]:
    return _load("ctoi", _build_ctoi)


def planets() -> dict[int, list[dict]]:
    return _load("planets", _build_planets)


def ebs() -> dict[int, list[dict]]:
    return _load("tess_ebs", _build_ebs)


def warm(log=lambda m: None) -> None:
    for name, fn in (("TOI list", toi), ("CTOI list", ctoi), ("confirmed planets", planets), ("TESS EB catalog", ebs)):
        try:
            n = len(fn())
            log(f"{name}: {n:,} stars indexed")
        except Exception as exc:  # keep going; checks degrade gracefully
            log(f"{name} unavailable ({exc})")


def known_for_tic(tic: int) -> list[dict]:
    """Every known transit/eclipse record for a TIC across bulk catalogs."""
    out: list[dict] = []
    for kind, fn in (("planet", planets), ("toi", toi), ("ctoi", ctoi), ("eb", ebs)):
        try:
            for rec in fn().get(int(tic), []):
                out.append({"kind": kind, **rec})
        except Exception:
            continue
    return out


def excluded_tics() -> set[int]:
    """Stars a blind survey should skip because they are already flagged."""
    s: set[int] = set()
    for fn in (toi, ctoi, planets, ebs):
        try:
            s.update(fn().keys())
        except Exception:
            continue
    return s


def period_match(p: float, q: float | None, tol: float = 0.01) -> str | None:
    """Return the harmonic relation if p ≈ k·q or q/k (k ∈ 1,2,3), else None."""
    if not q or not p or q <= 0:
        return None
    for k, label in ((1, "1:1"), (2, "2:1"), (0.5, "1:2"), (3, "3:1"), (1 / 3, "1:3")):
        if abs(p / (q * k) - 1) < tol:
            return label
    return None


def vsx_cone(ra: float, dec: float, radius_arcsec: float = config.TESS_PIXEL_ARCSEC) -> list[dict]:
    q = (
        f'SELECT Name, Type, Period, max, min, RAJ2000, DEJ2000 FROM "{config.VZ_VSX}" '
        f"WHERE {net.cone('RAJ2000', 'DEJ2000', ra, dec, radius_arcsec / 3600)}"
    )
    return net.tap(config.VIZIER_TAP, q, timeout=40)


def gaia_variability_cone(ra: float, dec: float, radius_arcsec: float = config.TESS_PIXEL_ARCSEC) -> list[dict]:
    q = (
        f'SELECT Source, Class, ClassSc, RA_ICRS, DE_ICRS FROM "I/358/vclassre" '
        f"WHERE {net.cone('RA_ICRS', 'DE_ICRS', ra, dec, radius_arcsec / 3600)}"
    )
    return net.tap(config.VIZIER_TAP, q, timeout=40)


def gaia_eb_periods(source_ids: list) -> dict[int, float]:
    """Orbital periods from the Gaia DR3 eclipsing-binary catalogue."""
    ids = ",".join(str(int(x)) for x in source_ids if x)
    if not ids:
        return {}
    rows = net.tap(config.VIZIER_TAP, f'SELECT Source, Freq FROM "I/358/veb" WHERE Source IN ({ids})', timeout=40)
    return {int(r["Source"]): 1.0 / r["Freq"] for r in rows if r.get("Freq")}
