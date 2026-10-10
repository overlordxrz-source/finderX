"""Persistence, parsing, reporting and the HTTP API — all offline."""

import pytest

from finderx import catalogs, report
from finderx.db import DB
from finderx.engines.solar import parse_neocp

NEOCP_SAMPLE = """\
P12qZDO 100 2026 10 04.4   0.0098  -2.2567 21.7 Added Oct. 4.94 UT               4   0.03 23.3  0.523
Sar2932  98 2026 10 04.8  23.7094 +26.1627 19.6 Updated Oct. 4.93 UT            20   0.09 26.7  0.029
garbage line
"""


def test_parse_neocp():
    rows = parse_neocp(NEOCP_SAMPLE)
    assert [r["name"] for r in rows] == ["P12qZDO", "Sar2932"]
    assert rows[0]["ra"] == pytest.approx(0.0098 * 15)
    assert rows[1]["dec"] == pytest.approx(26.1627)
    assert rows[1]["nobs"] == 20 and rows[1]["score"] == 98


@pytest.mark.parametrize("p,q,rel", [(3.0, 3.0, "1:1"), (6.0, 3.0, "2:1"), (1.5, 3.0, "1:2"), (3.0, 4.0, None), (3.0, None, None)])
def test_period_match(p, q, rel):
    assert catalogs.period_match(p, q) == rel


def cand(**kw):
    base = {
        "engine": "transit", "kind": "planet_candidate", "target": "TIC 1", "dedupe": "transit:1:pc:3.000",
        "ra": 10.0, "dec": -20.0, "title": "TIC 1 · P 3.0000 d", "subtitle": "1,000 ppm", "score": 0.7,
        "known": [], "flags": [],
        "metrics": {"period": 3.0, "t0": 3001.0, "duration_h": 2.5, "depth_ppm": 1000.0, "depth_err": 1e-4,
                    "snr": 12.0, "sde": 15.0, "n_transits": 8, "rp_earth": 3.4,
                    "star": {"tic": 1, "sectors": [70], "provenance": "QLP", "tmag": 10.0, "teff": 5700}},
    }
    base.update(kw)
    return base


def test_db_upsert_dedupes_and_keeps_vote():
    db = DB(":memory:")
    cid, new = db.candidate_upsert(cand())
    assert new and cid == "FXT-0001"
    assert db.vote(cid, "confirmed", "looks clean")
    cid2, new2 = db.candidate_upsert(cand(score=0.9))
    assert cid2 == cid and not new2
    c = db.candidate(cid)
    assert c["status"] == "confirmed" and c["note"] == "looks clean" and c["score"] == 0.9
    other, _ = db.candidate_upsert(cand(engine="variable", dedupe="variable:1:0.5"))
    assert other == "FXV-0002"
    with pytest.raises(ValueError):
        db.vote(cid, "maybe")


def test_payload_roundtrip_and_stats():
    db = DB(":memory:")
    db.payload_put("p1", {"raw": {"t": [1, 2], "f": [1.0, 0.99]}})
    assert db.payload_get("p1")["raw"]["f"] == [1.0, 0.99]
    db.mark_examined("transit", "123", "job", "quiet")
    assert db.was_examined("transit", "123")
    assert db.stats()["examined_total"] == 1


def test_report_text_has_submission_fields():
    db = DB(":memory:")
    cid, _ = db.candidate_upsert(cand())
    txt = report.text(db.candidate(cid))
    for needle in ("TIC ID", "Period (d)", "Epoch (BTJD)", "Depth (ppm)", "ExoFOP"):
        assert needle in txt
    csv = report.to_csv(db.candidates())
    assert csv.splitlines()[0].startswith("id,status,engine")


