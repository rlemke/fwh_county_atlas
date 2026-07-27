"""Shared library for the County Atlas domain.

- ``catalog``    — load layers.json (the layer registry)
- ``materialize``— osmium: county PBF -> per-layer GeoJSON
- ``render``     — GeoJSON layers -> self-contained interactive HTML atlas
- ``storage``    — backend-aware object-store I/O (county PBF in, atlas out)
- ``atlas``      — orchestration: list_counties / build_county_atlas / build_master_index
"""
from __future__ import annotations

from . import atlas, catalog, materialize, render, storage  # noqa: F401

__all__ = ["atlas", "catalog", "materialize", "render", "storage"]
