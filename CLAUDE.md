# finderX — notes for Claude Code sessions

finderX is a discovery terminal: four engines search public survey data
(TESS, Gaia DR3, AllWISE, MPC/JPL) and file **candidates** into a local
SQLite queue. The owner vets them in the UI. Your job in this repo is
usually one of: run surveys, triage the queue with analyst notes, or
improve the code.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest -q          # offline, ~20 s
```

Data lives in `data/` (git-ignored): `data/finderx.db` plus cached
reference catalogs in `data/cache/`. Override with `FINDERX_DATA=/path`.

## Running the compute

Everything the UI does is available headless:

```bash
.venv/bin/python -m finderx patrol --rounds 10 --n 40      # blind TESS survey (~1 star/s)
.venv/bin/python -m finderx scan transit --tic 261136679   # one star, newest 3 sectors
.venv/bin/python -m finderx scan transit --sector 100 --n 60
.venv/bin/python -m finderx scan stellar --name "NGC 2516" --radius 1
.venv/bin/python -m finderx scan galaxy --ra 54 --dec -62.5 --radius 0.5
.venv/bin/python -m finderx scan solar --mode neocp
```

Patrol skips stars already examined (`examined` table) and every TIC that
is a known TOI, CTOI, confirmed-planet host or TESS-EB-catalog star.
If the owner's server is running, CLI results land in the same database
and appear in their queue on the next refresh.

## Triage protocol

```bash
.venv/bin/python -m finderx queue --status new --json     # what needs looking at
.venv/bin/python -m finderx show FXT-0012                 # full dossier
.venv/bin/python -m finderx note FXT-0012 "…"             # your analyst note
```

- **Never vote** (`vote … confirm|reject`) unless the owner asks you to.
  Votes are theirs; that is the point of the project. Use `note`.
- A useful note says what the vetting numbers mean for *this* object, e.g.
  "Odd/even 0.8σ, no secondary, centroid 1.2σ, duration matches a K
  dwarf. But a G=13.1 Gaia neighbour 9″ away could produce it at 4%
  eclipse depth — ground-based seeing-limited photometry would settle it."
- Re-run a promising star with more sectors before writing it up:
  `scan transit --tic <id> --sectors 4`.
- Check ExoFOP (`https://exofop.ipac.caltech.edu/tess/target.php?id=<TIC>`)
  for anything the bulk catalogs missed.

## Code map

- `finderx/lightcurve.py` – MAST product discovery, FITS loading, detrending
- `finderx/analysis.py` – BLS transit search + vetting tests, Lomb-Scargle + variable typing (pure, unit-tested)
- `finderx/engines/` – `transit`, `stellar`, `galaxy`, `solar`; each is `run(ctx) -> dict`
- `finderx/jobs.py` – job threads, cancellation, event bus (SSE + CLI printer)
- `finderx/db.py`, `finderx/report.py`, `finderx/server.py`, `finderx/__main__.py`
- `finderx/web/` – the terminal UI (vanilla ES modules, no build step)

Engines talk to the outside world only through `JobContext`
(`log`, `stage`, `progress`, `candidate`, `result`, `mark`). New engines
register in `finderx/engines/__init__.py` with a `STAGES` list.

Thresholds live in `finderx/config.py`. If you change detection logic,
add a synthetic case to `tests/test_analysis.py` first.