def test_api_queue_vote_and_report():
    from fastapi.testclient import TestClient

    from finderx.jobs import JobManager
    from finderx.server import create_app

    db = DB(":memory:")
    cid, _ = db.candidate_upsert(cand())
    client = TestClient(create_app(JobManager(db=db)))
    rows = client.get("/api/candidates?status=new").json()["candidates"]
    assert [r["id"] for r in rows] == [cid]
    assert client.post(f"/api/candidates/{cid}/vote", json={"status": "confirmed"}).status_code == 200
    assert client.get("/api/candidates?status=new").json()["candidates"] == []
    assert "ExoFOP" in client.get(f"/api/candidates/{cid}/report").text
    assert client.post("/api/scan", json={"engine": "nope", "params": {}}).status_code == 400
    assert client.get("/api/resolve?q=TIC 261136679").json() == {"tic": 261136679}
    assert client.get("/api/resolve?q=83.8 -5.4").json()["dec"] == pytest.approx(-5.4)
    assert client.get("/").status_code == 200


def test_common_mode_register_flags_shared_epochs():
    from types import SimpleNamespace

    from finderx.engines.transit import _common_mode

    db = DB(":memory:")
    ctx = SimpleNamespace(db=db)
    for star in ("TIC 1", "TIC 2"):
        db.events_add(103, star, [4153.20, 4159.10, 4165.00])
    db.events_add(104, "TIC 3", [4153.20])  # other sector: must not count
    assert _common_mode(ctx, 103, "TIC 9", [4153.25, 4159.05, 4170.0], 0.1)       # 2 of 3 coincide
    assert not _common_mode(ctx, 103, "TIC 9", [4150.0, 4156.0, 4162.0], 0.1)
    assert not _common_mode(ctx, 103, "TIC 1", [4153.20, 4159.10], 0.1)          # only one *other* star


def test_bundle_roundtrip_keeps_plots_and_notes():
    src = DB(":memory:")
    cid, _ = src.candidate_upsert(cand(payload="p1"))
    src.payload_put("p1", {"raw": {"t": [1.0], "f": [1.0]}})
    src.set_analyst_note(cid, "looks real")
    src.candidate_upsert(cand(kind="systematic", dedupe="transit:2:sys:1.000", target="TIC 2"))
    bundle = src.export_bundle()
    assert len(bundle["candidates"]) == 1  # systematics stay home

    dst = DB(":memory:")
    dst.candidate_upsert(cand(dedupe="other", target="TIC 3"))  # occupies FXT-0001
    assert dst.import_bundle(bundle) == (1, 0)
    assert dst.import_bundle(bundle) == (0, 1)
    got = [c for c in dst.candidates() if c["target"] == "TIC 1"][0]
    full = dst.candidate(got["id"])
    assert full["analyst"] == "looks real" and full["plots"]["raw"]["f"] == [1.0]
    assert got["id"] != "FXT-0001"


def test_pixel_check_finds_the_dimming_neighbour():
    import numpy as np
    from astropy.wcs import WCS

    from finderx import pixels as px

    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [100.0, -30.0]
    w.wcs.crpix = [6.0, 6.0]                       # FITS 1-based → pixel (5, 5)
    w.wcs.cdelt = [-21 / 3600, 21 / 3600]
    hdr = w.to_header()

    rng = np.random.default_rng(3)
    t = np.arange(3000, 3027, 0.0023)
    P, t0, dur = 3.1, 3001.0, 0.12
    intr = np.abs(((t - t0) / P + 0.5) % 1 - 0.5) * P < dur / 2
    target = (5.0, 5.0)
    neighbour = (7.0, 4.0)
    cube = np.zeros((t.size, 11, 11))
    cube += 2000 * px._psf(*target)[None]
    cube += 300 * px._psf(*neighbour)[None] * np.where(intr, 0.85, 1.0)[:, None, None]   # neighbour eclipses 15 %
    cube += rng.normal(0, 0.4, cube.shape)
    cut = {"sector": 1, "time": t, "flux": cube, "wcs": hdr}

    img = px.difference_image(cut, P, t0, dur)
    assert img and img["n_events"] >= 7
    ra_t, de_t = w.pixel_to_world_values(*target)
    ra_n, de_n = w.pixel_to_world_values(*neighbour)
    stars = [
        {"Source": 1, "RA_ICRS": float(ra_t), "DE_ICRS": float(de_t), "Gmag": 11.0},
        {"Source": 2, "RA_ICRS": float(ra_n), "DE_ICRS": float(de_n), "Gmag": 13.0},
    ]
    depth = 0.15 * 300 / 2300
    loc = px.locate(cut, img, stars, 1, float(ra_t), float(de_t), depth)
    assert loc["verdict"] == "off_target" and loc["best"]["gaia"] == "2"

    # same field, but now the target carries the dip → on target
    cube2 = np.zeros_like(cube)
    cube2 += 2000 * px._psf(*target)[None] * np.where(intr, 0.99, 1.0)[:, None, None]
    cube2 += 300 * px._psf(*neighbour)[None]
    cube2 += rng.normal(0, 0.4, cube.shape)
    cut2 = {**cut, "flux": cube2}
    loc2 = px.locate(cut2, px.difference_image(cut2, P, t0, dur), stars, 1, float(ra_t), float(de_t), 0.01)
    assert loc2["verdict"] == "on_target"


