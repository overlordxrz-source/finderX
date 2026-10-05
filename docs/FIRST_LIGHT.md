# First light — 5 October 2026

The first real run of the rebuilt finderX, done while building it. Every
number here came from live archive data. The "Analyst notes" are mine
(Claude's), written with `python -m finderx note`. None of these
candidates has been voted on: that part is yours.

Reproduce any of them: `python -m finderx scan transit --tic <TIC> --sectors 4`.

## What ran

| | |
|---|---|
| TESS stars searched (blind patrol, QLP FFI light curves, sectors 99–104) | **809** |
| Signals logged | 210 (all engines) |
| Planet-like signals that reached the multi-sector re-check | 10 → **1 survived** |
| New eclipsing binaries (not in VSX, TESS EB catalogue or Gaia DR3 variability) | 11 |
| New periodic variables | 9 |
| Known objects recovered | π Men c (pipeline check), NR Ara (see below) |
| Spacecraft systematics caught by the common-mode register | 3 |
| Gaia fields censused (STARS) | 3 · 14 candidates |
| Deep fields (DEEP) | 2 · 151 extragalactic candidates nobody has classified |

Patrol speed on this machine: about 2.3 stars per second.

## The ones worth your time

### FXT-0154 · TIC 369016458 · hot-Jupiter candidate (or blended binary)

- P = **5.05208 d**, depth 7.5 ppt, duration 2.85 h, ~14 R⊕ around a
  1.5 R☉ G subgiant (Tmag 13.35, RA 262.51497 Dec −52.61810)
- First seen in one sector (S103). The automatic re-check over
  S66 · S93 · S103 · S104 recovered it: **16 transits over three years**,
  SNR 19, SDE 36. Odd and even depths agree (0.2σ).
- Concerns: a 4.9σ hint of a secondary eclipse, a 3.9σ centroid shift,
  and two Gaia neighbours (7.3″, 22.7″) bright enough to fake it as 6–8%
  eclipsing binaries.
- **No TOI, CTOI or ExoFOP entry** as of today.
- Next predicted transits (UTC, ±~1 h): **2026-10-08 22:55**,
  2026-10-14 00:10, 2026-10-19 01:25. The settling test is
  seeing-limited photometry of the field during one of them.

### FXV-0128 · NR Ara · a VSX correction you can submit

VSX lists NR Ara as an RR Lyrae star with **no period**. The TESS
light curve of a neighbouring TIC target (NR Ara is 20″ away, inside the
aperture) shows a **contact-binary** light curve with P = 0.50870 d. Gaia
DR3 independently classifies NR Ara (Gaia DR3 5913186870072785920) as
eclipsing with P = 0.50885 d. Suggested VSX revision: type EW/EB,
P = 0.50885 d, citing Gaia DR3 and TESS S104.

### New eclipsing binaries (none in VSX, the TESS EB catalogue or Gaia DR3 variability within 21″)

| ID | TIC | Period (d) | Notes |
|---|---|---|---|
| FXT-0141 | 455484591 | 2.85589 | textbook detached EB, 5.9% + 5% eclipses, SNR 90; centroid shift → check neighbours |
| FXT-0152 | 69183710 | 24.0131 | long-period detached EB, eclipses in four sectors 2021–2026 |
| FXT-0153 | 382312512 | 0.58423 | contact/ellipsoidal, 134 cycles in four sectors |
| FXT-0156 | 415317302 | 0.73125 | four sectors, strong secondary; may be a blended neighbour |
| FXT-0142 | 295255271 | 18.9487 | odd/even 8.9σ → orbit is twice the 9.474 d BLS period |
| FXT-0145, -0157, -0140, -0147, -0151, -0124 | | | see the queue |

These are the most realistic "first discoveries": AAVSO VSX accepts new
variables from anyone. Check the centroid flag and Gaia neighbours first so
the submission names the right star.

### Stellar census highlights

- **FXS-0013** (Gaia DR3 6567890793935555072): G = 14.4 halo star at
  604 pc moving 404 km/s across the sky, RV −160 km/s, ~344 km/s
  Galactocentric. **No SIMBAD entry.** Bright enough for a spectrum.
- **FXS-0009** (LEHPM 4281): v_tan = 620 km/s at 252 pc, ~1 mag under the
  M-dwarf sequence: a metal-poor halo subdwarf. A radial velocity would
  show whether it is near the Galactic escape speed.
- **FXS-0012**: an M dwarf at 97 pc with RUWE 6.7 (strong astrometric
  wobble), no SIMBAD entry, and no Gaia DR3 orbit solution. Gaia DR4
  should give its companion's orbit.

### Deep fields

Two southern fields (RA 36.2 Dec −32.9 and RA 54 Dec −62.5) gave
**102 Gaia DR3 quasar candidates** with no classification in Milliquas,
Quaia, SIMBAD or NED. 95 are fainter than G = 20, beyond Quaia's limit,
which is why the big catalogues stop short of them. 91 show no measurable
parallax or proper motion. **29 also have WISE AGN colours**: those are
the strongest (filter the queue to DEEP and sort by score). There are also
27 galaxy candidates and 22 dust-obscured, mid-IR-only AGN candidates.
Gaia redshifts for these faint sources are flagged and shown with a "?".

## What the run taught the pipeline

Each of these came from looking at real candidates and is now built in:

1. **Common-mode register.** Sectors 103 and 104 produced "planets" whose
   transits fell at the same instants on unrelated stars. Every event now
   goes into a per-sector register, and matches are filed as systematics,
   retroactively too.
2. **Edge trimming and spike clipping.** Ramps at segment edges and
   single-cadence drops made fake transits.
3. **Automatic multi-sector re-check.** Single-sector planet candidates
   are re-searched with up to three more sectors before reaching your
   queue. In this run that turned 10 into 1 survivor and 3 eclipsing
   binaries at their true periods.
4. **Quaia, Gaia DR3 orbits and Gaia DR3 eclipsing-binary periods** were
   added to the known-object checks after they caught things the first
   catalogue set missed.
