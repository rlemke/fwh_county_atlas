"""Tier-2 HUD join: public / subsidized housing points within the county.

HUD's Public Housing Buildings ArcGIS FeatureServer (keyless), queried by the county
bbox envelope -> point FeatureCollection, rendered like an OSM point layer.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

HUD = ("https://services.arcgis.com/VTyQ9soqVukalItT/arcgis/rest/services/"
       "Public_Housing_Buildings/FeatureServer/0/query")


def build_hud(bbox, layers: list[dict], on_log=None) -> dict:
    log = on_log or (lambda *_a, **_k: None)
    out: dict[str, dict] = {}
    for lyr in layers:
        if lyr.get("hud_source") != "public_housing":
            continue
        try:
            minlon, minlat, maxlon, maxlat = bbox
            q = {"where": "1=1", "geometry": f"{minlon},{minlat},{maxlon},{maxlat}",
                 "geometryType": "esriGeometryEnvelope", "inSR": "4326",
                 "spatialRel": "esriSpatialRelIntersects",
                 "outFields": "PROJECT_NAME,FORMAL_PARTICIPANT_NAME",
                 "returnGeometry": "true", "f": "geojson"}
            d = json.load(urllib.request.urlopen(
                f"{HUD}?{urllib.parse.urlencode(q)}", timeout=60))
            feats = []
            for f in d.get("features", []):
                g = f.get("geometry") or {}
                c = g.get("coordinates") or []
                if g.get("type") == "Point" and len(c) >= 2:
                    p = f.get("properties", {})
                    feats.append({"type": "Feature",
                                  "geometry": {"type": "Point", "coordinates": [c[0], c[1]]},
                                  "properties": {"name": p.get("PROJECT_NAME")
                                                 or p.get("FORMAL_PARTICIPANT_NAME")}})
            if feats:
                out[lyr["id"]] = {"type": "FeatureCollection", "features": feats}
            log(f"hud {lyr['id']}: {len(feats)} buildings")
        except Exception as exc:
            log(f"hud {lyr['id']} skipped: {exc}")
    return out
