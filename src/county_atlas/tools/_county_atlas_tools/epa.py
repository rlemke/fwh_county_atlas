"""Tier-2 EPA join: facility points within the county.

TRI (Toxics Release Inventory) facilities are fetched per-county from EPA
Envirofacts (``data.epa.gov/efservice/TRI_FACILITY``) by ``STATE_ABBR`` +
``COUNTY_NAME`` and returned as a point FeatureCollection — the same shape as an
OSM point layer, so the renderer draws them identically (``public`` privacy).

Not wired here: EPA Superfund / Brownfields. They live behind the EMEF ArcGIS
MapServer (``geopub.epa.gov/.../EMEF/efpoints``), which only serves the *national*
set (``where=1=1``) — its county / bbox filters 400. Wiring them per-county needs a
national fetch cached once + a client-side bbox filter; left as a TODO rather than a
national download per county. The catalog keeps them as "not available" meanwhile.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

TRI_BASE = "https://data.epa.gov/efservice/TRI_FACILITY"

STATE_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district-of-columbia": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI",
    "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new-hampshire": "NH", "new-jersey": "NJ",
    "new-mexico": "NM", "new-york": "NY", "north-carolina": "NC", "north-dakota": "ND",
    "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode-island": "RI", "south-carolina": "SC", "south-dakota": "SD", "tennessee": "TN",
    "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west-virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}


def _tri_points(state_abbr: str, county_name: str) -> list[dict]:
    cn = urllib.parse.quote(county_name.upper())
    url = f"{TRI_BASE}/STATE_ABBR/{state_abbr}/COUNTY_NAME/{cn}/ROWS/0:5000/JSON"
    rows = json.load(urllib.request.urlopen(url, timeout=90))
    feats = []
    for r in rows:
        if str(r.get("fac_closed_ind")) == "1":
            continue
        try:
            lon = float(r.get("pref_longitude") or r.get("fac_longitude"))
            lat = float(r.get("pref_latitude") or r.get("fac_latitude"))
        except (TypeError, ValueError):
            continue
        if not (-180 < lon < 180 and -90 < lat < 90):
            continue
        feats.append({"type": "Feature",
                      "geometry": {"type": "Point", "coordinates": [lon, lat]},
                      "properties": {"name": r.get("facility_name")}})
    return feats


def build_epa_points(state_slug: str, county_slug: str, layers: list[dict],
                     state_abbr: str, county_name: str, on_log=None) -> dict:
    """{layer_id: point FeatureCollection} for every catalog layer with epa_source=tri."""
    log = on_log or (lambda *_a, **_k: None)
    out: dict[str, dict] = {}
    for lyr in layers:
        if lyr.get("epa_source") != "tri":
            continue
        try:
            feats = _tri_points(state_abbr, county_name)
            if feats:
                out[lyr["id"]] = {"type": "FeatureCollection", "features": feats}
            log(f"epa {lyr['id']}: {len(feats)} facilities")
        except Exception as exc:
            log(f"epa {lyr['id']} skipped: {exc}")
    return out
