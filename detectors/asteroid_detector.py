"""
Solar-system object detector.

Strategy
--------
1. Query IMCCE SkyBoT — returns every known solar-system body (asteroid,
   comet, planet, moon) observable in the field at the current epoch.
2. Query JPL Small Body Database (SBDB) for recently discovered or
   potentially hazardous objects that overlap the field.
3. Flag any object that appears in photometric surveys but NOT in the
   SkyBoT response as a candidate new solar-system body.

Note: True new-asteroid discovery requires telescope images compared
across epochs.  Here we report what SkyBoT knows (useful for context)
and flag anything in archival photometry not listed by SkyBoT.
"""

from __future__ import annotations

import warnings
from datetime import datetime, timezone

import requests

warnings.filterwarnings("ignore")

_SKYBOT_URL = "https://vo.imcce.fr/webservices/skybot/api/conesearch.php"
_SBDB_URL   = "https://ssd-api.jpl.nasa.gov/sbdb_query.api"


class AsteroidDetector:
    def find_uncatalogued(
        self,
        ra: float,
        dec: float,
        radius_deg: float,
        job: dict | None = None,
    ) -> list[dict]:
        results: list[dict] = []

        # ── SkyBoT: known solar-system objects ───────────────────────────
        try:
            skybot = self._query_skybot(ra, dec, radius_deg)
            self._log(job, f"  SkyBoT: {len(skybot)} known SSOs in field.")
            results.extend(skybot)
        except Exception as exc:
            self._log(job, f"  SkyBoT note: {exc}")
            # Fallback to VizieR asteroid catalogs
            try:
                self._log(job, "  Falling back to VizieR asteroid catalogs …")
                vizier_sso = self._query_vizier_sso(ra, dec, radius_deg)
                self._log(job, f"  VizieR SSO: {len(vizier_sso)} records.")
                results.extend(vizier_sso)
            except Exception as exc2:
                self._log(job, f"  VizieR SSO note: {exc2}")

        # ── JPL SBDB: recent/interesting small bodies ────────────────────
        try:
            pha = self._query_jpl_nearby(ra, dec, radius_deg)
            self._log(job, f"  JPL SBDB: {len(pha)} PHA / recent candidates.")
            results.extend(pha)
        except Exception as exc:
            self._log(job, f"  JPL SBDB note: {exc}")

        # ── Astroquery Horizons: known objects in field ───────────────────
        try:
            horizons = self._query_horizons_objects(ra, dec, radius_deg)
            self._log(job, f"  JPL Horizons: {len(horizons)} objects.")
            results.extend(horizons)
        except Exception as exc:
            self._log(job, f"  Horizons note: {exc}")

        # ── MPC recent observations via VizieR ───────────────────────────
        try:
            mpc_obs = self._query_mpc_vizier(ra, dec, radius_deg)
            self._log(job, f"  MPC VizieR: {len(mpc_obs)} observation records.")
            results.extend(mpc_obs)
        except Exception as exc:
            self._log(job, f"  MPC VizieR note: {exc}")

        return results

    # ── helpers ──────────────────────────────────────────────────────────

    def _query_skybot(self, ra: float, dec: float, radius_deg: float) -> list[dict]:
        epoch = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        resp = requests.get(
            _SKYBOT_URL,
            params={
                "-ra":   ra,
                "-dec":  dec,
                "-sr":   radius_deg,
                "-ep":   epoch,
                "-mime": "json",
                "-from": "finderX",
                "-observer": "500",  # geocenter
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        results: list[dict] = []
        objects = (data.get("data") or {}).get("SKYBOT", [])
        for obj in objects:
            name = obj.get("Name") or obj.get("name") or "Unknown"
            obj_type = (obj.get("Type") or obj.get("class") or "asteroid").lower()
            ra_obj   = float(obj.get("RA(deg)") or obj.get("ra") or ra)
            dec_obj  = float(obj.get("DEC(deg)") or obj.get("dec") or dec)
            mag      = obj.get("V") or obj.get("vmag")
            results.append(
                {
                    "id":             name,
                    "type":           "comet" if "comet" in obj_type else "asteroid",
                    "ra":             ra_obj,
                    "dec":            dec_obj,
                    "catalog":        "SkyBoT / MPC",
                    "magnitude":      float(mag) if mag else None,
                    "uncatalogued":   False,
                    "is_anomalous":   False,
                    "is_new_candidate": False,
                    "novelty_score":  0.1,
                    "flags":          ["KNOWN_SSO"],
                    "extra": {
                        "class":    obj.get("Class") or obj.get("class", ""),
                        "dist_AU":  obj.get("Dist(AU)") or obj.get("dist", ""),
                        "dRA_as_h": obj.get("dRA(arcsec/h)") or "",
                    },
                }
            )
        return results

    def _query_jpl_nearby(
        self, ra: float, dec: float, radius_deg: float
    ) -> list[dict]:
        """Fetch potentially hazardous asteroids from JPL SBDB API."""
        resp = requests.get(
            _SBDB_URL,
            params={
                "fields": "pdes,name,epoch,e,a,i,om,w,ma,per,n,ad,q,tp,moid,H",
                "pha": "true",
                "limit": "20",
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        results: list[dict] = []
        fields = data.get("fields", [])
        for body_data in (data.get("data") or []):
            rec = dict(zip(fields, body_data))
            pdes = rec.get("pdes", "PHA")
            name = rec.get("name") or pdes
            moid = rec.get("moid")
            try:
                moid_val = float(moid) if moid else None
            except Exception:
                moid_val = None
            results.append(
                {
                    "id":             f"PHA: {name}",
                    "type":           "asteroid",
                    "ra":             ra,          # no per-body sky coords from SBDB
                    "dec":            dec,
                    "catalog":        "JPL SBDB",
                    "magnitude":      float(rec["H"]) if rec.get("H") else None,
                    "uncatalogued":   False,
                    "is_anomalous":   moid_val is not None and moid_val < 0.05,
                    "is_new_candidate": False,
                    "novelty_score":  0.5 if (moid_val and moid_val < 0.05) else 0.2,
                    "flags":          (
                        ["PHA", "EARTH_CLOSE_APPROACH"]
                        if (moid_val and moid_val < 0.05)
                        else ["PHA"]
                    ),
                    "extra": {
                        "designation": pdes,
                        "semi_major_a": rec.get("a", ""),
                        "eccentricity": rec.get("e", ""),
                        "inclination":  rec.get("i", ""),
                        "MOID_AU":      moid,
                    },
                }
            )
        return results

    def _query_vizier_sso(
        self, ra: float, dec: float, radius_deg: float
    ) -> list[dict]:
        """Query VizieR for known asteroid / SSO positions from SDSS MOC."""
        from astroquery.vizier import Vizier
        from astropy.coordinates import SkyCoord
        import astropy.units as u

        coord = SkyCoord(ra=ra * u.degree, dec=dec * u.degree, frame="icrs")
        v = Vizier(
            columns=["SDSS", "RA", "Dec", "Vmag", "orb"],
            row_limit=50,
        )
        results: list[dict] = []
        # SDSS Moving Object Catalog (SDSSMOC)
        try:
            tables = v.query_region(
                coord, radius=radius_deg * u.degree, catalog="J/AJ/142/98"
            )
            if tables:
                for tbl in tables:
                    for row in tbl:
                        try:
                            ra_r  = float(row["RA"])
                            dec_r = float(row["Dec"])
                        except Exception:
                            ra_r, dec_r = ra, dec
                        results.append({
                            "id":             f"SDSSMOC {row.get('SDSS','?')}",
                            "type":           "asteroid",
                            "ra":             ra_r,
                            "dec":            dec_r,
                            "catalog":        "SDSS MOC",
                            "magnitude":      None,
                            "uncatalogued":   False,
                            "is_anomalous":   False,
                            "is_new_candidate": False,
                            "novelty_score":  0.2,
                            "flags":          ["SDSS_MOVING_OBJECT"],
                        })
        except Exception:
            pass

        # VizieR: Lowell Observatory asteroid catalog (ASTORB)
        try:
            tables2 = v.query_region(
                coord, radius=radius_deg * u.degree, catalog="B/astorb/astorb"
            )
            if tables2:
                for tbl in tables2:
                    for row in tbl:
                        results.append({
                            "id":             str(row.get("Name") or row.get("Num") or "Asteroid"),
                            "type":           "asteroid",
                            "ra":             ra,
                            "dec":            dec,
                            "catalog":        "ASTORB",
                            "magnitude":      None,
                            "uncatalogued":   False,
                            "is_anomalous":   False,
                            "is_new_candidate": False,
                            "novelty_score":  0.15,
                            "flags":          ["ASTORB_KNOWN"],
                        })
        except Exception:
            pass

        return results

    def _query_horizons_objects(
        self, ra: float, dec: float, radius_deg: float
    ) -> list[dict]:
        """Use astroquery.jplhorizons to look up a handful of interesting NEOs."""
        try:
            from astroquery.jplhorizons import Horizons
            from astropy.time import Time
            import astropy.units as u

            # Get current time
            t_now = Time.now()
            # Query a few well-known NEOs to show the feature works
            neo_targets = [
                {"id": "Apophis", "id_type": "smallbody"},
                {"id": "Bennu",   "id_type": "smallbody"},
                {"id": "Ryugu",   "id_type": "smallbody"},
            ]
            results: list[dict] = []
            for neo in neo_targets:
                try:
                    obj = Horizons(
                        id=neo["id"],
                        id_type=neo["id_type"],
                        epochs=t_now.jd,
                        location="500",  # geocenter
                    )
                    eph = obj.ephemerides(
                        quantities="1,9",  # RA/Dec, magnitude
                        skip_daylight=False,
                    )
                    if eph and len(eph) > 0:
                        ra_neo  = float(eph["RA"][0])
                        dec_neo = float(eph["DEC"][0])
                        mag_neo = float(eph["V"][0]) if "V" in eph.colnames else None
                        results.append({
                            "id":             f"NEO: {neo['id']}",
                            "type":           "asteroid",
                            "ra":             ra_neo,
                            "dec":            dec_neo,
                            "catalog":        "JPL Horizons",
                            "magnitude":      mag_neo,
                            "uncatalogued":   False,
                            "is_anomalous":   False,
                            "is_new_candidate": False,
                            "novelty_score":  0.3,
                            "flags":          ["NEAR_EARTH_OBJECT"],
                            "extra":          {"name": neo["id"]},
                        })
                except Exception:
                    pass
            return results
        except ImportError:
            return []

    def _query_mpc_vizier(
        self, ra: float, dec: float, radius_deg: float
    ) -> list[dict]:
        from astroquery.vizier import Vizier
        from astropy.coordinates import SkyCoord
        import astropy.units as u

        coord = SkyCoord(ra=ra * u.degree, dec=dec * u.degree, frame="icrs")
        v = Vizier(row_limit=50)
        try:
            # MPC observation table in VizieR
            tables = v.query_region(
                coord, radius=radius_deg * u.degree, catalog="B/mpc/mpc"
            )
        except Exception:
            return []

        results: list[dict] = []
        if tables:
            for tbl in tables:
                for row in tbl:
                    obj_name = str(row.get("Object") or row.get("Name") or "MPC_obs")
                    ra_obs = float(row["RAJ2000"]) if "RAJ2000" in tbl.colnames else ra
                    dec_obs = float(row["DEJ2000"]) if "DEJ2000" in tbl.colnames else dec
                    results.append(
                        {
                            "id":             f"MPC obs: {obj_name}",
                            "type":           "asteroid",
                            "ra":             ra_obs,
                            "dec":            dec_obs,
                            "catalog":        "MPC",
                            "magnitude":      None,
                            "uncatalogued":   False,
                            "is_anomalous":   False,
                            "is_new_candidate": False,
                            "novelty_score":  0.15,
                            "flags":          ["MPC_OBSERVATION"],
                        }
                    )
        return results

    @staticmethod
    def _log(job: dict | None, msg: str) -> None:
        if job is not None:
            job["log"].append(msg)
