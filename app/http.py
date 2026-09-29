"""Shared httpx client with sane timeouts, a browser UA and simple retry."""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .config import USER_AGENT

_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/plain, */*"},
            follow_redirects=True,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _client


async def close() -> None:
    if _client is not None:
        await _client.aclose()


async def get_json(url: str, *, params: dict | None = None, headers: dict | None = None,
                   retries: int = 2, timeout: float | None = None) -> Any:
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = await client().get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code in (429, 502, 503, 504) and attempt < retries:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            if "json" not in ctype and r.text.lstrip()[:1] not in "[{":
                raise ValueError(f"non-JSON response from {url.split('?')[0]} ({ctype or 'no content-type'})")
            return r.json()
        except (httpx.TransportError, httpx.HTTPStatusError, ValueError) as e:
            last = e
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500 and e.response.status_code != 429:
                break
            if attempt < retries:
                await asyncio.sleep(1.0 * (attempt + 1))
    assert last is not None
    raise last


async def get_text(url: str, *, params: dict | None = None, headers: dict | None = None) -> str:
    r = await client().get(url, params=params, headers=headers)
    r.raise_for_status()
    return r.text
