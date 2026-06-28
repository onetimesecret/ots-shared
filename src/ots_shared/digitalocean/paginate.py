# src/ots_shared/digitalocean/paginate.py

"""Pagination + client-side name filtering for the DigitalOcean list APIs.

DigitalOcean has **no get-by-name** endpoint and paginates everything at 20
items/page by default (``per_page`` max 200). Every ``lots do`` list/lookup goes
through here so the 250/min + 5,000/hr rate budget is respected uniformly: list
with ``per_page=200`` to minimise round-trips, follow ``links.pages.next`` until
exhausted, and wrap each page fetch in the bounded :func:`with_backoff` so a
single transient 429 / 5xx does not abort a long enumeration.

``pydo`` is dict-in/dict-out, so these helpers operate on plain dicts — no SDK
types leak out.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .errors import with_backoff

# DO's per_page ceiling; lists default to 20, so we always pass this.
PER_PAGE_MAX = 200


def list_all(
    fetch_page: Callable[..., Mapping[str, Any]],
    key: str,
    *,
    per_page: int = PER_PAGE_MAX,
    max_pages: int = 1000,
    sleep: Callable[[float], None] | None = None,
) -> list[dict[str, Any]]:
    """Collect every item across all pages of a DigitalOcean list endpoint.

    ``fetch_page(per_page=, page=)`` is a thunk over e.g. ``client.droplets.list``;
    ``key`` is the envelope list key (``"droplets"``, ``"volumes"`` …). Follows
    ``links.pages.next`` until absent or ``max_pages`` is reached (a runaway
    guard). Each page fetch is retried with bounded backoff.
    """
    items: list[dict[str, Any]] = []
    page = 1
    backoff_kwargs = {"sleep": sleep} if sleep is not None else {}
    while page <= max_pages:
        resp = with_backoff(
            lambda p=page: fetch_page(per_page=per_page, page=p),
            **backoff_kwargs,  # type: ignore[arg-type]
        )
        items.extend(resp.get(key, []) or [])
        links = resp.get("links") or {}
        pages = links.get("pages") or {}
        if not pages.get("next"):
            break
        page += 1
    return items


def find_by_name(
    items: list[dict[str, Any]],
    name: str,
    *,
    kind: str,
    id_key: str = "id",
) -> dict[str, Any]:
    """Return the single item whose ``name`` matches, failing loud otherwise.

    DigitalOcean names are not unique, so a duplicate is fail-loud — the
    operator must disambiguate by id. ``kind`` is used only in the error message
    (e.g. ``"droplet"``); ``id_key`` names the id field reported on a duplicate.
    """
    matches = [i for i in items if i.get("name") == name]
    if not matches:
        raise SystemExit(f"{kind.capitalize()} '{name}' not found")
    if len(matches) > 1:
        ids = ", ".join(str(i.get(id_key, "?")) for i in matches)
        raise SystemExit(f"Multiple {kind}s named '{name}' ({ids}); specify an id to disambiguate")
    return matches[0]