def test_archival_check_catches_the_eclipsing_neighbour(monkeypatch):
    import numpy as np

    from finderx import archival as ar

    rng = np.random.default_rng(5)
    P, t_ref, dur = 2.5, 3000.0, 0.12
    t = np.sort(rng.uniform(-1800, -400, 400)) + t_ref       # sparse survey epochs years earlier
    ph = ((t - t_ref) / P + 0.5) % 1 - 0.5
    ecl = np.abs(ph * P) < dur / 2
    def lc(name, ra, dec, f):
        return {"survey": "Pan-STARRS1", "id": name, "ra": ra, "dec": dec, "t": t, "f": f, "e": np.full(t.size, 0.01)}
    flat = 1 + rng.normal(0, 0.01, t.size)
    lcs = {
        "a": lc("PS1 target", 10.0, 0.0, flat.copy()),
        "b": lc("PS1 nb", 10.0 + 20 / 3600, 0.0, np.where(ecl, 0.8, 1.0) + rng.normal(0, 0.01, t.size)),
    }
    monkeypatch.setattr(ar, "ps1_lightcurves", lambda ra, dec: lcs)
    monkeypatch.setattr(ar, "gaia_epoch_lightcurves", lambda ra, dec: {})
    stars = [{"Source": 1, "RA_ICRS": 10.0, "DE_ICRS": 0.0, "Gmag": 15.0},
             {"Source": 2, "RA_ICRS": 10.0 + 20 / 3600, "DE_ICRS": 0.0, "Gmag": 16.5}]
    depth = 0.2 * 10 ** (-0.4 * 1.5) / (1 + 10 ** (-0.4 * 1.5))
    res = ar.check(10.0, 0.0, P, t_ref, dur, depth, stars, "1", 0.002, 1e-6)
    assert res["verdict"] == "caught_on_neighbour"
    assert res["stars"][0]["gaia"] == "2" and res["stars"][0]["n_in"] >= 3


def test_planner_keeps_night_events_and_drops_daytime_ones():
    from datetime import datetime, timezone

    from astropy.time import Time

    from finderx import planner

    now = datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc)
    obs = planner.observer_from({"key": "lco-ctio"})
    night_mid = Time(datetime(2026, 10, 5, 4, 45, tzinfo=timezone.utc)).jd - 2457000   # local midnight at CTIO

    def cand(cid, t0):
        return {"id": cid, "engine": "transit", "kind": "planet_candidate", "title": cid, "target": cid, "status": "new",
                "ra": 20.0, "dec": -30.0, "flags": [],
                "metrics": {"period": 1.0, "t0": t0, "duration": 0.1, "depth": 0.01, "snr": 40, "n_transits": 20, "transit_times": [t0 - 200, t0]}}

    events = planner.predict([cand("night", night_mid), cand("day", night_mid + 0.5)], obs, days=4, now=now)
    assert {e["id"] for e in events} == {"night"}
    assert len(events) >= 3
    assert all(e["quality"] == "full" and e["alt"][4] > 45 for e in events)
    assert all(e["mid_utc"].endswith(("04:44", "04:45", "04:46")) for e in events)


