"""Tier-2 NOAA join: GHCN weather-station points within the county.

County climate is a single value — it doesn't make a *within-county* choropleth — so
the NOAA layer that actually maps at county scale is the **weather stations** (GHCN-Daily
station inventory) with their names + elevation, as points.

The station inventory (``ghcnd-stations.txt``, ~10 MB) is fetched ONCE and cached in the
object store under ``county-atlas/_shared/`` — then every county build reads the cache and
filters to its bbox. (This is the shared-national-cache pattern the EPA Superfund/
Brownfields layers still need.) Parsing reuses ``noaa_weather``'s ``parse_stations`` when
that domain is installed, else a built-in fixed-width parser.
"""
from __future__ import annotations

import urllib.request

try:
    from noaa_weather.tools._noaa_tools.ghcn_parse import parse_stations as _parse
    HAS_NOAA = True
except Exception:  # pragma: no cover
    _parse, HAS_NOAA = None, False

STATIONS_URL = "https://www.ncei.noaa.gov/pub/data/ghcn/daily/ghcnd-stations.txt"
CACHE_KEY = "county-atlas/_shared/ghcnd-stations.txt"


def _parse_min(text: str) -> list[dict]:
    """Fixed-width fallback: ID 1-11, LAT 13-20, LON 22-30, NAME 42-71."""
    out = []
    for line in text.splitlines():
        if len(line) < 42:
            continue
        try:
            out.append({"station_id": line[0:11].strip(),
                        "lat": float(line[12:20]), "lon": float(line[21:30]),
                        "name": line[41:71].strip()})
        except ValueError:
            continue
    return out


def _stations_text(s3, bucket: str) -> str:
    try:
        return s3.get_object(Bucket=bucket, Key=CACHE_KEY)["Body"].read().decode("latin-1")
    except Exception:
        text = urllib.request.urlopen(STATIONS_URL, timeout=180).read().decode("latin-1")
        try:
            s3.put_object(Bucket=bucket, Key=CACHE_KEY, Body=text.encode("latin-1"),
                          ContentType="text/plain")
        except Exception:
            pass
        return text


def build_station_points(bbox, layers: list[dict], s3, bucket: str, on_log=None) -> dict:
    """{layer_id: point FeatureCollection} for layers with ``noaa_source=stations``."""
    log = on_log or (lambda *_a, **_k: None)
    wanted = [l for l in layers if l.get("noaa_source") == "stations"]
    if not wanted:
        return {}
    try:
        text = _stations_text(s3, bucket)
        stations = _parse(text) if HAS_NOAA else _parse_min(text)
    except Exception as exc:
        log(f"noaa tier-2 skipped: {exc}")
        return {}
    minlon, minlat, maxlon, maxlat = bbox
    feats = []
    for s in stations:
        lat, lon = s.get("lat"), s.get("lon")
        if lat is None or lon is None:
            continue
        if minlat <= lat <= maxlat and minlon <= lon <= maxlon:
            feats.append({"type": "Feature",
                          "geometry": {"type": "Point", "coordinates": [lon, lat]},
                          "properties": {"name": s.get("name"), "id": s.get("station_id")}})
    log(f"noaa stations: {len(feats)} in county")
    out: dict[str, dict] = {}
    for lyr in wanted:
        if feats:
            out[lyr["id"]] = {"type": "FeatureCollection", "features": feats}
    return out
