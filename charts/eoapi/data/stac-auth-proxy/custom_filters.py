"""Authorization filters for STAC Auth Proxy.

Access is granted per collection, by the `auth:groups` list on the collection
itself. Items carry none of their own: an item is readable exactly when its
parent collection is.

A caller's roles are the CSCS Keycloak role names in its token, wherever the
realm puts them: the `groups` claim, realm roles, or roles on the client the
token was issued to (`azp`). Every decision is logged with them.
"""

import asyncio
import dataclasses
import json
import logging
import os
import re
import time
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Unfiltered.
ADMIN_GROUP = "prometheus_all"
# auth:groups value that grants everyone, authenticated or not.
PUBLIC = "public"
# Role -> auth:groups value; each role with and without "tds_". The dyamond
# roles exist misspelled: "dynamo" is what a real token carries, "dyanamo" is
# how CSCS reported it, and both are to become "dyamond". Until then all three
# spellings map. No role maps to "noaa" yet, so noaa-only collections are
# readable by ADMIN_GROUP only.
GROUPS = {
    "prometheus_nasa": "nasa",
    "prometheus_tds_nasa": "nasa",
    "prometheus_dyamond": "dyamond",
    "prometheus_tds_dyamond": "dyamond",
    "prometheus_dynamo": "dyamond",
    "prometheus_tds_dynamo": "dyamond",
    "prometheus_dyanamo": "dyamond",
    "prometheus_tds_dyanamo": "dyamond",
}
CACHE_TTL = 60
PAGE = 1000
UPSTREAM = os.environ.get("UPSTREAM_URL", "").rstrip("/")
# Catalogs extension paths naming one collection, alone or through its items.
# The server ignores `filter` on these, so the filters below refuse an
# unreadable collection themselves instead of filtering it.
CATALOG_COLLECTION = re.compile(
    r"^/catalogs/[^/]+/collections/(?P<collection_id>[^/]+)(?:/items(?:/[^/]+)?)?/?$",
    re.IGNORECASE,
)

# Upstream responses by path. Not keyed by caller: a collection's auth:groups
# belongs to the collection, so every caller shares one entry.
_cache: dict[str, tuple[float, Any]] = {}


def _roles(payload: dict[str, Any]) -> set[str]:
    """Role names in the token, wherever the realm put them."""
    groups = payload.get("groups") or []
    if isinstance(groups, str):
        groups = groups.replace(",", " ").split()
    client = (payload.get("resource_access") or {}).get(payload.get("azp")) or {}
    return {
        *groups,
        *(payload.get("realm_access") or {}).get("roles", []),
        *client.get("roles", []),
    }


def _readable(context: dict[str, Any]) -> list[str] | None:
    """auth:groups values this caller may read. None means unrestricted."""
    payload = context.get("payload") or {}
    roles = _roles(payload)
    if ADMIN_GROUP in roles:
        values = None
    else:
        values = sorted({value for group, value in GROUPS.items() if group in roles})
        values.append(PUBLIC)
    logger.info(
        "user=%s roles=%s readable=%s",
        payload.get("preferred_username") or payload.get("sub") or "-",
        sorted(roles),
        "all" if values is None else values,
    )
    return values


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


async def _collection_readable(collection_id: str, values: list[str]) -> bool:
    """Whether the collection shares an auth:groups value with `values`."""
    collection = await asyncio.to_thread(
        _get, f"/collections/{quote(collection_id, safe='')}"
    )
    return bool(set(collection.get("auth:groups") or []).intersection(values))


async def _catalog_collection_id(
    context: dict[str, Any], values: list[str]
) -> str | None:
    """Collection named by a catalog path, refused with a 404 if unreadable."""
    match = CATALOG_COLLECTION.match((context.get("req") or {}).get("path") or "")
    if not match:
        return None
    if not await _collection_readable(match["collection_id"], values):
        raise HTTPException(status_code=404, detail="Record not found.")
    return match["collection_id"]


@dataclasses.dataclass
class CollectionsFilter:
    """Collections sharing an auth:groups value with the caller."""

    async def __call__(self, context: dict[str, Any]) -> str | dict[str, Any]:
        values = _readable(context)
        if values is None:
            return "1=1"

        await _catalog_collection_id(context, values)
        return _expr(values)


@dataclasses.dataclass
class ItemsFilter:
    """Items of those collections.

    /collections/{id}/items and .../items/{item_id} name their collection, so
    that one collection is the whole question - one lookup, whatever the size
    of the catalogue. So do the catalog paths to the same items. Only /search,
    which names none, has to resolve the set.
    """

    async def __call__(self, context: dict[str, Any]) -> str | dict[str, Any]:
        values = _readable(context)
        if values is None:
            return "1=1"

        if await _catalog_collection_id(context, values):
            return "1=1"

        collection_id = ((context.get("req") or {}).get("path_params") or {}).get(
            "collection_id"
        )
        if collection_id:
            return "1=1" if await _collection_readable(collection_id, values) else "1=0"

        ids = await asyncio.to_thread(_readable_collection_ids, values)
        return {"op": "in", "args": [{"property": "collection"}, ids]} if ids else "1=0"
