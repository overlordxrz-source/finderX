"""finderX command line.

  python -m finderx serve                       # terminal UI on http://127.0.0.1:5050
  python -m finderx scan transit --tic 261136679
  python -m finderx scan transit --sector 70 --n 40
  python -m finderx scan stellar --name "Hyades" --radius 2
  python -m finderx scan galaxy --ra 150.1 --dec 2.2 --radius 0.5
  python -m finderx scan solar --mode neocp
  python -m finderx patrol --rounds 5 --n 40     # blind TESS survey
  python -m finderx queue --status new
  python -m finderx show FXT-0001
  python -m finderx vote FXT-0001 confirm --note "clean U-shaped transit"
  python -m finderx note FXT-0001 "analyst text"  # notes from an agent / reviewer
  python -m finderx report FXT-0001
  python -m finderx export --status confirmed --format csv
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from . import db as dbmod
from . import net, report

_C = {"info": "\033[37m", "debug": "\033[90m", "warn": "\033[33m", "error": "\033[31m", "ok": "\033[32m", "hi": "\033[36m", "end": "\033[0m"}


def _color(s: str, c: str) -> str:
    return s if not sys.stdout.isatty() else f"{_C[c]}{s}{_C['end']}"


def _printer(verbose: bool):
    def on(ev: dict):
        t = ev.get("type")
        ts = time.strftime("%H:%M:%S", time.localtime(ev.get("ts", time.time())))
        if t == "log":
            if ev.get("level") == "debug" and not verbose:
                return
            print(f"{_color(ts, 'debug')} {ev.get('src', ''):<8} {_color(ev['msg'], ev.get('level', 'info'))}", flush=True)
        elif t == "candidate":
            tag = "NEW " if ev.get("new") else "UPD "
            print(f"{_color(ts, 'debug')} {'SIGNAL':<8} {_color(tag + ev['id'], 'hi')} {ev['kind']:<18} {ev['title']} — {ev.get('subtitle', '')}", flush=True)
        elif t == "job" and ev.get("state") != "running":
            print(f"{_color(ts, 'debug')} {'JOB':<8} {ev['job']} {_color(ev['state'], 'ok' if ev['state'] == 'complete' else 'warn')} {json.dumps(ev.get('summary', {}))}", flush=True)

    return on


def _resolve_target(a) -> dict:
    p: dict = {}
    if getattr(a, "name", None):
        r = net.resolve(a.name)
        if not r:
            sys.exit(f"could not resolve '{a.name}'")
        print(f"resolved {a.name} → RA {r['ra']:.5f}  Dec {r['dec']:+.5f}  ({r.get('otype')})")
        p.update(ra=r["ra"], dec=r["dec"])
    if getattr(a, "ra", None) is not None:
        p["ra"] = a.ra
    if getattr(a, "dec", None) is not None:
        p["dec"] = a.dec
    if getattr(a, "radius", None) is not None:
        p["radius"] = a.radius
    return p


def cmd_scan(a) -> None:
    from .jobs import JobManager

    params = _resolve_target(a)
    for k in ("tic", "sector", "n", "mode", "workers", "sectors"):
        v = getattr(a, k, None)
        if v is not None:
            params[k] = v
    if a.include_known:
        params["include_known"] = True
    m = JobManager()
    if not a.json:
        m.bus.on(_printer(a.verbose))
    ctx = m.run_sync(a.engine, params)
    if a.json:
        print(json.dumps({"job": ctx.id, "counters": dict(ctx.counters), "candidates": m.db.candidates(limit=50, order="recent")}, default=str, indent=1))
    else:
        _print_job_results(m.db, ctx.id)


def _print_job_results(db, jid: str) -> None:
    rows = [c for c in db.candidates(limit=500, order="recent") if c["job"] == jid]
    if rows:
        print()
        _table(rows)
    res = db.results(jid)
    if res:
        print(f"\n{len(res)} live objects (see `python -m finderx serve` for the sky view). First 15:")
        for r in res[:15]:
            print("  " + "  ".join(f"{k}={v}" for k, v in r.items() if k in ("kind", "name", "class", "score", "vmag", "date", "dist_ld", "ra", "dec") and v is not None))


def cmd_patrol(a) -> None:
    from .jobs import JobManager

    params = {"patrol": True, "n": a.n, "workers": a.workers}
    if a.sector:
        params["sector"] = a.sector
    if a.rounds:
        params["rounds"] = a.rounds
    if a.minutes:
        params["minutes"] = a.minutes
    m = JobManager()
    m.bus.on(_printer(a.verbose))
    try:
        ctx = m.run_sync("transit", params)
        _print_job_results(m.db, ctx.id)
    except KeyboardInterrupt:
        print("\npatrol interrupted")


def _table(rows: list[dict]) -> None:
    print(f"{'ID':<10} {'STATUS':<10} {'KIND':<20} {'SCORE':>5}  TITLE")
    for c in rows:
        known = " (known)" if c.get("kind", "").startswith("known_") else ""
        print(f"{c['id']:<10} {c['status']:<10} {c['kind']:<20} {c['score']:>5.2f}  {c['title']}{known}")
        if c.get("subtitle"):
            print(f"{'':<48}{c['subtitle']}")


def cmd_queue(a) -> None:
    rows = dbmod.get().candidates(status=a.status, engine=a.engine, kind=a.kind, limit=a.limit)
    if not a.include_known:
        rows = [r for r in rows if not r["kind"].startswith("known_")]
    if a.json:
        print(json.dumps(rows, default=str, indent=1))
    else:
        _table(rows)


def cmd_show(a) -> None:
    c = dbmod.get().candidate(a.id)
    if not c:
        sys.exit(f"no candidate {a.id}")
    if a.json:
        c.pop("plots", None)
        print(json.dumps(c, default=str, indent=1))
    else:
        print(report.text(c))


def cmd_vote(a) -> None:
    verdict = {"confirm": "confirmed", "reject": "rejected", "flag": "flagged", "new": "new"}[a.verdict]
    if not dbmod.get().vote(a.id, verdict, a.note):
        sys.exit(f"no candidate {a.id}")
    print(f"{a.id} → {verdict}")


def cmd_note(a) -> None:
    if not dbmod.get().set_analyst_note(a.id, " ".join(a.text)):
        sys.exit(f"no candidate {a.id}")
    print(f"analyst note saved on {a.id}")


def cmd_report(a) -> None:
    c = dbmod.get().candidate(a.id)
    if not c:
        sys.exit(f"no candidate {a.id}")
    print(report.text(c))


def cmd_export(a) -> None:
    rows = dbmod.get().candidates(status=a.status, limit=100000)
    out = report.to_csv(rows) if a.format == "csv" else json.dumps(rows, default=str, indent=1)
    if a.out:
        open(a.out, "w").write(out)
        print(f"wrote {len(rows)} candidates to {a.out}")
    else:
        sys.stdout.write(out)


def cmd_stats(a) -> None:
    print(json.dumps(dbmod.get().stats(), indent=1))


def cmd_serve(a) -> None:
    import uvicorn

    from .server import create_app

    url = f"http://{a.host}:{a.port}"
    print(f"\n  finderX discovery terminal → {url}\n")
    if a.open:
        import threading
        import webbrowser

        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(), host=a.host, port=a.port, log_level="warning")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="finderx", description="finderX discovery terminal")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the terminal UI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=5050)
    s.add_argument("--open", action="store_true", help="open a browser tab")
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("scan", help="run one engine")
    s.add_argument("engine", choices=["transit", "stellar", "galaxy", "solar"])
    s.add_argument("--tic", type=int)
    s.add_argument("--sector", type=int)
    s.add_argument("--sectors", type=int, help="max sectors to stitch for --tic")
    s.add_argument("--n", type=int)
    s.add_argument("--name", help="resolve a target name (SIMBAD/NED via Sesame)")
    s.add_argument("--ra", type=float)
    s.add_argument("--dec", type=float)
    s.add_argument("--radius", type=float)
    s.add_argument("--mode", help="solar: field | neocp | approaches | watch")
    s.add_argument("--workers", type=int)
    s.add_argument("--include-known", action="store_true")
    s.add_argument("--json", action="store_true")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_scan)

    s = sub.add_parser("patrol", help="blind TESS survey of fresh stars")
    s.add_argument("--sector", type=int)
    s.add_argument("--n", type=int, default=40, help="stars per round")
    s.add_argument("--rounds", type=int, default=0)
    s.add_argument("--minutes", type=float, default=0)
    s.add_argument("--workers", type=int, default=4)
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_patrol)

    s = sub.add_parser("queue", help="list candidates")
    s.add_argument("--status", default="new,flagged")
    s.add_argument("--engine")
    s.add_argument("--kind")
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--include-known", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_queue)

    s = sub.add_parser("show", help="candidate dossier")
    s.add_argument("id")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("vote", help="vet a candidate")
    s.add_argument("id")
    s.add_argument("verdict", choices=["confirm", "reject", "flag", "new"])
    s.add_argument("--note")
    s.set_defaults(fn=cmd_vote)

    s = sub.add_parser("note", help="attach an analyst note")
    s.add_argument("id")
    s.add_argument("text", nargs="+")
    s.set_defaults(fn=cmd_note)

    s = sub.add_parser("report", help="submission-ready summary")
    s.add_argument("id")
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("export", help="export candidates")
    s.add_argument("--status", default="confirmed")
    s.add_argument("--format", choices=["csv", "json"], default="csv")
    s.add_argument("--out")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("stats")
    s.set_defaults(fn=cmd_stats)

    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
