# First light — 5 October 2026

The first real run of the rebuilt finderX, done while building it. Every
number here came from live archive data. The "Analyst notes" are mine
(Claude's), written with `python -m finderx note`. None of these
candidates has been voted on: that part is yours.

Reproduce any of them: `python -m finderx scan transit --tic <TIC> --sectors 4`,
then `python -m finderx pixels <ID>` to see which star dims.

## What ran

| | |
|---|---|
| TESS stars searched (blind patrol, QLP FFI light curves, sectors 99–104) | **809** |
| Signals logged | 210 (all engines) |
| Planet-like signals that reached the multi-sector re-check | 10 → **1 survived** |
| Eclipsing signals (incl. the planet candidate) | 13 → after the pixel check: **3 new binaries**, 7 catalogue corrections, 2 already known, 1 unresolved |
| New periodic variables | 9 |
| Known objects recovered | π Men c (pipeline check), NR Ara (see below) |
| Spacecraft systematics caught by the common-mode register | 3 (one turned out to be a real binary, FXT-0146) |
| Gaia fields censused (STARS) | 3 · 14 candidates |
| Deep fields (DEEP) | 2 · 151 extragalactic candidates nobody has classified |

Patrol speed on this machine: about 2.3 stars per second.

## The ones worth your time

### FXT-0146 · TIC 375895390 · a hot eclipsing binary with the wrong catalogue entry

- P = **5.13849 d**, hot host (Teff ≈ 14,500 K, Tmag 11), primary and
  secondary eclipses (secondary at 33σ).
- **The pixel check puts the dip on the target** in S100 and S101, and
  **Gaia DR3 epoch photometry from 2015–16 caught the target dimmer at
  three predicted eclipses**, ten years before TESS.
- VSX lists the star (Gaia DR3 5344333737486446208) as a δ Sct/γ Dor
  pulsator with P = 2.7802 d. Both the type and the period are wrong.
  **Suggested VSX revision: EA, P = 5.13849 d.**
- First filed as a spacecraft systematic from one sector. A four-sector
  re-scan cleared it.
- Depth: ~1.2% in a fixed TESScut aperture, unchanged across all seven
  sectors 2019–2026. The QLP light curve claims 7–14%; that is an
  aperture/background artefact, not a deepening eclipse.

### FXT-0154 · TIC 369016458 · the planet candidate that wasn't

The run's one surviving planet candidate (P = 5.05208 d, 16 transits over
three years, no TOI or CTOI) was resolved by the new pixel check. TESS
difference images in S103 and S104 put the dip on **Gaia DR3
5925053173201979776**, a G = 17.3 star 18″ away that would need an 18%
eclipse. Gaia DR3 epoch photometry independently caught that star
mid-eclipse (Δχ² 1372). Gaia and VSX list it with P = 0.252441 d, but
TESS sees eclipses every 5.05208 d in four sectors, so the short period is
a sampling alias. **Suggested VSX revision: P = 5.05208 d.**

### FXV-0128 · NR Ara · a VSX correction you can submit

VSX lists NR Ara as an RR Lyrae star with **no period**. The TESS
light curve of a neighbouring TIC target (NR Ara is 20″ away, inside the
aperture) shows a **contact-binary** light curve with P = 0.50870 d. Gaia
DR3 independently classifies NR Ara (Gaia DR3 5913186870072785920) as
eclipsing with P = 0.50885 d. Suggested VSX revision: type EW/EB,
P = 0.50885 d, citing Gaia DR3 and TESS S104.

### Eclipsing binaries, after the pixel check

TESS pixels are 21″ wide. In these crowded southern fields, **9 of the 13
eclipsing signals came from a neighbour 18–64″ away**, not the TIC target. The
table names the star that actually varies, and what the catalogues already
say about it (VSX + Gaia DR3 variability, matched on Gaia ID and period).

| ID | P orbit (d) | Star that varies | Catalogue status | What to submit |
|---|---|---|---|---|
| FXT-0141 | 2.85589 | TIC 455484591 (target) | none | **new EB** — detached, 5.9% + 5% eclipses, SNR 90 |
| FXT-0140 | 1.94919 | TIC 333351106 (target) | none | **new EB** — A-type host |
| FXT-0156 | 0.73125 | Gaia DR3 5881113050785828224 (G 13.6, 48″) | none | **new EB** at the Gaia position, not the TIC |
| FXT-0146 | 5.13849 | TIC 375895390 (target) | VSX: DSCT/GDOR, P 2.7802 | revision: EA, P 5.13849 |
| FXT-0154 | 5.05208 | Gaia DR3 5925053173201979776 (G 17.3, 18″) | Gaia/VSX P 0.252441 (alias) | revision: P 5.05208 |
| FXT-0145 | 4.84669 | Gaia DR3 5351271999459508224 (G 11.8, 42″) | Gaia ECL P 0.960418 | revision once a 2nd sector confirms; TESS folds to nothing at Gaia's period |
| FXT-0142 | 1.89494 | Gaia DR3 6431224934580345984 (G 11.3, 63″) | Gaia ECL P 0.947476 (½ orbit); VSX EA P 1.9048933 | revision: VSX period is 0.5% long |
| FXT-0152 | 24.0131 | Gaia DR3 5884400517473162496 (G 12.5, 26″) | Gaia: LPV, no period | revision: EA, P 24.0131 (a giant in an EB?) |
| FXT-0157 | 3.24722 | Gaia DR3 4036919274348877440 (G 13.5, 41″) | Gaia: DSCT/GDOR, no period | revision: eclipse period |
| FXT-0124 | 0.73673 | Gaia DR3 5940806430378615040 (G 14.5, 61″) | Gaia ECL P 0.736731; VSX VAR, no period | VSX: add type + period |
| FXT-0151 | 2.43528 | Gaia DR3 5867542530316566784 (G 13.5, 23″) | Gaia ECL P 2.435046 | nothing, already known |
| FXT-0153 | 0.58423 | Gaia DR3 6434251855728562432 (G 15.9, 43″) | Gaia ECL P 0.584256 | nothing, already known |
| FXT-0147 | 0.91486 | target or Gaia DR3 5864406658771597696 | — | unresolved (Δχ² 3); needs another sector |

AAVSO VSX accepts new variables and revisions from anyone. Always submit
the star in the "star that varies" column, at its Gaia position.

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
5. **Pixel check.** TESScut difference images plus a PSF fit at every Gaia
   star locate the star that dims. It moved 9 of 13 eclipsing signals,
   the planet candidate included, onto a neighbouring star.
6. **Source lookup.** The star the pixels blame is checked against VSX and
   Gaia DR3 on its own ID and period. In the first draft of this report
   four "new" binaries turned out to be already catalogued. Now that
   check runs automatically.
7. **Fundamental-period check.** A deep, short-period eclipse makes a broad
   BLS peak that the SDE normalisation flattens, so the search locked onto
   5× the period for FXT-0142 (and 2–3× in synthetic tests). Each detection
   is now tested at P/k on the epochs that P/k adds.
8. **Archival depth fit.** Gaia/PS1 points inside predicted eclipses are
   compared with the TESS eclipse *shape*, with the depth free. A
   mis-scaled TESS depth had made the archive "rule out" FXT-0146 even
   though the star was visibly dimmer on schedule.
