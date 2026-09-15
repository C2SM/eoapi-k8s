"""Authorization filters for STAC Auth Proxy.

Access is granted per collection, by the `auth:groups` list on the collection
itself. Items carry none of their own: an item is readable exactly when its
parent collection is.
"""

import asyncio
import dataclasses
import json
import os
import time
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

ADMIN_GROUP = "/eoapi-admin"
# auth:groups value that grants everyone, authenticated or not.
PUBLIC = "public"
GROUPS = {"/eoapi-noaa": "noaa", "/nasa-users": "nasa", "/dyamond-users": "dyamond"}
CACHE_TTL = 60
PAGE = 1000
UPSTREAM = os.environ.get("UPSTREAM_URL", "").rstrip("/")

# Upstream responses by path. Not keyed by caller: a collection's auth:groups
# belongs to the collection, so every caller shares one entry.
_cache: dict[str, tuple[float, Any]] = {}


def _readable(context: dict[str, Any]) -> list[str] | None:
    """auth:groups values this caller may read. None means unrestricted."""
    groups = (context.get("payload") or {}).get("groups") or []
    if isinstance(groups, str):
        groups = groups.replace(",", " ").split()
    if ADMIN_GROUP in groups:
        return None
    return [value for group, value in GROUPS.items() if group in groups] + [PUBLIC]


def _expr(values: list[str]) -> dict[str, Any]:
    """CQL2 matching collections that share one of `values`."""
    op = "a_contains" if len(values) == 1 else "a_overlaps"
    return {"op": op, "args": [{"property": "auth:groups"}, values]}


def _get(path: str) -> Any:
    """Upstream JSON, briefly cached. A missing collection reads as {}."""
    hit = _cache.get(path)
    if hit and time.monotonic() - hit[0] < CACHE_TTL:
        return hit[1]

    request = Request(f"{UPSTREAM}{path}", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=10) as response:
            body = json.load(response)
    except HTTPError as error:
        if error.code != 404:
            raise
        body = {}

    _cache[path] = (time.monotonic(), body)
    return body


def _readable_collection_ids(values: list[str]) -> list[str]:
    """Ids of every collection the caller may read, filtered by pgstac."""
    ids: list[str] = []
    while True:
        page = _get(
            "/collections?"
            + urlencode(
                {
                    "filter": json.dumps(_expr(values)),
                    "filter-lang": "cql2-json",
                    "limit": PAGE,
                    "offset": len(ids),
                }
            )
        )
        collections = page.get("collections", [])
        ids += [c["id"] for c in collections]
        if not collections or len(ids) >= (page.get("numberMatched") or 0):
            return ids


@dataclasses.dataclass
class CollectionsFilter:
    """Collections sharing an auth:groups value with the caller."""

    async def __call__(self, context: dict[str, Any]) -> str | dict[str, Any]:
        values = _readable(context)
        return "1=1" if values is None else _expr(values)


@dataclasses.dataclass
class ItemsFilter:
    """Items of those collections.

    /collections/{id}/items and .../items/{item_id} name their collection, so
    that one collection is the whole question - one lookup, whatever the size
    of the catalogue. Only /search, which names none, has to resolve the set.
    """

    async def __call__(self, context: dict[str, Any]) -> str | dict[str, Any]:
        values = _readable(context)
        if values is None:
            return "1=1"

        collection_id = ((context.get("req") or {}).get("path_params") or {}).get(
            "collection_id"
        )
        if collection_id:
            collection = await asyncio.to_thread(
                _get, f"/collections/{quote(collection_id, safe='')}"
            )
            groups = set(collection.get("auth:groups") or [])
            return "1=1" if groups.intersection(values) else "1=0"

        ids = await asyncio.to_thread(_readable_collection_ids, values)
        return {"op": "in", "args": [{"property": "collection"}, ids]} if ids else "1=0"
