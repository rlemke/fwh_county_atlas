"""Load the County Atlas layer catalog (layers.json).

The catalog is the single source of truth for what layers exist, where their data
comes from, their privacy tier, and how they render. It ships inside the package at
``county_atlas/layers.json``; override with ``FW_ATLAS_LAYERS=/path``.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path


def catalog_path() -> Path:
    env = os.environ.get("FW_ATLAS_LAYERS")
    if env:
        return Path(env)
    # .../county_atlas/tools/_county_atlas_tools/catalog.py -> parents[2] = county_atlas
    return Path(__file__).resolve().parents[2] / "layers.json"


@lru_cache(maxsize=4)
def load_catalog() -> dict:
    return json.loads(catalog_path().read_text())


def osm_layers(tier_max: int = 1) -> list[dict]:
    """The layers that render directly from a county PBF (tier <= tier_max)."""
    return [l for l in load_catalog()["layers"]
            if l["source"]["kind"] == "osm_pbf" and l["tier"] <= tier_max]


def categories() -> list[str]:
    return load_catalog()["categories"]
