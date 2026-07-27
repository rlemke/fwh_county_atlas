"""Tier-2 USGS join: earthquakes (points) + principal aquifers (polygons) in the county.

Both are geographic features that vary within a county, so they render as ordinary
map layers (fed into ``materialized`` like the OSM layers), scoped by the county bbox:

- **earthquakes** — USGS FDSN event API, a bbox query (``minmagnitude`` default 2.5).
  Most counties legitimately return none — a low-seismicity county shows an empty layer,
  not faked data.
- **aquifers** — the USGS "Principal Aquifers of the United States" ArcGIS FeatureServer
  (the same service ``save_earth`` uses), queried by envelope. Polygons.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

FDSN = "https://earthquake.usgs.gov/fdsnws/event/1/query"
AQUIFER = ("https://services1.arcgis.com/RQG3sksSXcoDoIfj/arcgis/rest/services/"
           "Principal_Aquifers_of_the_United_States/FeatureServer/0/query")
MIN_MAG = float(os.environ.get("FW_ATLAS_QUAKE_MINMAG", "2.5"))


def _earthquakes(bbox) -> list[dict]:
    minlon, minlat, maxlon, maxlat = bbox
    q = {"format": "geojson", "minmagnitude": MIN_MAG, "limit": 2000,
         "minlatitude": minlat, "maxlatitude": maxlat,
         "minlongitude": minlon, "maxlongitude": maxlon}
    d = json.load(urllib.request.urlopen(f"{FDSN}?{urllib.parse.urlencode(q)}", timeout=60))
    out = []
    for f in d.get("features", []):
        c = f.get("geometry", {}).get("coordinates") or []
        if len(c) >= 2:
            out.append({"type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [c[0], c[1]]},
                        "properties": {"name": f["properties"].get("place"),
                                       "mag": f["properties"].get("mag")}})
    return out


def _arcgis_envelope(url: str, bbox) -> list[dict]:
    minlon, minlat, maxlon, maxlat = bbox
    q = {"where": "1=1", "geometry": f"{minlon},{minlat},{maxlon},{maxlat}",
         "geometryType": "esriGeometryEnvelope", "inSR": "4326",
         "spatialRel": "esriSpatialRelIntersects", "outFields": "*",
         "returnGeometry": "true", "f": "geojson"}
    d = json.load(urllib.request.urlopen(f"{url}?{urllib.parse.urlencode(q)}", timeout=90))
    return d.get("features", [])


def build_usgs(bbox, layers: list[dict], on_log=None) -> dict:
    """{layer_id: FeatureCollection} for every layer with a ``usgs_source`` we handle."""
    log = on_log or (lambda *_a, **_k: None)
    out: dict[str, dict] = {}
    for lyr in layers:
        src = lyr.get("usgs_source")
        try:
            if src == "earthquakes":
                feats = _earthquakes(bbox)
            elif src == "aquifers":
                feats = _arcgis_envelope(AQUIFER, bbox)
            else:
                continue
            if feats:
                out[lyr["id"]] = {"type": "FeatureCollection", "features": feats}
            log(f"usgs {lyr['id']}: {len(feats)} features")
        except Exception as exc:
            log(f"usgs {lyr['id']} skipped: {exc}")
    return out
