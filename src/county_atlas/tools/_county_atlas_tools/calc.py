"""Tier-3 calculated indicators — derived purely from tier-1 OSM + tier-2 census.

No new data sources; pure computation over what's already materialized:

- **ratio** — per-tract ratio of two census choropleths (e.g. annual rent ÷ income).
- **nearest_distance** — per-tract straight-line km from the tract centroid to the
  nearest base feature (OSM points, or polygon/line centroids). *Approximate:* a true
  N-minute isochrone needs network routing (a future ``osm.Network`` tier); we compute
  and label the straight-line distance honestly.
- **per_capita** — county-level count of an OSM layer per N residents (ACS population).
  Facilities are too sparse for a meaningful per-tract choropleth, so these are
  **indicator-panel stats**, not map layers.
"""
from __future__ import annotations

import math

from . import census, render

# County per-capita panel stats: (label, osm layer id, per-N residents).
PER_CAPITA = [
    ("Hospitals per 10k", "osm.hospitals", 10000),
    ("Clinics per 10k", "osm.clinics", 10000),
    ("Schools per 10k", "osm.schools", 10000),
    ("Fire stations per 10k", "osm.fire", 10000),
    ("Libraries per 10k", "osm.library", 10000),
]


def _centroid(geom):
    pts = []
    for gk, coords in render.iter_geoms(geom or {}):
        if gk == "pg" and coords:
            pts.extend(coords[0])
        elif gk == "ln":
            pts.extend(coords)
        elif gk == "pt":
            pts.append(coords)
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def _rep_points(materialized: dict, layer_id: str) -> list[tuple]:
    """Representative points for a layer (points as-is; polygons/lines → centroid)."""
    out = []
    for f in materialized.get(layer_id, {}).get("features", []):
        c = _centroid(f.get("geometry"))
        if c:
            out.append(c)
    return out


def _haversine(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    d = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(d))


def _choro(layer, feats, vals, fmt):
    breaks, cls = census._classify(vals)
    for i, f in enumerate(feats):
        f["cls"] = cls[i]
    return {"features": feats,
            "legend": {"label": layer["label"], "fmt": fmt, "worse": "high",
                       "breaks": breaks, "colors": census.RAMP, "nodata": census.NODATA}}


def build_calc(choropleths: dict, materialized: dict, calc_layers: list[dict],
               county_pop: float | None, on_log=None) -> tuple[dict, list]:
    """Returns ``(calc_choropleths, extra_indicators)``.

    ``extra_indicators`` is a list of ``(label, value_str, source_str)`` for the panel.
    """
    log = on_log or (lambda *_a, **_k: None)
    out_choro: dict[str, dict] = {}
    # a tract feature template (geometry) from any tier-2 tract choropleth
    tract_src = next((c for c in choropleths.values() if c.get("features")), None)

    for lyr in calc_layers:
        spec = lyr.get("calc")
        if not spec:
            continue
        op = spec.get("op")
        try:
            if op == "ratio":
                num = choropleths.get(spec["numerator"])
                den = choropleths.get(spec["denominator"])
                if not (num and den):
                    continue
                ann = spec.get("annualize", 1)
                vals, feats = [], []
                for nf, df in zip(num["features"], den["features"]):
                    a, b = nf.get("value"), df.get("value")
                    v = (a * ann / b * 100) if (a and b) else None
                    vals.append(v)
                    feats.append({"geometry": nf["geometry"], "value": v})
                out_choro[lyr["id"]] = _choro(lyr, feats, vals, "pct")
                log(f"calc {lyr['id']}: ratio over {len(feats)} tracts")

            elif op == "nearest_distance":
                base = _rep_points(materialized, spec["base"])
                if not base or not tract_src:
                    continue
                vals, feats = [], []
                for tf in tract_src["features"]:
                    ct = _centroid(tf["geometry"])
                    v = min(_haversine(ct, bp) for bp in base) if ct else None
                    vals.append(v)
                    feats.append({"geometry": tf["geometry"], "value": v})
                out_choro[lyr["id"]] = _choro(lyr, feats, vals, "km")
                log(f"calc {lyr['id']}: nearest-distance over {len(feats)} tracts")
        except Exception as exc:
            log(f"calc {lyr['id']} skipped: {exc}")

    indicators = []
    if county_pop:
        for label, layer_id, per in PER_CAPITA:
            n = len(materialized.get(layer_id, {}).get("features", []))
            indicators.append((label, f"{n / county_pop * per:.1f}",
                               f"{n} ÷ {int(county_pop):,} pop"))
    return out_choro, indicators
