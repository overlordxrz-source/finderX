import threading
import uuid
import traceback
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# In-memory job store: job_id -> job dict
jobs: dict[str, dict] = {}


def make_job() -> dict:
    return {
        "status": "running",
        "progress": 0,
        "results": [],
        "stats": {},
        "log": [],
        "started_at": datetime.utcnow().isoformat(),
        "error": None,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/resolve", methods=["POST"])
def resolve_name():
    """Resolve an object name to RA/Dec using SIMBAD."""
    name = (request.json or {}).get("name", "").strip()
    if not name:
        return jsonify({"error": "No name provided"}), 400
    try:
        from astroquery.simbad import Simbad
        result = Simbad.query_object(name)
        if result is None or len(result) == 0:
            return jsonify({"error": f"'{name}' not found in SIMBAD"}), 404
        ra = float(result["ra"][0])
        dec = float(result["dec"][0])
        return jsonify({"ra": ra, "dec": dec, "name": name})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/detect", methods=["POST"])
def start_detection():
    """Launch an async detection job and return its ID."""
    data = request.json or {}
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = make_job()

    def run():
        job = jobs[job_id]
        try:
            ra     = float(data.get("ra", 83.82))
            dec    = float(data.get("dec", -5.39))
            radius = float(data.get("radius", 0.25))
            mode   = data.get("type", "all")

            all_results = []

            # ── Stellar (Gaia + SIMBAD + ML) ──────────────────────────────
            if mode in ("all", "stars", "stellar"):
                job["log"].append("🔭 Querying Gaia DR3 — this may take 15-30s …")
                job["progress"] = 5
                from detectors.stellar_detector import StellarDetector
                stars = StellarDetector().find_anomalies(ra, dec, radius, job)
                all_results.extend(stars)
                job["progress"] = 40
                job["log"].append(f"✅ Stellar scan complete — {len(stars)} objects")

            # ── Solar-system (SkyBoT + MPC) ───────────────────────────────
            if mode in ("all", "asteroids", "solar_system"):
                job["log"].append("☄️  Scanning for solar-system objects via SkyBoT …")
                job["progress"] = 45
                from detectors.asteroid_detector import AsteroidDetector
                asteroids = AsteroidDetector().find_uncatalogued(ra, dec, radius, job)
                all_results.extend(asteroids)
                job["progress"] = 70
                job["log"].append(f"✅ Solar-system scan complete — {len(asteroids)} objects")

            # ── Extragalactic (SIMBAD galaxies + NED) ────────────────────
            if mode in ("all", "galaxies", "extragalactic"):
                job["log"].append("🌌 Querying NED + SIMBAD for galaxies & quasars …")
                job["progress"] = 75
                from detectors.galaxy_detector import GalaxyDetector
                galaxies = GalaxyDetector().find_anomalies(ra, dec, radius, job)
                all_results.extend(galaxies)
                job["progress"] = 95
                job["log"].append(f"✅ Extragalactic scan complete — {len(galaxies)} objects")

            # Sort by novelty descending
            all_results.sort(key=lambda x: x.get("novelty_score", 0), reverse=True)

            uncatalogued   = [r for r in all_results if r.get("uncatalogued")]
            anomalous      = [r for r in all_results if r.get("is_anomalous")]
            new_candidates = [r for r in all_results if r.get("is_new_candidate")]

            job["results"] = all_results[:200]
            job["stats"] = {
                "total":         len(all_results),
                "uncatalogued":  len(uncatalogued),
                "anomalous":     len(anomalous),
                "new_candidates": len(new_candidates),
            }
            job["status"]   = "complete"
            job["progress"] = 100
            job["log"].append(
                f"🎯 Done! {len(new_candidates)} new candidates, "
                f"{len(uncatalogued)} uncatalogued, {len(anomalous)} ML-anomalous."
            )

        except Exception as exc:
            job["status"] = "error"
            job["error"]  = str(exc)
            job["log"].append(f"❌ Error: {exc}")
            job["log"].append(traceback.format_exc())

    threading.Thread(target=run, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>")
def get_status(job_id):
    if job_id not in jobs:
        return jsonify({"error": "Job not found"}), 404
    job = jobs[job_id]
    return jsonify({
        "status":   job["status"],
        "progress": job["progress"],
        "log":      job["log"],
        "stats":    job.get("stats", {}),
        "results":  job.get("results", [])[:150],
        "error":    job.get("error"),
    })


if __name__ == "__main__":
    app.run(debug=False, port=5050, use_reloader=False)
