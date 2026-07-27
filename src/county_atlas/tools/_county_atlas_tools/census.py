"""Tier-2 census join: ACS values -> census-tract geometry -> per-county choropleths.

Reuses ``census_us.tools._lib.metrics`` (the ACS-column registry + ``compute_metric``
formula — the single source of truth already used by the census-us domain) so the
atlas doesn't reinvent the demographic math. Geometry is the keyless TIGER/Line tract
shapefile; ACS values come from the Census API (needs ``CENSUS_API_KEY`` — present in
the fleet runner env). Every catalog census layer with a ``metric`` field becomes a
tract choropleth for the county; on any failure the layer degrades to "not available".
"""
from __future__ import annotations

import io
import json
import os
import re
import tempfile
import urllib.parse
import urllib.request
import zipfile

try:  # the census-us domain provides the metric registry; optional dependency
    from census_us.tools._lib.metrics import METRICS, compute_metric
    _METRIC = {m.key: m for m in METRICS}
    HAS_METRICS = True
except Exception:  # pragma: no cover
    _METRIC, compute_metric, HAS_METRICS = {}, None, False

ACS_YEAR = os.environ.get("FW_ATLAS_ACS_YEAR", "2023")
TIGER_YEAR = os.environ.get("FW_ATLAS_TIGER_YEAR", "2024")
# 5-class sequential ramp (matches the atlas chart-ink accent).
RAMP = ["#e2ede9", "#a7d0c5", "#5fae9c", "#2b8a76", "#0f6b5c"]
NODATA = "#cfd6d1"

STATE_FIPS = {
    "alabama": "01", "alaska": "02", "arizona": "04", "arkansas": "05", "california": "06",
    "colorado": "08", "connecticut": "09", "delaware": "10", "district-of-columbia": "11",
    "florida": "12", "georgia": "13", "hawaii": "15", "idaho": "16", "illinois": "17",
    "indiana": "18", "iowa": "19", "kansas": "20", "kentucky": "21", "louisiana": "22",
    "maine": "23", "maryland": "24", "massachusetts": "25", "michigan": "26",
    "minnesota": "27", "mississippi": "28", "missouri": "29", "montana": "30",
    "nebraska": "31", "nevada": "32", "new-hampshire": "33", "new-jersey": "34",
    "new-mexico": "35", "new-york": "36", "north-carolina": "37", "north-dakota": "38",
    "ohio": "39", "oklahoma": "40", "oregon": "41", "pennsylvania": "42",
    "rhode-island": "44", "south-carolina": "45", "south-dakota": "46", "tennessee": "47",
    "texas": "48", "utah": "49", "vermont": "50", "virginia": "51", "washington": "53",
    "west-virginia": "54", "wisconsin": "55", "wyoming": "56",
}


class CensusError(RuntimeError):
    """A tier-2 census fetch/join step failed (missing key, network, no data)."""


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _api_key() -> str:
    key = os.environ.get("CENSUS_API_KEY", "").strip()
    if not key:
        raise CensusError("CENSUS_API_KEY not set — tier-2 census layers need it")
    return key


def _acs(get_cols: str, geo_for: str, geo_in: str = "") -> list[list[str]]:
    q = {"get": get_cols, "for": geo_for, "key": _api_key()}
    if geo_in:
        q["in"] = geo_in
    url = (f"https://api.census.gov/data/{ACS_YEAR}/acs/acs5?"
           + urllib.parse.urlencode(q, safe=":+* "))
    with urllib.request.urlopen(url, timeout=90) as r:
        return json.load(r)


def resolve(state_slug: str, county_slug: str) -> tuple[str, str, str]:
    """(state_fips, county_fips, county_name) via an ACS NAME query.

    Shared by the census / health / epa tier-2 joins so they agree on FIPS + the
    canonical county name (e.g. ``"Coos"`` — used by EPA's county filter).
    """
    sf = STATE_FIPS.get(state_slug)
    if not sf:
        raise CensusError(f"unknown state slug: {state_slug}")
    rows = _acs("NAME", "county:*", f"state:{sf}")
    header, body = rows[0], rows[1:]
    ci = {h: i for i, h in enumerate(header)}
    for row in body:
        # NAME e.g. "Coos County, Oregon" -> leading part; strip the type word
        raw = row[ci["NAME"]].split(",")[0]
        bare = re.sub(r"\s+(County|Parish|Borough|Census Area|Municipality)$", "", raw)
        if re.sub(r"-(county|parish|borough|census-area|municipality)$", "", _slug(raw)) == county_slug:
            return sf, row[ci["county"]], bare
    raise CensusError(f"county {county_slug} not found in state {state_slug}")


