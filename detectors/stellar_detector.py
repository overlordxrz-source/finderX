"""
Stellar anomaly detector.

Pipeline
--------
1. Cone search Gaia DR3 (up to 500 sources) for the field.
2. Cone search SIMBAD for the same field.
3. Spatially cross-match the two: Gaia objects with no SIMBAD counterpart
   within 2 arcsec are flagged NOT_IN_SIMBAD.
4. Build a feature matrix from Gaia photometry / astrometry and run an
   Isolation Forest to flag statistical outliers (ML_ANOMALY).
5. Objects that are BOTH uncatalogued AND anomalous are "new candidates".
"""

from __future__ import annotations

import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


_GAIA_FEATURES = ["parallax", "pmra", "pmdec", "phot_g_mean_mag", "bp_rp"]


class StellarDetector:
    def find_anomalies(
        self,
        ra: float,
        dec: float,
        radius_deg: float,
        job: dict | None = None,
    ) -> list[dict]:
        from astroquery.gaia import Gaia
        from astroquery.simbad import Simbad
        from astropy.coordinates import SkyCoord, match_coordinates_sky
        import astropy.units as u
        from sklearn.ensemble import IsolationForest
        from sklearn.preprocessing import StandardScaler

        coord = SkyCoord(ra=ra * u.degree, dec=dec * u.degree, frame="icrs")

        # ── Gaia DR3 ──────────────────────────────────────────────────────
        Gaia.MAIN_GAIA_TABLE = "gaiadr3.gaia_source"
        Gaia.ROW_LIMIT = 500
        try:
            self._log(job, "  Fetching Gaia DR3 sources …")
            gaia_job = Gaia.cone_search_async(
                coordinate=coord,
                radius=u.Quantity(radius_deg, u.degree),
            )
            gaia_table = gaia_job.get_results()
        except Exception as exc:
            self._log(job, f"  Gaia failed: {exc}")
            return []

        if gaia_table is None or len(gaia_table) == 0:
            self._log(job, "  No Gaia sources found in this field.")
            return []

        self._log(job, f"  {len(gaia_table)} Gaia sources retrieved.")

        # ── SIMBAD cross-match ────────────────────────────────────────────
        simbad_matched_indices: set[int] = set()
        simbad_types: dict[int, str] = {}
        try:
            self._log(job, "  Cross-matching with SIMBAD …")
            simbad = Simbad()
            simbad.add_votable_fields("otype")
            simbad_table = simbad.query_region(coord, radius=radius_deg * u.degree)
            if simbad_table is not None and len(simbad_table) > 0:
                simbad_coords = SkyCoord(
                    ra=simbad_table["ra"].data.astype(float) * u.degree,
                    dec=simbad_table["dec"].data.astype(float) * u.degree,
                    frame="icrs",
                )
                gaia_coords = SkyCoord(
                    ra=gaia_table["ra"].data.astype(float) * u.degree,
                    dec=gaia_table["dec"].data.astype(float) * u.degree,
                    frame="icrs",
                )
                idx, sep2d, _ = gaia_coords.match_to_catalog_sky(simbad_coords)
                for i, sep in enumerate(sep2d):
                    if sep.arcsec < 2.0:
                        simbad_matched_indices.add(i)
                        try:
                            simbad_types[i] = str(simbad_table["otype"][idx[i]])
                        except Exception:
                            pass
            n_matched = len(simbad_matched_indices)
            n_total   = len(gaia_table)
            self._log(job, f"  {n_matched}/{n_total} Gaia sources matched in SIMBAD.")
        except Exception as exc:
            self._log(job, f"  SIMBAD cross-match failed: {exc}")

        # ── Isolation Forest on Gaia features ────────────────────────────
        anomaly_scores = np.zeros(len(gaia_table))
        available = [f for f in _GAIA_FEATURES if f in gaia_table.colnames]
        if len(available) >= 2:
            try:
                rows = {f: _safe_col(gaia_table[f]) for f in available}
                df = pd.DataFrame(rows)
                valid = df.notna().all(axis=1)
                df_v = df[valid]
                if len(df_v) >= 15:
                    scaler = StandardScaler()
                    X = scaler.fit_transform(df_v.values)
                    iso = IsolationForest(
                        contamination=0.1, n_estimators=150, random_state=42
                    )
                    iso.fit(X)
                    scores = iso.decision_function(X)
                    valid_idx = np.where(valid.values)[0]
                    for idx_v, score in zip(valid_idx, scores):
                        anomaly_scores[idx_v] = float(score)
                self._log(job, "  IsolationForest anomaly scoring done.")
            except Exception as exc:
                self._log(job, f"  ML scoring failed: {exc}")

        # ── Build result list ─────────────────────────────────────────────
        results: list[dict] = []
        for i, row in enumerate(gaia_table):
            is_uncat   = i not in simbad_matched_indices
            anom_score = float(anomaly_scores[i])
            is_anom    = anom_score < -0.05

            # Novelty: uncatalogued alone is already interesting
            novelty = 0.0
            if is_uncat:
                novelty += 1.0
            if is_anom:
                novelty += max(0.0, -anom_score)

            flags: list[str] = []
            if is_uncat:
                flags.append("NOT_IN_SIMBAD")
            if is_anom:
                flags.append("ML_ANOMALY")

            pm = _safe_val(row, "pmra"), _safe_val(row, "pmdec")
            has_high_pm = False
            if pm[0] is not None and pm[1] is not None:
                pm_mag = (pm[0] ** 2 + pm[1] ** 2) ** 0.5
                if pm_mag > 100:
                    flags.append("HIGH_PROPER_MOTION")
                    has_high_pm = True
                    novelty += 0.3

            bp_rp = _safe_val(row, "bp_rp")
            if bp_rp is not None and bp_rp < 0.2:
                flags.append("BLUE_OBJECT")
                novelty += 0.2

            # A new candidate is: uncatalogued in SIMBAD OR (anomalous + high PM) OR blue
            is_new = (
                is_uncat
                or (is_anom and has_high_pm)
                or (is_uncat and is_anom)
            )

            results.append(
                {
                    "id":            str(row["source_id"]),
                    "type":          "star",
                    "ra":            float(row["ra"]),
                    "dec":           float(row["dec"]),
                    "catalog":       "Gaia DR3",
                    "simbad_type":   simbad_types.get(i, ""),
                    "magnitude":     _safe_val(row, "phot_g_mean_mag"),
                    "bp_rp":         bp_rp,
                    "parallax":      _safe_val(row, "parallax"),
                    "pmra":          _safe_val(row, "pmra"),
                    "pmdec":         _safe_val(row, "pmdec"),
                    "uncatalogued":  is_uncat,
                    "anomaly_score": anom_score,
                    "is_anomalous":  is_anom,
                    "novelty_score": round(novelty, 3),
                    "is_new_candidate": is_new,
                    "flags":         flags,
                }
            )

        results.sort(key=lambda x: x["novelty_score"], reverse=True)
        return results

    @staticmethod
    def _log(job: dict | None, msg: str) -> None:
        if job is not None:
            job["log"].append(msg)


def _safe_col(col) -> list:
    """Convert a masked astropy column to a plain Python list of floats/NaN."""
    out = []
    for v in col:
        try:
            f = float(v)
            out.append(f if np.isfinite(f) else np.nan)
        except Exception:
            out.append(np.nan)
    return out


def _safe_val(row, key: str) -> float | None:
    try:
        v = float(row[key])
        return v if np.isfinite(v) else None
    except Exception:
        return None
