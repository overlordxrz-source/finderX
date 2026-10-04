"""Turn a vetted candidate into something you can submit or share."""

from __future__ import annotations

import csv
import io
import json

BTJD_OFFSET = 2457000.0

_ROUTES = {
    "planet_candidate": (
        "ExoFOP-TESS Community TOI (CTOI)",
        "https://exofop.ipac.caltech.edu/tess/ — log in, then 'Upload CTOIs'. Attach the folded light curve "
        "and the vetting summary below. Planet Hunters TESS used exactly this route.",
    ),
    "eclipsing_binary": (
        "AAVSO VSX (new variable: EA/EB/EW)",
        "https://www.aavso.org/vsx/ — 'Submit a new variable'. Include the period, epoch and amplitude below.",
    ),
    "variable": (
        "AAVSO VSX (new variable)",
        "https://www.aavso.org/vsx/ — 'Submit a new variable'. If VSX already lists the star without a period, "
        "use 'Revise' and add the period instead.",
    ),
    "quasar": (
        "Spectroscopic follow-up target",
        "Unconfirmed quasars are confirmed with a spectrum. Share the list with a survey team (e.g. DESI / 4MOST "
        "secondary-target calls) or an observatory with public time.",
    ),
    "obscured_agn": ("Spectroscopic / X-ray follow-up target", "Cross-check eROSITA / Chandra archives; a spectrum confirms it."),
    "galaxy": ("NED submission", "https://ned.ipac.caltech.edu/ — contact NED to add a new object with its coordinates and evidence."),
}


def route(kind: str) -> tuple[str, str]:
    k = kind.replace("known_", "")
    if k in _ROUTES:
        return _ROUTES[k]
    return ("Literature / SIMBAD", "Write it up (a research note in RNAAS is the lightest path) so SIMBAD can index it.")


def text(c: dict) -> str:
    m = c.get("metrics") or {}
    star = m.get("star") or {}
    title, how = route(c["kind"])
    lines = [
        f"finderX candidate {c['id']} — {c['title']}",
        f"kind: {c['kind']}    status: {c['status']}    score: {c.get('score', 0):.2f}",
        f"position: RA {c['ra']:.6f}  Dec {c['dec']:+.6f}  (ICRS deg)" if c.get("ra") is not None else "",
        "",
    ]
    if c["engine"] == "transit":
        lines += [
            f"TIC ID              {star.get('tic')}",
            f"Sectors             {', '.join(map(str, star.get('sectors', [])))}  ({star.get('provenance')})",
            f"Period (d)          {m.get('period'):.6f}",
            f"Epoch (BTJD)        {m.get('t0'):.5f}     (BJD {m.get('t0', 0) + BTJD_OFFSET:.5f})",
            f"Duration (h)        {m.get('duration_h'):.3f}",
            f"Depth (ppm)         {m.get('depth_ppm'):.0f} ± {m.get('depth_err', 0) * 1e6:.0f}",
            f"SNR / SDE           {m.get('snr'):.1f} / {m.get('sde'):.1f}",
            f"Transits observed   {m.get('n_transits')}",
            f"Planet radius (R⊕)  {m.get('rp_earth') and round(m['rp_earth'], 2)}",
            f"Host Tmag / Teff    {star.get('tmag')} / {star.get('teff')}",
            "",
            "Vetting",
            f"  odd/even depth difference   {m.get('odd_even_sigma', 0):.1f} σ",
            f"  secondary eclipse           {m.get('secondary_snr', 0):.1f} σ",
            f"  centroid shift in transit   {m.get('centroid_sigma') or 0:.1f} σ",
            f"  duration / expected         {m.get('duration_ratio') or float('nan'):.2f}",
            f"  shape (box≈1, V≈0.33)       {m.get('shape_ratio') or float('nan'):.2f}",
            f"  flags                       {', '.join(c.get('flags') or []) or 'none'}",
        ]
        if m.get("contaminants"):
            lines.append("  Gaia neighbours able to mimic the dip:")
            for n in m["contaminants"]:
                lines.append(f"    Gaia DR3 {n['gaia']}  sep {n['sep_arcsec']}\"  ΔG {n['dG']}  needs {n['needed_depth'] * 100:.1f}% eclipse")
    elif c["engine"] == "variable":
        lines += [
            f"TIC ID              {star.get('tic')}",
            f"Type (guess)        {m.get('type_guess')} — {m.get('type_label')}",
            f"Period (d)          {m.get('true_period'):.6f}",
            f"Amplitude           {m.get('ptp_ppm', 0) / 1e3:.2f} ppt peak-to-peak (TESS band)",
            f"LS power / FAP      {m.get('power'):.2f} / {m.get('fap'):.1e}",
            f"Sectors             {', '.join(map(str, star.get('sectors', [])))}",
        ]
    else:
        for k, v in m.items():
            if v is not None and not isinstance(v, (dict, list)):
                lines.append(f"{k:<20}{v}")
    if c.get("known"):
        lines += ["", "Catalogue matches"]
        for k in c["known"]:
            lines.append(f"  {k.get('kind'):<10} {k.get('label')}  {k.get('type') or ''}  P={k.get('period')}  match={k.get('match')}")
    if c.get("note"):
        lines += ["", f"Your note: {c['note']}"]
    if c.get("analyst"):
        lines += ["", f"Analyst note: {c['analyst']}"]
    lines += ["", f"Where to report: {title}", f"  {how}"]
    return "\n".join(x for x in lines if x is not None)


def to_csv(cands: list[dict]) -> str:
    buf = io.StringIO()
    cols = ["id", "status", "engine", "kind", "target", "ra", "dec", "score", "title", "subtitle", "flags", "known", "note", "metrics"]
    w = csv.writer(buf)
    w.writerow(cols)
    for c in cands:
        w.writerow([
            c.get("id"), c.get("status"), c.get("engine"), c.get("kind"), c.get("target"),
            c.get("ra"), c.get("dec"), c.get("score"), c.get("title"), c.get("subtitle"),
            "|".join(c.get("flags") or []),
            "|".join(str(k.get("label")) for k in c.get("known") or []),
            c.get("note") or "",
            json.dumps(c.get("metrics") or {}),
        ])
    return buf.getvalue()
