"""Event-facet handlers for the County Atlas domain — thin layers over ``_county_atlas_tools``.

Facets (all in namespace ``county.atlas``):
- ``ListCounties``      — enumerate county-extract leaves (the fan-out unit)
- ``BuildCountyAtlas``  — build one county's atlas (download -> materialize -> render -> store)
- ``BuildMasterIndex``  — US -> state -> county index over the built atlases
"""
from __future__ import annotations

import os
from typing import Any

from ..tools._county_atlas_tools import atlas

NS = "county.atlas"


def handle_list_counties(p: dict[str, Any]) -> dict[str, Any]:
    log = p.get("_step_log")
    counties = atlas.list_counties(prefix=p.get("prefix", "north-america/us"),
                                   bucket=p.get("bucket", "osm-extracts"))
    if log:
        log(f"ListCounties: {len(counties)} counties under {p.get('prefix')}", level="success")
    return {"counties": counties, "count": len(counties)}


def handle_build_county_atlas(p: dict[str, Any]) -> dict[str, Any]:
    log = p.get("_step_log")
    try:
        res = atlas.build_county_atlas(
            county_key=p["county_key"], tier=int(p.get("tier", 1)),
            bucket=p.get("bucket", "osm-extracts"),
            on_log=(lambda m: log(m)) if log else None)
        if log:
            log(f"BuildCountyAtlas: {p['county_key']} -> {res['layer_count']} live layers, "
                f"{res['feature_count']:,} features", level="success")
        return res
    except Exception as exc:
        if log:
            log(f"BuildCountyAtlas: {exc}", level="error")
        raise


def handle_build_master_index(p: dict[str, Any]) -> dict[str, Any]:
    log = p.get("_step_log")
    res = atlas.build_master_index(prefix=p.get("prefix", "north-america/us"),
                                   bucket=p.get("bucket", "osm-extracts"),
                                   on_log=(lambda m: log(m)) if log else None)
    if log:
        log(f"BuildMasterIndex: {res['county_count']} counties -> {res['index_path']}",
            level="success")
    return res


_DISPATCH: dict[str, Any] = {
    f"{NS}.ListCounties": handle_list_counties,
    f"{NS}.BuildCountyAtlas": handle_build_county_atlas,
    f"{NS}.BuildMasterIndex": handle_build_master_index,
}


def handle(payload: dict) -> dict:
    facet = payload["_facet_name"]
    handler = _DISPATCH.get(facet)
    if handler is None:
        raise ValueError(f"Unknown facet: {facet}")
    return handler(payload)


def register_handlers(runner) -> None:
    for facet_name in _DISPATCH:
        # download + osmium are blocking I/O (no heartbeat) -> rely on the global
        # execution timeout / the FFL Timeout mixin.
        runner.register_handler(
            facet_name=facet_name,
            module_uri=f"file://{os.path.abspath(__file__)}",
            entrypoint="handle",
            timeout_ms=0,
        )


def register_poller(poller) -> None:
    for facet_name, handler in _DISPATCH.items():
        poller.register(facet_name, handler)
