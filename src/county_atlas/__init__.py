"""County Atlas — a per-county information-map platform for Facetwork.

A map per US county plus a US -> state -> county master index. One generic,
catalog-driven renderer (``layers.json``) over the per-county OSM extracts produced
by ``osm.planet``; the fan-out (``BuildAtlasFanout``) mirrors the planet pipeline —
one ``BuildCountyAtlas`` task per county, distributed across the fleet.

Discovered by the Facetwork runner via the ``facetwork.domains`` entry point in
pyproject.toml::

    [project.entry-points."facetwork.domains"]
    county-atlas = "county_atlas:domain"
"""
from __future__ import annotations

from pathlib import Path

from facetwork.domains import DomainPackage

from .handlers import register_all_registry_handlers

domain = DomainPackage(
    name="county-atlas",
    ffl_dir=Path(__file__).parent / "ffl",
    register_handlers=register_all_registry_handlers,
)