def county_fips(state_slug: str, county_slug: str) -> tuple[str, str]:
    sf, cf, _name = resolve(state_slug, county_slug)
    return sf, cf


def fetch_tracts(state_fips: str, county_fips: str, s3=None, bucket=None) -> dict[str, dict]:
    """Public alias — {tract_GEOID: geometry} for the county (keyless TIGER).

    When ``s3`` is given, the per-state tract shapefile is pulled from (or seeded into)
    the shared cache, so a fan-out downloads each state's file only once.
    """
    return _tiger_tracts(state_fips, county_fips, s3=s3, bucket=bucket)


def _tiger_tracts(state_fips: str, county_fips: str, s3=None, bucket=None) -> dict[str, dict]:
    """{tract_GEOID: geometry} for the county, from the keyless TIGER tract shapefile."""
    import shapefile  # pyshp
    url = (f"https://www2.census.gov/geo/tiger/TIGER{TIGER_YEAR}/TRACT/"
           f"tl_{TIGER_YEAR}_{state_fips}_tract.zip")
    if s3 is not None:
        from . import storage
        data = storage.cached_bytes(s3, f"tiger/{TIGER_YEAR}_tract_{state_fips}.zip",
                                    url, bucket=bucket or storage.BUCKET)
    else:
        with urllib.request.urlopen(url, timeout=180) as r:
            data = r.read()
    tmp = tempfile.mkdtemp()
    zipfile.ZipFile(io.BytesIO(data)).extractall(tmp)
    import glob
    shp = glob.glob(os.path.join(tmp, "*.shp"))[0]
    reader = shapefile.Reader(shp)
    fields = [f[0] for f in reader.fields[1:]]
    fi = {f: i for i, f in enumerate(fields)}
    out: dict[str, dict] = {}
    for rec, shape in zip(reader.records(), reader.shapes()):
        if rec[fi["COUNTYFP"]] != county_fips:
            continue
        out[rec[fi["GEOID"]]] = shape.__geo_interface__
    return out


def _acs_tract_values(state_fips: str, county_fips: str, cols: list[str]) -> dict[str, dict]:
    """{tract_GEOID: {col: float}} for the metric columns across the county's tracts."""
    rows = _acs(",".join(cols), "tract:*", f"state:{state_fips}+county:{county_fips}")
    header, body = rows[0], rows[1:]
    ci = {h: i for i, h in enumerate(header)}
    out: dict[str, dict] = {}
    for row in body:
        geoid = row[ci["state"]] + row[ci["county"]] + row[ci["tract"]]
        props = {}
        for c in cols:
            try:
                props[c] = float(row[ci[c]])
            except (TypeError, ValueError):
                props[c] = None
        out[geoid] = props
    return out


