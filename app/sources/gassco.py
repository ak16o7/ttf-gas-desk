"""Gassco UMM (REMIT urgent market messages) from the public Atom feed.

The feed carries only the latest ~10 publications (each update of an event is its own message), so
this is a "latest messages" view, not a complete outage register. Several versions of one event
are collapsed to the newest.
"""
from __future__ import annotations

import datetime as dt
import re
import xml.etree.ElementTree as ET

from ..cache import cache
from ..http import get_text

FEED = "https://umm.gassco.no/atom.xml"
ATOM = "{http://www.w3.org/2005/Atom}"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(el, name: str):
    for c in el.iter():
        if _local(c.tag) == name:
            return c
    return None


def _text(el, name: str) -> str | None:
    c = _find(el, name)
    return c.text.strip() if c is not None and c.text else None


def _num(s: str | None) -> float | None:
    try:
        return float(s) if s is not None else None
    except ValueError:
        return None


def parse_feed(xml_text: str, now: dt.datetime | None = None) -> list[dict]:
    now = now or dt.datetime.now(dt.timezone.utc)
    root = ET.fromstring(xml_text)
    msgs = []
    for e in root.iter(f"{ATOM}entry"):
        link = e.find(f"{ATOM}link")
        summary = e.find(f"{ATOM}summary")
        if summary is None or not summary.text:
            continue
        body = summary.text.strip()
        body = re.sub(r"^<\?xml[^>]*\?>", "", body).strip()
        try:
            u = ET.fromstring(body)
        except ET.ParseError:
            continue
        mid = _text(u, "messageId") or ""
        m = re.match(r"^(.*?)_+(\d+)$", mid)
        event_id, version = (m.group(1), int(m.group(2))) if m else (mid, 0)
        start, stop = _text(u, "eventStart"), _text(u, "eventStop")
        asset = _find(u, "affectedAsset")
        name = _text(asset, "name") if asset is not None else None
        try:
            s_dt = dt.datetime.fromisoformat(start) if start else None
            e_dt = dt.datetime.fromisoformat(stop) if stop else None
        except ValueError:
            s_dt = e_dt = None
        status = _text(u, "eventStatus")
        msgs.append({
            "event": event_id, "version": version, "asset": name,
            "type": _text(u, "unavailabilityType"), "event_type": _text(u, "eventType"), "status": status,
            "start": start, "stop": stop, "published": _text(u, "publicationDateTime"),
            "unit": _text(u, "unitMeasure"), "unavailable": _num(_text(u, "unavailableCapacity")),
            "available": _num(_text(u, "availableCapacity")), "technical": _num(_text(u, "technicalCapacity")),
            "reason": _text(u, "unavailabilityReason"),
            "active_now": bool(status == "Active" and s_dt and e_dt and s_dt <= now < e_dt),
            "url": link.get("href") if link is not None else None,
        })
    latest: dict[str, dict] = {}
    for m in msgs:
        if m["event"] not in latest or m["version"] > latest[m["event"]]["version"]:
            latest[m["event"]] = m
    return sorted(latest.values(), key=lambda m: m["published"] or "", reverse=True)


async def umm() -> tuple[list[dict], dict]:
    async def fetch():
        txt = await get_text(FEED)
        return parse_feed(txt)

    return await cache.get("gassco:umm", 15 * 60, fetch)
