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
