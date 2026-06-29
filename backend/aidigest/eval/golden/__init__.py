"""Golden datasets for the validation harness.

Small, hand-authored JSON fixtures used by tests + nightly recall checks:

  * ``busy_day.json``  — multiple cross-source stories incl. a BREAKTHROUGH.
  * ``quiet_day.json`` — nothing major; the digest must say so honestly.

Each fixture is ``{name, description, date, quiet_expected,
expected_overall_tier, items:[...]}`` where every item maps to ``Item.create``
kwargs. ``load_golden`` returns the raw dict; ``golden_items`` materializes the
``items`` into frozen ``Item`` objects (idempotent content-hash ids).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from aidigest.models import Family, Item

_GOLDEN_DIR = Path(__file__).resolve().parent


def golden_path(name: str) -> Path:
    """Absolute path to a golden fixture JSON by short name (e.g. 'busy_day')."""
    return _GOLDEN_DIR / f"{name}.json"


def load_golden(name: str) -> dict:
    """Load a golden fixture dict by short name. Raises if missing/invalid."""
    path = golden_path(name)
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or "items" not in data:
        raise ValueError(f"golden fixture {name!r} is malformed (missing 'items')")
    return data


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def golden_items(name: str) -> list[Item]:
    """Materialize a golden fixture's ``items`` into frozen ``Item`` objects."""
    data = load_golden(name)
    items: list[Item] = []
    for raw in data["items"]:
        items.append(
            Item.create(
                source=str(raw["source"]),
                family=Family(raw["family"]),
                title=str(raw["title"]),
                url=raw.get("url"),
                author=raw.get("author"),
                published_at=_parse_dt(raw.get("published_at")),
                raw_text=str(raw.get("raw_text", "")),
                metrics=dict(raw.get("metrics") or {}),
                raw=dict(raw.get("raw") or {}),
            )
        )
    return items


__all__ = ["golden_path", "load_golden", "golden_items"]