def build_direct_choropleths(state_slug: str, county_slug: str, layers: list[dict],
                             on_log=None, tracts: dict | None = None,
                             sf: str | None = None, cf: str | None = None) -> dict:
    """Tract choropleths from layers that name raw ACS columns via ``census_var``.

    ``census_var`` is either ``{"raw": col, "fmt": ...}`` (a direct value) or
    ``{"num": [cols], "den": col, "fmt": "pct"}`` (a summed-ratio percentage). For the
    metrics not in the census-us registry (home value, building age, commute, language,
    disability). Same shape as ``build_census_choropleths``.
    """
    log = on_log or (lambda *_a, **_k: None)
    wanted = [l for l in layers if l.get("census_var")]
    if not wanted:
        return {}
    try:
        if sf is None or cf is None:
            sf, cf, _ = resolve(state_slug, county_slug)
        cols: set[str] = set()
        for l in wanted:
            v = l["census_var"]
            if v.get("raw"):
                cols.add(v["raw"])
            cols.update(v.get("num", []))
            if v.get("den"):
                cols.add(v["den"])
        if tracts is None:
            tracts = _tiger_tracts(sf, cf)
        acs = _acs_tract_values(sf, cf, sorted(cols))
        log(f"census-direct: {len(acs)} tracts, {len(cols)} cols")
    except Exception as exc:
        log(f"census-direct skipped: {exc}")
        return {}

    geoids = [g for g in tracts if g in acs]
    out: dict[str, dict] = {}
    for lyr in wanted:
        v = lyr["census_var"]
        series = []
        for g in geoids:
            p = acs[g]
            if v.get("raw"):
                val = p.get(v["raw"])
                if val is not None and val < 0:   # ACS uses negatives as annotations
                    val = None
            else:
                den = p.get(v["den"])
                val = (sum(p.get(c) or 0 for c in v["num"]) / den * 100) if den else None
            series.append(val)
        breaks, cls = _classify(series)
        out[lyr["id"]] = {
            "features": [{"geometry": tracts[g], "value": series[i], "cls": cls[i]}
                         for i, g in enumerate(geoids)],
            "legend": {"label": lyr["label"], "fmt": v.get("fmt", "pct"),
                       "worse": v.get("worse", "high"), "breaks": breaks,
                       "colors": RAMP, "nodata": NODATA},
        }
    return out


def _classify(values: list[float]) -> tuple[list[float], list[int]]:
    """Quantile breaks (5 classes) + a class index per value (None -> -1)."""
    real = sorted(v for v in values if v is not None)
    if not real:
        return [], [-1] * len(values)
    n = len(RAMP)
    breaks = [real[min(len(real) - 1, int(round(i * len(real) / n)))] for i in range(1, n)]
    cls = []
    for v in values:
        if v is None:
            cls.append(-1)
        else:
            k = 0
            while k < len(breaks) and v > breaks[k]:
                k += 1
            cls.append(k)
    return breaks, cls


def build_census_choropleths(state_slug: str, county_slug: str, layers: list[dict],
                             on_log=None, tracts: dict | None = None,
                             sf: str | None = None, cf: str | None = None) -> dict:
    """For every catalog layer with a ``metric``, build a tract choropleth for the county.

    Returns ``{layer_id: {"features": [{geometry, value, cls}], "legend": {...}}}``.
    Layers whose metric is unknown/unavailable, or the whole set on a hard failure,
    are simply omitted (the atlas then shows them as "not available").
    """
    log = on_log or (lambda *_a, **_k: None)
    wanted = [l for l in layers if l.get("metric") and l["metric"] in _METRIC]
    if not wanted or not HAS_METRICS:
        return {}
    try:
        if sf is None or cf is None:
            sf, cf, _ = resolve(state_slug, county_slug)
        metrics = [_METRIC[l["metric"]] for l in wanted]
        cols = sorted({c for m in metrics for c in (m.num if isinstance(m.num, list)
                       else [m.num] if m.num else []) + ([m.den] if m.den else [])
                       + ([m.raw] if m.raw else [])})
        cols = [c for c in cols if re.match(r"^B\d", c)]  # ACS B-table cols only
        if tracts is None:
            tracts = _tiger_tracts(sf, cf)
        acs = _acs_tract_values(sf, cf, cols)
        log(f"census: {len(tracts)} tracts, ACS for {len(acs)} tracts, {len(cols)} cols")
    except Exception as exc:
        log(f"census tier-2 skipped: {exc}")
        return {}

    geoids = [g for g in tracts if g in acs]
    out: dict[str, dict] = {}
    for lyr, m in zip(wanted, metrics):
        vals = [compute_metric(acs[g], m) for g in geoids]
        breaks, cls = _classify(vals)
        feats = [{"geometry": tracts[g], "value": vals[i], "cls": cls[i]}
                 for i, g in enumerate(geoids)]
        out[lyr["id"]] = {
            "features": feats,
            "legend": {"label": lyr["label"], "fmt": m.fmt, "worse": m.worse,
                       "breaks": breaks, "colors": RAMP, "nodata": NODATA},
        }
    return out
