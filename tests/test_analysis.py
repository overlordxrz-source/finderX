"""Offline tests for the signal search: synthetic light curves, no network."""

import numpy as np
import pytest

from finderx import analysis as A
from finderx import lightcurve as L

SUN = A.Star(radius=1.0, mass=1.0, teff=5800, logg=4.44)


def sector(seed=0, cadence_min=10.0, noise=8e-4, days=27.0):
    rng = np.random.default_rng(seed)
    t = np.arange(0, days, cadence_min / 1440.0) + 3000.0
    t = t[(t - 3000.0 < 13.0) | (t - 3000.0 > 14.0)]  # mid-sector downlink gap
    return t, 1.0 + rng.normal(0, noise, t.size)


def box(t, period, t0, dur, depth):
    return np.where(np.abs(L.fold(t, period, t0) * period) < dur / 2, depth, 0.0)


def search(t, f, star=SUN):
    trend = L.detrend(t, f, 0.75)
    return A.transit_search(t, f / trend, star)


def test_recovers_injected_planet_under_stellar_variability():
    t, f = sector(1)
    f *= 1 + 3e-3 * np.sin(2 * np.pi * t / 4.3)  # slow spot modulation
    f -= box(t, 3.7, 3001.3, 0.12, 2.5e-3)
    signals, pgram = search(t, f)
    assert signals, "no signal found"
    s = signals[0]
    assert s.period == pytest.approx(3.7, rel=3e-3)
    assert s.depth == pytest.approx(2.5e-3, rel=0.25)
    assert s.kind == "planet_candidate"
    assert s.rp_earth == pytest.approx(np.sqrt(2.5e-3) * 109.1, rel=0.2)
    assert pgram["p"] and len(pgram["p"]) == len(pgram["power"])


def test_finds_second_planet_after_masking_first():
    t, f = sector(2, noise=5e-4)
    f -= box(t, 2.9, 3000.7, 0.10, 3e-3)
    f -= box(t, 7.4, 3002.2, 0.15, 2.2e-3)
    signals, _ = search(t, f)
    periods = sorted(round(s.period, 1) for s in signals)
    assert 2.9 in periods and 7.4 in periods


def test_eclipsing_binary_is_not_called_a_planet():
    t, f = sector(3)
    p_orb = 5.0
    f -= box(t, p_orb, 3001.0, 0.15, 8e-3)          # primary
    f -= box(t, p_orb, 3001.0 + p_orb / 2, 0.15, 3e-3)  # weaker secondary
    s = search(t, f)[0][0]
    assert s.kind == "eclipsing_binary"
    if s.period == pytest.approx(p_orb / 2, rel=5e-3):
        assert "ODD_EVEN_MISMATCH" in s.flags   # folded both eclipses together
    else:
        assert s.period == pytest.approx(p_orb, rel=5e-3)
        assert "SECONDARY_ECLIPSE" in s.flags   # found the orbit, saw the secondary


def test_secondary_eclipse_is_caught():
    t, f = sector(4)
    f -= box(t, 3.3, 3001.0, 0.12, 6e-3)
    f -= box(t, 3.3, 3001.0 + 1.65, 0.12, 2e-3)
    s = search(t, f)[0][0]
    assert "SECONDARY_ECLIPSE" in s.flags or "ODD_EVEN_MISMATCH" in s.flags
    assert s.kind == "eclipsing_binary"


def test_giant_companion_is_too_large():
    t, f = sector(5)
    f -= box(t, 4.1, 3001.5, 0.15, 0.09)  # 9% dip on a Sun-like star → ~33 R⊕
    s = search(t, f)[0][0]
    assert "TOO_LARGE_FOR_PLANET" in s.flags


def test_pure_noise_is_quiet():
    t, f = sector(6)
    signals, _ = search(t, f)
    assert signals == []


def test_detrend_preserves_transit_depth():
    t, f = sector(7, noise=2e-4)
    f *= 1 + 5e-3 * np.sin(2 * np.pi * t / 6.0)
    dip = box(t, 3.0, 3000.5, 0.1, 4e-3)
    trend = L.detrend(t, f - dip, 0.75)
    resid = (f - dip) / trend
    intr = dip > 0
    measured = 1 - np.median(resid[intr])
    assert measured == pytest.approx(4e-3, rel=0.15)


def test_lomb_scargle_period_and_rotation_class():
    t, f = sector(8)
    f *= 1 + 8e-3 * np.sin(2 * np.pi * t / 2.37)
    v, pgram = A.variability_search(t, f, A.Star(teff=4800))
    assert v is not None
    assert v.period == pytest.approx(2.37, rel=0.01)
    assert v.type_guess == "ROT"
    assert pgram["p"]


def test_contact_binary_reported_at_twice_ls_period():
    t, f = sector(9)
    p_orb = 0.36
    phase = 2 * np.pi * t / p_orb
    f *= 1 - 0.02 * (1 - np.cos(2 * phase)) / 2 - 0.002 * (1 - np.cos(phase)) / 2
    v, _ = A.variability_search(t, f, A.Star(teff=5600))
    assert v is not None
    assert v.type_guess in ("EW", "EB")
    assert v.true_period == pytest.approx(p_orb, rel=0.01)


def test_red_noise_snr_is_not_larger_than_white():
    t, f = sector(10)
    f -= box(t, 3.1, 3000.9, 0.1, 3e-3)
    s = search(t, f)[0][0]
    assert s.snr <= s.depth / s.depth_err + 1e-6


def test_star_rejects_placeholder_radius():
    st = A.Star.from_meta({"radius": -1.0, "logg": 4.4, "teff": 5000})
    assert st.radius is None and st.mass is None


def test_iterative_search_does_not_repeat_a_signal():
    t, f = sector(11, noise=4e-4)
    f -= box(t, 4.2, 3001.1, 0.2, 5e-3)
    signals, _ = search(t, f)
    periods = [s.period for s in signals]
    for i, p in enumerate(periods):
        for q in periods[i + 1:]:
            assert not A._same_period(p, q)


def test_cleaning_keeps_transits_and_drops_spikes_and_edges():
    t, f = sector(12, noise=3e-4)
    f -= box(t, 3.3, 3001.6, 0.12, 3e-3)
    spike = 500
    f[spike] -= 0.02
    keep = L.clip_isolated_dips(f) & L.trim_edges(t)
    assert not keep[spike]
    intr = box(t, 3.3, 3001.6, 0.12, 1.0) > 0
    assert keep[intr].mean() > 0.9          # transits survive
    assert not keep[0] and not keep[-1]     # segment edges trimmed
