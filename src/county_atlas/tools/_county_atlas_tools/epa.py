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

# EMEF ArcGIS MapServer — national only (where=1=1); layer 0 = Superfund, 5 = Brownfields.
_EMEF = "https://geopub.epa.gov/arcgis/rest/services/EMEF/efpoints/MapServer"
EMEF_LAYER = {"superfund": 0, "brownfields": 5}
_PAGE, _MAX_PAGES = 1000, 60
# EMEF site-name property (lowercase keys); try these in order.
_NAME_KEYS = ("primary_name", "facility_name", "site_name", "name", "cleanup_name")

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


def _fetch_national(dataset: str) -> dict:
    """The whole national EMEF layer as a FeatureCollection (paginated by resultOffset)."""
    base = (f"{_EMEF}/{EMEF_LAYER[dataset]}/query?where=1%3D1&outFields=*"
            f"&returnGeometry=true&f=geojson")
    feats: list[dict] = []
    for page in range(_MAX_PAGES):
        url = f"{base}&resultOffset={page * _PAGE}&resultRecordCount={_PAGE}"
        d = json.load(urllib.request.urlopen(url, timeout=120))
        if isinstance(d, dict) and "error" in d:
            raise RuntimeError(d["error"].get("message", d["error"]))
        pf = d.get("features", [])
        feats.extend(pf)
        if not d.get("exceededTransferLimit") or len(pf) < _PAGE:
            break
    return {"type": "FeatureCollection", "features": feats}


def _cached_national(s3, bucket: str, dataset: str) -> dict:
    """National EMEF set from the shared object-store cache (``_shared/``), fetched once."""
    key = f"county-atlas/_shared/epa_{dataset}.geojson"
    try:
        return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    except Exception:
        fc = _fetch_national(dataset)
        try:
            s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(fc).encode("utf-8"),
                          ContentType="application/geo+json")
        except Exception:
            pass
        return fc


def _name(props: dict) -> str | None:
    for k in _NAME_KEYS:
        if props.get(k):
            return props[k]
    return None


def _clip_points(national: dict, bbox) -> list[dict]:
    minlon, minlat, maxlon, maxlat = bbox
    out = []
    for f in national.get("features", []):
        g = f.get("geometry") or {}
        c = g.get("coordinates") or []
        if g.get("type") == "Point" and len(c) >= 2 \
                and minlon <= c[0] <= maxlon and minlat <= c[1] <= maxlat:
            out.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [c[0], c[1]]},
                        "properties": {"name": _name(f.get("properties", {}))}})
    return out


def build_epa_points(state_slug: str, county_slug: str, layers: list[dict],
                     state_abbr: str | None, county_name: str | None,
                     bbox=None, s3=None, bucket=None, on_log=None) -> dict:
    """{layer_id: point FeatureCollection} for county EPA layers.

    ``tri`` is a per-county Envirofacts query (needs state_abbr + county_name);
    ``superfund`` / ``brownfields`` come from the shared national EMEF cache clipped
    to ``bbox`` (needs bbox + s3 + bucket). Any layer whose inputs are missing, or
    that errors, is skipped — never faked.
    """
    log = on_log or (lambda *_a, **_k: None)
    out: dict[str, dict] = {}
    for lyr in layers:
        src = lyr.get("epa_source")
        try:
            if src == "tri":
                if not (state_abbr and county_name):
                    continue
                feats = _tri_points(state_abbr, county_name)
            elif src in EMEF_LAYER:
                if not (bbox and s3 is not None and bucket):
                    continue
                feats = _clip_points(_cached_national(s3, bucket, src), bbox)
            else:
                continue
            if feats:
                out[lyr["id"]] = {"type": "FeatureCollection", "features": feats}
            log(f"epa {lyr['id']}: {len(feats)} facilities")
        except Exception as exc:
            log(f"epa {lyr['id']} skipped: {exc}")
    return out
