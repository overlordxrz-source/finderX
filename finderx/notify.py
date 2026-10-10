"""Phone notifications when the pipeline finds something worth a look.

Uses ntfy.sh (free, no account): install the ntfy app, subscribe to a topic
name only you know, and set it with ``python -m finderx notify --topic NAME``
or the ``FINDERX_NTFY_TOPIC`` environment variable. A Discord webhook URL
works too (``FINDERX_DISCORD_WEBHOOK``).
"""

from __future__ import annotations

import os
import threading

from . import net

NOTIFY_KINDS = {"planet_candidate", "eclipsing_binary", "variable", "high_velocity", "hidden_companion", "ultracool", "white_dwarf"}
MIN_SCORE = float(os.environ.get("FINDERX_NOTIFY_MIN_SCORE", 0.6))


def settings(db) -> dict:
    s = db.meta_get("notify", {}) or {}
    topic = os.environ.get("FINDERX_NTFY_TOPIC") or s.get("ntfy_topic")
    hook = os.environ.get("FINDERX_DISCORD_WEBHOOK") or s.get("discord_webhook")
    return {"ntfy_topic": topic, "discord_webhook": hook, "min_score": s.get("min_score", MIN_SCORE)}


def send(db, title: str, message: str, tags: str = "telescope", click: str | None = None) -> list[str]:
    cfg = settings(db)
    sent = []
    if cfg["ntfy_topic"]:
        headers = {"Title": title.encode("utf-8"), "Tags": tags}
        if click:
            headers["Click"] = click
        r = net.client().post(f"https://ntfy.sh/{cfg['ntfy_topic']}", content=message.encode("utf-8"), headers=headers, timeout=15)
        r.raise_for_status()
        sent.append("ntfy")
    if cfg["discord_webhook"]:
        r = net.client().post(cfg["discord_webhook"], json={"content": f"**{title}**\n{message}"}, timeout=15)
        r.raise_for_status()
        sent.append("discord")
    return sent


def on_candidate(db, cand: dict, cid: str) -> None:
    """Fire-and-forget push for a strong new candidate."""
    cfg = settings(db)
    if not (cfg["ntfy_topic"] or cfg["discord_webhook"]):
        return
    if cand.get("kind") not in NOTIFY_KINDS or float(cand.get("score") or 0) < cfg["min_score"]:
        return
    title = f"finderX · {cid} · {cand['kind'].replace('_', ' ')}"
    msg = f"{cand['title']}\n{cand.get('subtitle', '')}"
    threading.Thread(target=_safe_send, args=(db, title, msg), daemon=True).start()


def _safe_send(db, title, msg):
    try:
        send(db, title, msg, click=f"http://127.0.0.1:{os.environ.get('FINDERX_PORT', 5050)}/")
    except Exception:
        pass

