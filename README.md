# finderX

> A real-time cosmic discovery engine. Hunt for uncatalogued stars, anomalous objects, asteroids, and quasar candidates using live astronomical survey data and ML — all from a localhost UI.

![Python](https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square)
![Flask](https://img.shields.io/badge/flask-3.x-lightgrey?style=flat-square)
![Gaia DR3](https://img.shields.io/badge/data-Gaia%20DR3-orange?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)

---

## What is this

finderX is a local web app that lets you point at any region of the sky and run a multi-source detection pipeline to find objects that are:

- **In Gaia DR3 but not in SIMBAD** — real detections with no published classification
- **Statistically anomalous** — outliers in photometric/astrometric feature space (ML)
- **High proper-motion** — potential nearby stars not in any name catalog
- **Photometric quasar candidates** — color-selected from SDSS but unconfirmed spectroscopically
- **Known solar-system objects** — asteroids, comets, NEOs passing through the field

The goal is genuinely scientific: Gaia DR3 contains ~1.8 billion sources while SIMBAD classifies ~15 million objects. Every 0.25° field in the sky hides dozens of Gaia sources with no published identity.

---

## Architecture

```
finderX/
├── app.py                      # Flask API server + async job queue
├── detectors/
│   ├── stellar_detector.py     # Gaia DR3 × SIMBAD × IsolationForest
│   ├── asteroid_detector.py    # SkyBoT / JPL Horizons / VizieR SDSS MOC
│   └── galaxy_detector.py      # SIMBAD extragalactic × NED × SDSS QSO colors
├── templates/
│   └── index.html              # Full UI — Aladin Lite + dark space theme
├── requirements.txt
└── start.sh
```

### Backend

Flask serves the frontend and exposes three API routes:

| Route | Description |
|-------|-------------|
| `GET /` | Serves the sky UI |
| `POST /api/resolve` | Resolves an object name to RA/Dec via SIMBAD |
| `POST /api/detect` | Launches an async detection job, returns `job_id` |
| `GET /api/status/<job_id>` | Polls job progress, log lines, and results |

Detection jobs run in background threads so the frontend can poll without blocking. Each job produces a list of result objects sorted by **novelty score** descending.

### Detection pipeline

#### Stellar (`stellar_detector.py`)

```
Gaia DR3 cone search (≤500 sources)
         │
         ├── SIMBAD cone search (same field)
         │       └── spatial cross-match @ 2 arcsec
         │           ├── matched → known object
         │           └── unmatched → NOT_IN_SIMBAD ✓
         │
         └── Feature matrix: [parallax, pmRA, pmDec, G_mag, BP-RP]
                 └── IsolationForest (contamination=0.1, n_estimators=150)
                         └── decision_function < -0.05 → ML_ANOMALY ✓
                         └── |pmRA²+pmDec²|^0.5 > 100 mas/yr → HIGH_PROPER_MOTION ✓
                         └── BP-RP < 0.2 → BLUE_OBJECT ✓
```

**Novelty score** = `1.0` (if NOT_IN_SIMBAD) + `|anomaly_score|` (if anomalous) + `0.3` (high PM) + `0.2` (blue)

#### Solar system (`asteroid_detector.py`)

1. **SkyBoT** (IMCCE) — cone search of all known solar-system bodies at current epoch
2. **JPL Horizons** (astroquery) — ephemerides for named NEOs (Apophis, Bennu, Ryugu …)
3. **JPL SBDB** — potentially hazardous asteroid catalog with MOID flagging
4. **SDSS Moving Object Catalog** via VizieR — archival asteroid detections
5. **ASTORB** via VizieR — Lowell Observatory asteroid catalog

#### Extragalactic (`galaxy_detector.py`)

1. **SIMBAD** — galaxy, QSO, Seyfert, AGN, BL Lac, LINER query
2. **NED** (NASA/IPAC Extragalactic Database) — region query; objects in NED but not SIMBAD flagged `NED_ONLY`
3. **SDSS DR16 quasar color selection** — Richards+2002 photometric cuts:
   - `u-g < 0.6`
   - `g-r < 0.5`
   - `-0.3 < r-i < 0.5`
   
   Sources passing these cuts without spectroscopic confirmation → `QSO_COLOR_CANDIDATE`

### Frontend

Single-page app served by Flask (`templates/index.html`):

- **Aladin Lite v3** — interactive sky viewer with 7 survey options (DSS2, 2MASS, SDSS, WISE IR, XMM X-ray, GALEX UV)
- **Control panel** — coordinate input, object-name resolver (SIMBAD), radius, object class selector
- **Async polling** — job status updates every 1.2s with live log stream
- **Result list** — sorted by novelty score; click any item to zoom Aladin to that object
- **Sky markers** — 4 Aladin catalogs overlaid: Normal (blue circles), Solar system (orange triangles), ML Anomaly (amber rhombs), New Candidates (green crosses)

### Data flow

```
Browser → POST /api/detect
              │
              └── Thread spawns
                      │
                      ├── StellarDetector.find_anomalies()
                      │       ├── astroquery.gaia  → Gaia TAP/ADQL
                      │       └── astroquery.simbad → SIMBAD CDS
                      │
                      ├── AsteroidDetector.find_uncatalogued()
                      │       ├── requests → vo.imcce.fr (SkyBoT)
                      │       ├── astroquery.jplhorizons → Horizons API
                      │       └── astroquery.vizier → VizieR CDS
                      │
                      └── GalaxyDetector.find_anomalies()
                              ├── astroquery.simbad
                              ├── astroquery.ipac.ned
                              └── astroquery.vizier → SDSS DR16

Browser polls GET /api/status/<job_id> → renders results
```

---

## Quick start

```bash
git clone https://github.com/overlordxrz-source/finderX
cd finderX

# Create virtualenv and install deps
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run
python app.py
# → http://localhost:5050
```

Or just:

```bash
./start.sh
```

---

## Usage

1. **Click the sky map** to set target coordinates — or type an object name and hit **Go** (resolves via SIMBAD)
2. Set **search radius** (0.1° for dense fields; 0.5° for open sky)
3. Select **object class** — All / Stars / Asteroids-Comets / Galaxies-QSOs
4. Hit **Start Detection**
5. Watch the live log; green-bordered results = new discovery candidates
6. Click any result to jump Aladin Lite directly to that position

### Interesting targets

| Target | RA | Dec | Why |
|--------|----|-----|-----|
| Orion Nebula | 83.82 | -5.39 | Dense stellar field — 40+ Gaia sources not in SIMBAD |
| Galactic center | 266.40 | -29.00 | Extreme density, hundreds of uncatalogued faint sources |
| M87 | 187.70 | 12.39 | AGN + surrounding galaxy/QSO population |
| Perseus Cluster | 49.95 | 41.51 | Galaxy cluster, many NED objects |
| LMC | 80.90 | -69.75 | Magellanic Cloud — active star formation |
| Coma Cluster | 194.90 | 27.98 | Dense galaxy cluster + background QSO candidates |

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `flask` + `flask-cors` | API server |
| `astroquery` | Unified interface to Gaia, SIMBAD, NED, VizieR, JPL Horizons, MPC |
| `astropy` | Sky coordinates, units, time |
| `scikit-learn` | IsolationForest anomaly detection |
| `numpy` + `pandas` | Feature matrix manipulation |
| `scipy` | Statistical support |
| `requests` | SkyBoT / JPL SBDB direct HTTP calls |

---

## Future goals

### Short term
- [ ] **Time-domain asteroid detection** — compare two epochs of Gaia observations for the same field to flag sources that moved (proper motion inconsistent with stellar background → asteroid candidate)
- [ ] **TESS light-curve integration** — pull TESS sectors for detected stars via `astroquery.mast`; flag sources with periodic or transient variability not in existing variable star catalogs
- [ ] **Export** — download results as CSV / FITS / VOTable for follow-up with real telescope scheduling tools (e.g. LCO, AAVSO)

### Medium term
- [ ] **Image-subtraction pipeline** — pull archival Pan-STARRS or SDSS images for a field at two epochs, subtract them, and flag residuals (the real asteroid/transient discovery method)
- [ ] **Spectral classification** — integrate SDSS spectra (via astroquery.sdss) for objects in the field to auto-classify detected outliers
- [ ] **Alert stream integration** — subscribe to ZTF (Zwicky Transient Facility) or ATLAS alert streams for real transient events; cross-match with uncatalogued Gaia sources in real time
- [ ] **Proper-motion catalog builder** — stack multi-epoch Gaia observations to build a local high-PM catalog for a user-defined sky region

### Long term
- [ ] **CNN-based image classifier** — train on Pan-STARRS / SDSS cutouts of known object types; apply to detected outlier stamps to predict class probability
- [ ] **LSST/Rubin Observatory integration** — when Rubin's alert broker goes live, pipe alerts into finderX for instant cross-matching against the Gaia uncatalogued source list
- [ ] **Citizen science layer** — flag top candidates with a simple vote interface so human reviewers can mark interesting objects for telescope follow-up
- [ ] **Automated MPC submission** — for confirmed new asteroid candidates (multi-epoch position measurements), auto-format observations for Minor Planet Center submission

---

## How real discoveries happen

Real uncatalogued objects found by finderX would need:
1. **Confirmation** — at least 3 independent position measurements across different nights
2. **Spectroscopy** — to determine object type (star, galaxy, QSO, asteroid)
3. **Reporting** — variable stars to AAVSO / VSX; asteroids to MPC; new galaxies/QSOs to NED or published paper

The objects flagged `NOT_IN_SIMBAD` in a typical finderX run are **real Gaia detections** that simply haven't been studied yet. The Milky Way contains an estimated 100–400 billion stars; Gaia has measured 1.8 billion of them; SIMBAD has classified about 15 million. The gap is where discoveries live.

---

## License

MIT
