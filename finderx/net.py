"""Thin, dependency-light clients for the archives finderX talks to.

* ``tap``     – ADQL over any IVOA TAP sync endpoint (VizieR, SIMBAD, NED, NASA)
* ``mast``    – MAST portal ``invoke`` API (CAOM product discovery)
* ``xmatch``  – CDS X-Match: upload a small position list, match a big catalog
* ``get``     – plain GET with retries

All calls retry transient failures with exponential backoff and raise
``ArchiveError`` with a short, human-readable message otherwise.
"""

from __future__ import annotations

import csv
import io
import json
import time
from typing import Any, Iterable

import httpx

from . import config


class ArchiveError(RuntimeError):
    pass


_client: httpx.Client | None = None


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            headers={"User-Agent": config.USER_AGENT},
            timeout=httpx.Timeout(60.0, connect=15.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )
    return _client


def _request(method: str, url: str, retries: int = 3, **kw) -> httpx.Response:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = client().request(method, url, **kw)
            if r.status_code >= 500 or r.status_code == 429:
                raise ArchiveError(f"{_host(url)} HTTP {r.status_code}")
            return r
        except (httpx.TransportError, ArchiveError) as exc:
            last = exc
            if attempt < retries - 1:
                time.sleep(1.5 * 2**attempt)
    raise ArchiveError(f"{_host(url)} unreachable: {last}")


def _host(url: str) -> str:
    return url.split("/")[2] if "//" in url else url


def get(url: str, **kw) -> httpx.Response:
    r = _request("GET", url, **kw)
    if r.status_code >= 400:
        raise ArchiveError(f"{_host(url)} HTTP {r.status_code}")
    return r


def _convert(v: str) -> Any:
    v = v.strip()
    if v == "":
        return None
    try:
        return int(v)
    except ValueError:
        pass
    try:
        f = float(v)
        return f if f == f else None  # drop NaN
    except ValueError:
        return v


def parse_csv(text: str) -> list[dict]:
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return []
    return [dict(zip(header, (_convert(x) for x in row))) for row in reader if row]


def tap(url: str, query: str, timeout: float = 90.0, max_rec: int | None = None) -> list[dict]:
    data = {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": query}
    if max_rec:
        data["MAXREC"] = str(max_rec)
    r = _request("POST", url, data=data, timeout=timeout)
    if r.status_code >= 400 or r.text.lstrip().startswith("<?xml"):
        raise ArchiveError(f"{_host(url)} rejected query: {_votable_error(r.text)}")
    return parse_csv(r.text)


def _votable_error(text: str) -> str:
    import re

    m = re.search(r'<INFO name="QUERY_STATUS" value="ERROR">(.*?)</INFO>', text, re.S)
    return (m.group(1).strip() if m else text[:200]).replace("\n", " ")


def cone(ra_col: str, dec_col: str, ra: float, dec: float, radius_deg: float, frame: str = "ICRS") -> str:
    """ADQL cone predicate."""
    return (
        f"1=CONTAINS(POINT('{frame}',{ra_col},{dec_col}),"
        f"CIRCLE('{frame}',{ra:.7f},{dec:.7f},{radius_deg:.6f}))"
    )


def mast(service: str, params: dict, page: int | None = None, pagesize: int | None = None) -> dict:
    req: dict[str, Any] = {"service": service, "format": "json", "params": params}
    if page is not None:
        req["page"] = page
    if pagesize is not None:
        req["pagesize"] = pagesize
    r = _request("POST", config.MAST_INVOKE, data={"request": json.dumps(req)}, timeout=120)
    if r.status_code >= 400:
        raise ArchiveError(f"MAST HTTP {r.status_code}")
    out = r.json()
    if out.get("status") not in ("COMPLETE", None):
        raise ArchiveError(f"MAST status {out.get('status')}: {out.get('msg', '')}")
    return out


def mast_download(uri: str, cache: bool = True) -> bytes:
    path = config.CACHE_DIR / "mast" / uri.split("/")[-1]
    if cache and path.exists():
        return path.read_bytes()
    r = _request("GET", config.MAST_DOWNLOAD, params={"uri": uri}, timeout=180)
    if r.status_code >= 400:
        raise ArchiveError(f"MAST download HTTP {r.status_code} for {uri.split('/')[-1]}")
    if cache:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
    return r.content


def xmatch(rows: Iterable[tuple[float, float]], cat2: str, radius_arcsec: float) -> list[dict]:
    """Match a list of (ra, dec) against a CDS catalog; adds ``row`` (input index)."""
    buf = io.StringIO()
    buf.write("row,ra,dec\n")
    n = 0
    for i, (ra, dec) in enumerate(rows):
        buf.write(f"{i},{ra:.7f},{dec:.7f}\n")
        n += 1
    if n == 0:
        return []
    r = _request(
        "POST",
        config.CDS_XMATCH,
        data={
            "request": "xmatch",
            "distMaxArcsec": radius_arcsec,
            "RESPONSEFORMAT": "csv",
            "cat2": cat2,
            "colRA1": "ra",
            "colDec1": "dec",
        },
        files={"cat1": ("pos.csv", buf.getvalue())},
        timeout=120,
    )
    if r.status_code >= 400:
        raise ArchiveError(f"CDS X-Match HTTP {r.status_code}")
    return parse_csv(r.text)


def resolve(name: str) -> dict | None:
    """Resolve an object name through CDS Sesame (SIMBAD → NED → VizieR)."""
    import re
    from urllib.parse import quote

    r = get(f"{config.SESAME}?{quote(name)}", timeout=20)
    m = re.search(r"<jradeg>([-\d.]+)</jradeg>\s*<jdedeg>([-\d.]+)</jdedeg>", r.text)
    if not m:
        return None
    otype = re.search(r"<otype>(.*?)</otype>", r.text)
    oname = re.search(r"<oname>(.*?)</oname>", r.text)
    return {
        "name": oname.group(1) if oname else name,
        "ra": float(m.group(1)),
        "dec": float(m.group(2)),
        "otype": otype.group(1) if otype else None,
    }


def ping(url: str) -> tuple[bool, float]:
    t = time.perf_counter()
    try:
        r = client().get(url, timeout=8.0)
        ok = r.status_code < 500
    except httpx.HTTPError:
        ok = False
    return ok, (time.perf_counter() - t) * 1000.0
