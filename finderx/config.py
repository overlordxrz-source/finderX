"""Paths, service endpoints and pipeline thresholds.

Everything tunable lives here so the engines stay readable. Values can be
overridden with environment variables where noted.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("FINDERX_DATA", ROOT / "data"))
CACHE_DIR = DATA_DIR / "cache"
DB_PATH = DATA_DIR / "finderx.db"
WEB_DIR = Path(__file__).resolve().parent / "web"

USER_AGENT = "finderX/2.0 (+https://github.com/overlordxrz-source/finderX)"

# ── Archive endpoints ─────────────────────────────────────────────────────
MAST_INVOKE = "https://mast.stsci.edu/api/v0/invoke"
MAST_DOWNLOAD = "https://mast.stsci.edu/api/v0.1/Download/file"
VIZIER_TAP = "https://tapvizier.cds.unistra.fr/TAPVizieR/tap/sync"
SIMBAD_TAP = "https://simbad.cds.unistra.fr/simbad/sim-tap/sync"
NED_TAP = "https://ned.ipac.caltech.edu/tap/sync"
EXOARCHIVE_TAP = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"
CDS_XMATCH = "http://cdsxmatch.u-strasbg.fr/xmatch/api/v1/sync"
SESAME = "https://cds.unistra.fr/cgi-bin/nph-sesame/-oxp/SNV"
TOI_CSV = "https://exofop.ipac.caltech.edu/tess/download_toi.php?sort=toi&output=csv"
CTOI_CSV = "https://exofop.ipac.caltech.edu/tess/download_ctoi.php?sort=ctoi&output=csv"
SKYBOT = "https://ssp.imcce.fr/webservices/skybot/api/conesearch.php"
NEOCP_TXT = "https://minorplanetcenter.net/iau/NEO/neocp.txt"
JPL_CAD = "https://ssd-api.jpl.nasa.gov/cad.api"

# Health-check targets shown as link lights in the UI.
LINKS = {
    "MAST": "https://mast.stsci.edu/api/v0/",
    "CDS": "https://tapvizier.cds.unistra.fr/TAPVizieR/tap/availability",
    "NASA-EXO": "https://exoplanetarchive.ipac.caltech.edu/TAP/availability",
    "NED": "https://ned.ipac.caltech.edu/tap/availability",
    "IMCCE": "https://ssp.imcce.fr/webservices/skybot/",
    "MPC": "https://minorplanetcenter.net/iau/NEO/neocp.txt",
}

# VizieR table names (all fast, indexed mirrors).
VZ_GAIA = "I/355/gaiadr3"
VZ_GAIA_QSO = "I/356/qsocand"
VZ_GAIA_GAL = "I/356/galcand"
VZ_ALLWISE = "II/328/allwise"
VZ_MILLIQUAS = "VII/294/catalog"
VZ_VSX = "B/vsx/vsx"
VZ_TESS_EB = "J/ApJS/258/16/tess-ebs"

# How long downloaded reference catalogs stay fresh.
CATALOG_TTL_HOURS = float(os.environ.get("FINDERX_CATALOG_TTL", 24))

# ── Transit search ────────────────────────────────────────────────────────
TRANSIT = {
    "bin_minutes": 10.0,           # cadence the BLS runs on
    "detrend_window_d": 0.75,      # sliding robust-median window
    "min_period_d": 0.5,
    "max_period_cap_d": 40.0,
    "durations_d": [0.04, 0.06, 0.08, 0.11, 0.15, 0.2, 0.27],
    "max_frequencies": 80_000,
    "max_signals": 3,              # iterative search depth (multi-planet)
    "sde_threshold": 9.0,
    "snr_threshold": 8.0,          # red-noise aware (see analysis._red_noise_snr)
    "min_transits": 2,
    "max_sectors": 4,              # multi-sector stitching cap for single-star scans
}

# ── Periodic variability ──────────────────────────────────────────────────
VARIABLE = {
    "min_period_d": 0.04,
    "max_period_d": 13.0,
    "min_power": 0.25,             # Lomb-Scargle normalized power
    "min_amp_sigma": 6.0,          # semi-amplitude vs. point scatter
    "min_amp_ppm": 800.0,
}

# Matching radii.
TESS_PIXEL_ARCSEC = 21.0
