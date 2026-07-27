"""TIGER boundary overlays: block groups + unified school districts for a county.

Per-state TIGER/Line shapefiles (keyless). Block groups carry ``COUNTYFP`` (exact
filter); unified school districts don't, so they're bbox-filtered on the shape bbox.
Returned as line-only polygon FeatureCollections (the renderer draws them as
outlines via the layer's ``fill:none`` style). Census tracts are already fetched by
``census.fetch_tracts`` — atlas.py reuses that geometry for the tract overlay rather
than refetching here.
"""
from __future__ import annotations

import glob
import io
import os
import tempfile
import urllib.request
import zipfile

YEAR = os.environ.get("FW_ATLAS_TIGER_YEAR", "2024")


def _load(url: str):
    import shapefile  # pyshp
    data = urllib.request.urlopen(url, timeout=180).read()
    tmp = tempfile.mkdtemp()
    zipfile.ZipFile(io.BytesIO(data)).extractall(tmp)
    r = shapefile.Reader(glob.glob(os.path.join(tmp, "*.shp"))[0])
    return r, {f[0]: i for i, f in enumerate(r.fields[1:])}


def _bbox_hit(shape, bbox) -> bool:
    minx, miny, maxx, maxy = shape.bbox  # pyshp per-shape bbox
    return not (maxx < bbox[0] or minx > bbox[2] or maxy < bbox[1] or miny > bbox[3])


def build_boundaries(state_fips: str, county_fips: str, bbox, layers: list[dict],
                     on_log=None) -> dict:
    """{layer_id: line-polygon FeatureCollection} for boundary_source block_groups/school_districts."""
    log = on_log or (lambda *_a, **_k: None)
    out: dict[str, dict] = {}
    for lyr in layers:
        src = lyr.get("boundary_source")
        try:
            if src == "block_groups":
                r, fi = _load(f"https://www2.census.gov/geo/tiger/TIGER{YEAR}/BG/"
                              f"tl_{YEAR}_{state_fips}_bg.zip")
                feats = [{"type": "Feature", "geometry": sh.__geo_interface__, "properties": {}}
                         for rec, sh in zip(r.records(), r.shapes())
                         if rec[fi["COUNTYFP"]] == county_fips]
            elif src == "school_districts":
                r, _fi = _load(f"https://www2.census.gov/geo/tiger/TIGER{YEAR}/UNSD/"
                               f"tl_{YEAR}_{state_fips}_unsd.zip")
                feats = [{"type": "Feature", "geometry": sh.__geo_interface__, "properties": {}}
                         for sh in r.shapes() if _bbox_hit(sh, bbox)]
            else:
                continue
            if feats:
                out[lyr["id"]] = {"type": "FeatureCollection", "features": feats}
            log(f"boundary {lyr['id']}: {len(feats)} features")
        except Exception as exc:
            log(f"boundary {lyr['id']} skipped: {exc}")
    return out
