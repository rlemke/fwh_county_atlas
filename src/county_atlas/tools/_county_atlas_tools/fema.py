"""Tier-2 FEMA join: National Risk Index tract choropleth for the county.

FEMA's NRI Census-Tracts ArcGIS FeatureServer serves per-tract composite natural-hazard
``RISK_SCORE`` **with tract geometry**, queryable by county (``STCOFIPS``). So this
builds the choropleth directly from NRI's own geometry — no census-tract join needed —
in the same shape the census/health joins produce, so the renderer draws it identically.
``aggregate`` privacy (tract areas). Returns ``{}`` on any failure (layer stays
"not available"), never faked.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

from . import census

NRI_TRACTS = ("https://services.arcgis.com/XG15cJAlne2vxtgt/arcgis/rest/services/"
              "National_Risk_Index_Census_Tracts/FeatureServer/0/query")


def build_nri(state_fips: str, county_fips: str, layers: list[dict], on_log=None) -> dict:
    log = on_log or (lambda *_a, **_k: None)
    wanted = [l for l in layers if l.get("fema_source") == "nri"]
    if not wanted:
        return {}
    try:
        q = {"where": f"STCOFIPS='{state_fips}{county_fips}'",
             "outFields": "TRACTFIPS,RISK_SCORE,RISK_RATNG",
             "returnGeometry": "true", "f": "geojson"}
        d = json.load(urllib.request.urlopen(
            f"{NRI_TRACTS}?{urllib.parse.urlencode(q)}", timeout=90))
        feats = d.get("features", [])
        log(f"fema NRI: {len(feats)} tracts")
    except Exception as exc:
        log(f"fema tier-2 skipped: {exc}")
        return {}
    if not feats:
        return {}

    vals = []
    for f in feats:
        try:
            vals.append(float(f["properties"].get("RISK_SCORE")))
        except (TypeError, ValueError):
            vals.append(None)
    breaks, cls = census._classify(vals)
    out: dict[str, dict] = {}
    for lyr in wanted:
        out[lyr["id"]] = {
            "features": [{"geometry": feats[i].get("geometry"), "value": vals[i],
                          "cls": cls[i]} for i in range(len(feats))],
            "legend": {"label": lyr["label"], "fmt": "index", "worse": "high",
                       "breaks": breaks, "colors": census.RAMP, "nodata": census.NODATA},
        }
    return out
