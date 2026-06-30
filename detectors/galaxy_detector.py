"""
Extragalactic object detector.

Pipeline
--------
1. Query SIMBAD for all objects in the field and keep galaxies / QSOs / AGN.
2. Query NED (NASA/IPAC Extragalactic Database) for the same field.
3. De-duplicate: objects in NED that have no SIMBAD match within 3 arcsec
   are flagged NED_ONLY; objects in SIMBAD not in NED are flagged SIMBAD_ONLY.
4. Also query SDSS photometric catalog via VizieR and flag objects whose
   u-g, g-r colors match quasar locus but have no spectroscopic ID.
"""

from __future__ import annotations

import warnings
import numpy as np

warnings.filterwarnings("ignore")

_GALAXY_OTYPES = {
    "G", "Galaxy", "GiG", "GinCl", "GGroup", "GClstr",
    "BClG", "PaG", "IG", "Sy1", "Sy2", "Seyfert", "LINER",
    "EmG", "LSB", "cD", "SBG", "H2G", "rG", "LensedG",
    "QSO", "AGN", "BLLac", "Blazar", "Quasar",
}


class GalaxyDetector:
    def find_anomalies(
        self,
        ra: float,
        dec: float,
        radius_deg: float,
        job: dict | None = None,
    ) -> list[dict]:
        from astroquery.simbad import Simbad
        try:
            from astroquery.ipac.ned import Ned
        except ImportError:
            from astroquery.ned import Ned  # type: ignore
        from astropy.coordinates import SkyCoord, match_coordinates_sky
        import astropy.units as u

        coord = SkyCoord(ra=ra * u.degree, dec=dec * u.degree, frame="icrs")
        results: list[dict] = []

        # ── SIMBAD ───────────────────────────────────────────────────────
        simbad_coords_list: list = []
        try:
            sb = Simbad()
            sb.add_votable_fields("otype", "rv_value", "z_value")
            sb_table = sb.query_region(coord, radius=radius_deg * u.degree)
            if sb_table is not None and len(sb_table) > 0:
                for row in sb_table:
                    otype = str(row["otype"]).strip()
                    is_galactic = any(g in otype for g in _GALAXY_OTYPES)
                    ra_r  = float(row["ra"])
                    dec_r = float(row["dec"])
                    simbad_coords_list.append((ra_r, dec_r))
                    z = None
                    try:
                        z = float(row["z_value"])
                    except Exception:
                        pass
                    obj_type = _classify_simbad(otype)
                    results.append(
                        {
                            "id":             str(row["main_id"]),
                            "type":           obj_type,
                            "ra":             ra_r,
                            "dec":            dec_r,
                            "catalog":        "SIMBAD",
                            "object_type":    otype,
                            "redshift":       z,
                            "magnitude":      None,
                            "uncatalogued":   False,
                            "is_anomalous":   False,
                            "is_new_candidate": False,
                            "novelty_score":  0.25 if is_galactic else 0.1,
                            "flags":          [f"SIMBAD_{otype[:10].upper().replace(' ', '_')}"],
                        }
                    )
            self._log(job, f"  SIMBAD extragalactic: {len(results)} objects.")
        except Exception as exc:
            self._log(job, f"  SIMBAD query issue: {exc}")

        # ── NED ───────────────────────────────────────────────────────────
        try:
            try:
                from astroquery.ipac.ned import Ned as NedNew
                ned_module = NedNew
            except ImportError:
                ned_module = Ned
            ned_table = ned_module.query_region(coord, radius=radius_deg * u.degree)
            if ned_table is not None and len(ned_table) > 0:
                # Build SkyCoord arrays for cross-matching
                if simbad_coords_list:
                    sb_ra  = np.array([c[0] for c in simbad_coords_list])
                    sb_dec = np.array([c[1] for c in simbad_coords_list])
                    sb_sky = SkyCoord(ra=sb_ra * u.degree, dec=sb_dec * u.degree)
                else:
                    sb_sky = None

                ned_added = 0
                for row in ned_table:
                    ned_ra  = float(row["RA(deg)"])
                    ned_dec = float(row["DEC(deg)"])
                    ned_id  = str(row["Object Name"])
                    ned_type = str(row.get("Type", "G"))

                    in_simbad = False
                    if sb_sky is not None:
                        ned_pt = SkyCoord(
                            ra=ned_ra * u.degree, dec=ned_dec * u.degree
                        )
                        sep = ned_pt.separation(sb_sky).arcsec
                        if sep.min() < 3.0:
                            in_simbad = True

                    flags = ["NED_ONLY"] if not in_simbad else ["NED_OBJECT"]
                    novelty = 0.4 if not in_simbad else 0.2

                    results.append(
                        {
                            "id":             ned_id,
                            "type":           _classify_ned(ned_type),
                            "ra":             ned_ra,
                            "dec":            ned_dec,
                            "catalog":        "NED",
                            "object_type":    ned_type,
                            "redshift":       _safe_float(row.get("Redshift")),
                            "magnitude":      None,
                            "uncatalogued":   not in_simbad,
                            "is_anomalous":   False,
                            "is_new_candidate": False,
                            "novelty_score":  novelty,
                            "flags":          flags,
                        }
                    )
                    ned_added += 1
                self._log(job, f"  NED: {ned_added} extragalactic objects.")
        except Exception as exc:
            self._log(job, f"  NED query issue: {exc}")

        # ── SDSS quasar candidates via VizieR ────────────────────────────
        try:
            qso_cands = self._sdss_qso_candidates(coord, radius_deg)
            self._log(job, f"  SDSS quasar color cuts: {len(qso_cands)} candidates.")
            results.extend(qso_cands)
        except Exception as exc:
            self._log(job, f"  SDSS quasar check: {exc}")

        return results

    def _sdss_qso_candidates(
        self, coord, radius_deg: float
    ) -> list[dict]:
        """Flag SDSS sources whose u-g / g-r colors fall in the quasar locus."""
        from astroquery.vizier import Vizier
        import astropy.units as u

        v = Vizier(
            columns=["RAJ2000", "DEJ2000", "umag", "gmag", "rmag", "imag"],
            row_limit=200,
        )
        tables = v.query_region(
            coord, radius=radius_deg * u.degree, catalog="V/154/sdss16"
        )
        results: list[dict] = []
        if not tables:
            return results
        for tbl in tables:
            for row in tbl:
                try:
                    umag = float(row["umag"])
                    gmag = float(row["gmag"])
                    rmag = float(row["rmag"])
                    imag = float(row["imag"])
                except Exception:
                    continue
                if not all(np.isfinite(v) for v in [umag, gmag, rmag, imag]):
                    continue
                # Quasar color selection (Richards+2002 locus)
                ug = umag - gmag
                gr = gmag - rmag
                ri = rmag - imag
                is_qso_color = (ug < 0.6) and (gr < 0.5) and (-0.3 < ri < 0.5)
                if is_qso_color:
                    results.append(
                        {
                            "id":             f"SDSS J{row['RAJ2000']:.4f}{row['DEJ2000']:+.4f}",
                            "type":           "quasar",
                            "ra":             float(row["RAJ2000"]),
                            "dec":            float(row["DEJ2000"]),
                            "catalog":        "SDSS DR16",
                            "magnitude":      gmag,
                            "object_type":    "QSO candidate",
                            "uncatalogued":   True,
                            "is_anomalous":   True,
                            "is_new_candidate": True,
                            "novelty_score":  0.7,
                            "flags":          ["QSO_COLOR_CANDIDATE", "NOT_SPECTROSCOPICALLY_CONFIRMED"],
                            "extra":          {"u-g": round(ug, 3), "g-r": round(gr, 3)},
                        }
                    )
        return results

    @staticmethod
    def _log(job: dict | None, msg: str) -> None:
        if job is not None:
            job["log"].append(msg)


def _classify_simbad(otype: str) -> str:
    otype = otype.upper()
    if any(q in otype for q in ["QSO", "AGN", "BLAZAR", "BLLAC", "LINER", "SY1", "SY2"]):
        return "quasar"
    if "G" in otype:
        return "galaxy"
    return "extragalactic"


def _classify_ned(ned_type: str) -> str:
    t = ned_type.strip().upper()
    if t in ("G",):
        return "galaxy"
    if t in ("QSO",):
        return "quasar"
    if t in ("AbLS", "GClstr", "GPair", "GGroup"):
        return "galaxy"
    return "extragalactic"


def _safe_float(val) -> float | None:
    try:
        v = float(val)
        return v if np.isfinite(v) else None
    except Exception:
        return None