def test_rerun_with_corrected_period_refreshes_the_old_candidate():
    from finderx.db import DB

    db = DB(":memory:")
    base = {"engine": "transit", "kind": "eclipsing_binary", "target": "TIC 1", "title": "x", "score": 0.5}
    old, _ = db.candidate_upsert({**base, "job": "j1", "dedupe": "transit:1:eclipsing_binary:4.737", "metrics": {"period": 4.7371742}})
    db.candidate_upsert({**base, "job": "j1", "dedupe": "transit:1:planet_candidate:11.3", "metrics": {"period": 11.3}})
    assert db.harmonic_twin("TIC 1", "transit", 0.947476, job="j2") == "transit:1:eclipsing_binary:4.737"
    assert db.harmonic_twin("TIC 1", "transit", 0.947476, job="j1") is None   # same job: a distinct signal
    assert db.harmonic_twin("TIC 1", "transit", 1.3, job="j2") is None


def test_archival_partial_eclipse_points_do_not_rule_out_the_target(monkeypatch):
    # archival epochs that land on ingress/egress are only partly dimmed; a box
    # model called that "full brightness mid-eclipse" and excluded the target
    import numpy as np

    from finderx import archival as ar

    P, t_ref, dur, depth = 5.0, 3000.0, 0.2, 0.07
    # TESS-like template: V-ish eclipse, flat outside
    tt = np.arange(t_ref - 30, t_ref + 30, 10 / 1440)
    dt = (((tt - t_ref) / P + 0.5) % 1 - 0.5) * P
    tf = 1 - depth * np.clip(1 - np.abs(dt) / (dur / 2), 0, 1)
    tmpl = ar.eclipse_template(tt, tf, P, t_ref, dur)
    # three survey epochs: two on the eclipse wings, one near mid-eclipse; the rest out of eclipse
    k = np.arange(-300, -280)
    t_obs = t_ref + k * P + np.r_[0.075, -0.08, 0.01, np.linspace(0.6, 4.0, 17)]
    f_true = 1 - depth * np.clip(1 - np.abs(t_obs - t_ref - k * P) / (dur / 2), 0, 1)
    lc = {"survey": "Gaia DR3 epochs", "id": "Gaia DR3 1", "gaia": "1", "ra": 10.0, "dec": 0.0,
          "t": t_obs, "f": f_true, "e": np.full(t_obs.size, 0.003)}
    monkeypatch.setattr(ar, "ps1_lightcurves", lambda ra, dec: {})
    monkeypatch.setattr(ar, "gaia_epoch_lightcurves", lambda ra, dec: {"1": lc})
    stars = [{"Source": 1, "RA_ICRS": 10.0, "DE_ICRS": 0.0, "Gmag": 11.0}]
    box = ar.check(10.0, 0.0, P, t_ref, dur, depth, stars, "1", 0.002, 1e-6)
    shaped = ar.check(10.0, 0.0, P, t_ref, dur, depth, stars, "1", 0.002, 1e-6, template=tmpl)
    assert shaped["verdict"] == "caught_on_target"
    # the TESS product overstated the depth 5× (dilution / background): the
    # target still dimmed on schedule, so it must not be ruled out
    inflated = ar.check(10.0, 0.0, P, t_ref, dur, depth * 5, stars, "1", 0.002, 1e-6, template=ar.eclipse_template(tt, 1 - 5 * (1 - tf), P, t_ref, dur))
    assert inflated["verdict"] == "caught_on_target"
    assert box["verdict"] != "target_ruled_out"
