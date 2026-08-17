"""Custom collection and item filters for STAC Auth Proxy."""

import asyncio
import dataclasses
import json
import os
import time
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen


ADMIN_GROUP = "/eoapi-admin"

GROUP_AUTH_VALUES = {
    "/eoapi-noaa": "noaa",
    "/nasa-users": "nasa",
    "/dyamond-users": "dyamond",
}

COLLECTION_CACHE_TTL_SECONDS = 60
UPSTREAM_URL = os.environ.get("UPSTREAM_URL", "").rstrip("/")
_collection_cache: dict[tuple[str, ...], tuple[float, list[str]]] = {}


def _groups(context: dict[str, Any]) -> set[str]:
    groups = (context.get("payload") or {}).get("groups", [])

    if isinstance(groups, str):
        groups = groups.replace(",", " ").split()

    return {group for group in groups if isinstance(group, str)}


def _authorized_auth_values(groups: set[str]) -> list[str]:
    return [
        auth_value
        for group, auth_value in GROUP_AUTH_VALUES.items()
        if group in groups
    ]


def _auth_groups_filter(context: dict[str, Any]) -> str | dict[str, Any]:
    groups = _groups(context)

    if ADMIN_GROUP in groups:
        return "1=1"

    auth_values = _authorized_auth_values(groups)

    if not auth_values:
        return "1=0"

    if len(auth_values) == 1:
        return {
            "op": "a_contains",
            "args": [{"property": "auth:groups"}, auth_values],
        }

    return {
        "op": "a_overlaps",
        "args": [{"property": "auth:groups"}, auth_values],
    }


def _collection_auth_groups(collection: dict[str, Any]) -> set[str]:
    auth_groups = collection.get("auth:groups", [])

    if isinstance(auth_groups, str):
        auth_groups = auth_groups.replace(",", " ").split()

    return {auth_group for auth_group in auth_groups if isinstance(auth_group, str)}


def _collection_ids_filter(collection_ids: list[str]) -> str | dict[str, Any]:
    if not collection_ids:
        return "1=0"

    if len(collection_ids) == 1:
        return {
            "op": "=",
            "args": [{"property": "collection"}, collection_ids[0]],
        }

    return {
        "op": "in",
        "args": [{"property": "collection"}, collection_ids],
    }


def _next_href(payload: dict[str, Any], current_url: str) -> str | None:
    for link in payload.get("links", []):
        if link.get("rel") == "next" and link.get("href"):
            return urljoin(current_url, link["href"])

    return None


def _load_allowed_collection_ids(auth_values: tuple[str, ...]) -> list[str]:
    now = time.monotonic()
    cached = _collection_cache.get(auth_values)

    if cached and now - cached[0] < COLLECTION_CACHE_TTL_SECONDS:
        return cached[1]

    if not UPSTREAM_URL:
        return []

    auth_value_set = set(auth_values)
    collection_ids: list[str] = []
    url = f"{UPSTREAM_URL}/collections"

    while url:
        request = Request(url, headers={"Accept": "application/json"})

        with urlopen(request, timeout=10) as response:
            payload = json.load(response)

        for collection in payload.get("collections", []):
            if auth_value_set & _collection_auth_groups(collection):
                collection_id = collection.get("id")
                if isinstance(collection_id, str):
                    collection_ids.append(collection_id)

        url = _next_href(payload, url)

    collection_ids = sorted(set(collection_ids))
    _collection_cache[auth_values] = (now, collection_ids)
    return collection_ids


async def _allowed_collection_ids(context: dict[str, Any]) -> list[str] | None:
    groups = _groups(context)

    if ADMIN_GROUP in groups:
        return None

    auth_values = tuple(sorted(_authorized_auth_values(groups)))

    if not auth_values:
        return []

    return await asyncio.to_thread(_load_allowed_collection_ids, auth_values)


async def _items_auth_groups_filter(context: dict[str, Any]) -> str | dict[str, Any]:
    collection_ids = await _allowed_collection_ids(context)

    if collection_ids is None:
        return "1=1"

    return _collection_ids_filter(collection_ids)


@dataclasses.dataclass
class CollectionsFilter:
    """Allow collection reads based on collection auth:groups metadata."""

    async def __call__(self, context: dict[str, Any]) -> str | dict[str, Any]:
        return _auth_groups_filter(context)


@dataclasses.dataclass
class ItemsFilter:
    """Allow item reads for collections allowed by auth:groups metadata."""

    async def __call__(self, context: dict[str, Any]) -> str | dict[str, Any]:
        return await _items_auth_groups_filter(context)
