"""Materialize county-atlas OSM layers from a single county PBF via osmium.

Each ``osm_pbf`` layer's ``filter`` is a space-separated list of osmium
``tags-filter`` expressions (OR semantics). We filter, then ``osmium export`` to
GeoJSON. Requires the osmium-tool binary on the runner.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

HAS_OSMIUM = shutil.which("osmium") is not None


class MaterializeError(RuntimeError):
    """osmium filter/export failed or the binary is missing."""


def osmium_geojson(pbf: str, filt: str, workdir: Path) -> dict:
    if not HAS_OSMIUM:
        raise MaterializeError("osmium (osmium-tool) not found on PATH")
    exprs = filt.split()
    fpbf = workdir / "f.osm.pbf"
    fgj = workdir / "f.geojson"
    subprocess.run(["osmium", "tags-filter", pbf, *exprs, "-o", str(fpbf), "--overwrite"],
                   check=True, capture_output=True)
    r = subprocess.run(["osmium", "export", str(fpbf), "-f", "geojson", "-o", str(fgj),
                        "--overwrite"], capture_output=True)
    if r.returncode != 0 or not fgj.exists():
        return {"features": []}
    try:
        return json.loads(fgj.read_text())
    except Exception:
        return {"features": []}


def materialize_osm(pbf: str, layers: list[dict], skip: set[str] | None = None
                    ) -> tuple[dict, dict]:
    """Run every ``osm_pbf`` layer against ``pbf``.

    Returns ``(materialized, counts)`` where ``materialized[id]`` is a GeoJSON dict
    and ``counts[id]`` its feature count. ``skip`` omits heavy/redundant layer ids
    from rendering (they stay in the catalog).
    """
    skip = skip or set()
    work = Path(tempfile.mkdtemp())
    materialized: dict[str, dict] = {}
    counts: dict[str, int] = {}
    for lyr in layers:
        if lyr["id"] in skip:
            continue
        gj = osmium_geojson(pbf, lyr["source"]["filter"], work)
        materialized[lyr["id"]] = gj
        counts[lyr["id"]] = len(gj.get("features", []))
    return materialized, counts
