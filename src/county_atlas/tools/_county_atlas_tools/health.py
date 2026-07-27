"""Tier-2 health join: CDC PLACES tract prevalence -> per-county choropleths.

Reuses the census tract geometry (``census.fetch_tracts``) and the same choropleth
shape the census join produces, so the renderer draws them identically. Data is CDC
PLACES (BRFSS model-based small-area estimates at census-tract resolution), fetched
keyless from the CDC Socrata API by county FIPS. Every catalog health layer with a
``places`` measure id (e.g. ``OBESITY``, ``DIABETES``) becomes a tract choropleth.
Percentages, ``aggregate`` privacy — tract areas, never points.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from . import census

PLACES_RESOURCE = os.environ.get("FW_ATLAS_PLACES_RESOURCE", "cwsq-ngmh")  # tract-level


def _places_by_tract(county_fips_full: str) -> dict[str, dict]:
    """{tract_GEOID: {measureid: value}} for the county from CDC PLACES (tract level)."""
    q = {"countyfips": county_fips_full,
         "$select": "locationname,measureid,data_value", "$limit": "60000"}
    url = f"https://data.cdc.gov/resource/{PLACES_RESOURCE}.json?" + urllib.parse.urlencode(q)
    tok = os.environ.get("SOCRATA_APP_TOKEN", "").strip()
    req = urllib.request.Request(url, headers={"X-App-Token": tok} if tok else {})
    rows = json.load(urllib.request.urlopen(req, timeout=90))
    out: dict[str, dict] = {}
    for r in rows:
        try:
            out.setdefault(r["locationname"], {})[r["measureid"]] = float(r["data_value"])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def build_places_choropleths(state_slug: str, county_slug: str, layers: list[dict],
                             on_log=None, tracts: dict | None = None,
                             sf: str | None = None, cf: str | None = None) -> dict:
    """Build a tract choropleth for every catalog layer with a ``places`` measure id."""
    log = on_log or (lambda *_a, **_k: None)
    wanted = [l for l in layers if l.get("places")]
    if not wanted:
        return {}
    try:
        if sf is None or cf is None:
            sf, cf, _ = census.resolve(state_slug, county_slug)
        if tracts is None:
            tracts = census.fetch_tracts(sf, cf)
        vals = _places_by_tract(sf + cf)
        log(f"places: health data for {len(vals)} tracts")
    except Exception as exc:
        log(f"health tier-2 skipped: {exc}")
        return {}

    geoids = [g for g in tracts if g in vals]
    out: dict[str, dict] = {}
    for lyr in wanted:
        mid = lyr["places"]
        series = [vals[g].get(mid) for g in geoids]
        breaks, cls = census._classify(series)
        feats = [{"geometry": tracts[g], "value": series[i], "cls": cls[i]}
                 for i, g in enumerate(geoids)]
        out[lyr["id"]] = {
            "features": feats,
            "legend": {"label": lyr["label"], "fmt": "pct", "worse": "high",
                       "breaks": breaks, "colors": census.RAMP, "nodata": census.NODATA},
        }
    return out
