#!/usr/bin/env python3
"""build-county-atlas — render one county's atlas HTML from a local PBF (offline CLI).

    build-county-atlas <county.osm.pbf> <state> <county> <out.html> [--tier N]

Uses the same _county_atlas_tools library the runner handler does; writes to a local
file instead of the object store. Handy for previewing a single county without a fleet.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _county_atlas_tools import catalog, materialize, render  # type: ignore


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("pbf")
    p.add_argument("state")
    p.add_argument("county")
    p.add_argument("out")
    p.add_argument("--tier", type=int, default=1)
    a = p.parse_args()

    cat = catalog.load_catalog()
    layers = catalog.osm_layers(tier_max=a.tier)
    materialized, counts = materialize.materialize_osm(
        a.pbf, layers, skip={"osm.landuse"})
    html = render.build_atlas_html(cat, materialized, counts, a.state, a.county)
    Path(a.out).write_text(html)
    live = sum(1 for v in counts.values() if v > 0)
    print(f"wrote {a.out}  | live layers: {live}/{len(cat['layers'])} | "
          f"features: {sum(counts.values()):,}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    # allow running the file directly: add the sibling _county_atlas_tools to the path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
