"""County Atlas domain tests — offline (no osmium / no object store).

Cover the catalog contract, the pure renderer, and the handler dispatch. The
osmium materialization and S3 I/O are exercised by the CLI / an integration run.
"""
from __future__ import annotations

from county_atlas.handlers import county_atlas_handlers as h
from county_atlas.tools._county_atlas_tools import catalog, render, storage


def test_catalog_loads_and_is_well_formed():
    c = catalog.load_catalog()
    assert len(c["layers"]) > 50
    assert len(c["categories"]) == 14
    req = {"id", "label", "category", "geometry", "source", "privacy", "coverage", "tier"}
    assert all(req <= set(l) for l in c["layers"])
    # every layer's category is a declared category
    cats = set(c["categories"])
    assert all(l["category"] in cats for l in c["layers"])
    # privacy tiers are from the enforced set
    assert all(l["privacy"] in {"public", "aggregate", "generalized", "suppressed"}
               for l in c["layers"])


def test_osm_tier1_layers_are_pbf_backed():
    l1 = catalog.osm_layers(tier_max=1)
    assert len(l1) >= 40
    assert all(x["source"]["kind"] == "osm_pbf" and x["tier"] == 1 for x in l1)


def test_render_produces_self_contained_html():
    cat = catalog.load_catalog()
    materialized = {
        "osm.county_boundary": {"features": [{"geometry": {"type": "Polygon", "coordinates": [
            [[-124.5, 43.0], [-124.0, 43.0], [-124.0, 43.5], [-124.5, 43.5], [-124.5, 43.0]]]}}]},
        "osm.places": {"features": [{"geometry": {"type": "Point", "coordinates": [-124.2, 43.2]}}]},
        "osm.hospitals": {"features": [{"geometry": {"type": "Point", "coordinates": [-124.3, 43.3]}}]},
    }
    counts = {"osm.county_boundary": 1, "osm.places": 1, "osm.hospitals": 1}
    html = render.build_atlas_html(cat, materialized, counts, "oregon", "coos")
    assert "<svg" in html and "Coos County" in html
    assert 'data-layer="osm.county_boundary"' in html
    assert "maplibre" not in html and "http://" not in html  # self-contained, no external refs
    assert "1 live" in html or "live" in html


def test_dispatch_covers_the_three_facets():
    assert set(h._DISPATCH) == {
        "county.atlas.ListCounties",
        "county.atlas.BuildCountyAtlas",
        "county.atlas.BuildMasterIndex",
    }
    assert all(callable(v) for v in h._DISPATCH.values())


def test_storage_key_derivation():
    ck = "north-america/us/oregon/coos"
    assert storage.pbf_key(ck) == "north-america/us/oregon/coos-latest.osm.pbf"
    assert storage.atlas_html_key(ck) == "county-atlas/oregon/coos/index.html"
    assert storage.atlas_manifest_key(ck) == "county-atlas/oregon/coos/manifest.json"


# --- tier-2 census join (offline: no network / no API key) ---------------------

def test_census_classify_and_state_fips():
    from county_atlas.tools._county_atlas_tools import census
    assert census.STATE_FIPS["oregon"] == "41" and census.STATE_FIPS["district-of-columbia"] == "11"
    breaks, cls = census._classify([1, 2, 3, 4, 5, None, 10])
    assert len(breaks) == 4          # 5 classes -> 4 break points
    assert cls[5] == -1              # None value -> no-data class
    assert cls[0] <= cls[-1]         # monotonic with value


def test_census_degrades_gracefully_without_key(monkeypatch):
    from county_atlas.tools._county_atlas_tools import census
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    # a mapped metric layer, but no key -> returns {} (layer stays "not available")
    out = census.build_census_choropleths(
        "oregon", "coos", [{"id": "census.income", "metric": "median_income"}])
    assert out == {}


def test_every_metric_layer_maps_to_a_real_registry_key():
    # each catalog layer with a `metric` must reference an actual census-us metric
    from county_atlas.tools._county_atlas_tools import census
    if not census.HAS_METRICS:
        return  # census-us not installed in this env
    mapped = [l for l in catalog.load_catalog()["layers"] if l.get("metric")]
    assert mapped, "expected some census layers to declare a metric"
    assert all(l["metric"] in census._METRIC for l in mapped)


def test_render_choropleth_emits_legend_and_toggle():
    cat = catalog.load_catalog()
    mat = {"osm.county_boundary": {"features": [{"geometry": {"type": "Polygon", "coordinates": [
        [[-124.5, 43.0], [-124.0, 43.0], [-124.0, 43.5], [-124.5, 43.5], [-124.5, 43.0]]]}}]}}
    counts = {"osm.county_boundary": 1}
    choro = {"census.income": {
        "features": [{"geometry": {"type": "Polygon", "coordinates": [
            [[-124.4, 43.1], [-124.1, 43.1], [-124.1, 43.4], [-124.4, 43.1]]]},
            "value": 50000, "cls": 3}],
        "legend": {"label": "Median household income", "fmt": "dollar", "worse": "low",
                   "breaks": [30000, 45000, 60000, 80000],
                   "colors": ["#e2ede9", "#a7d0c5", "#5fae9c", "#2b8a76", "#0f6b5c"],
                   "nodata": "#cfd6d1"}}}
    html = render.build_atlas_html(cat, mat, counts, "oregon", "coos", choropleths=choro)
    assert 'data-choro="census.income"' in html
    assert "var CHORO=" in html and 'id="lgd"' in html
    assert "Median household income" in html
    assert "http://" not in html  # still self-contained


# --- tier-2 health (CDC PLACES) + EPA (TRI) -----------------------------------

def test_places_and_tri_catalog_flags():
    layers = catalog.load_catalog()["layers"]
    places = [l for l in layers if l.get("places")]
    assert len(places) >= 8
    assert all(l["geometry"] == "choropleth" and l["privacy"] == "aggregate" for l in places)
    tri = [l for l in layers if l.get("epa_source") == "tri"]
    assert tri and tri[0]["geometry"] == "point" and tri[0]["privacy"] == "public"


def test_epa_state_abbr_and_layer_filter():
    from county_atlas.tools._county_atlas_tools import epa
    assert epa.STATE_ABBR["oregon"] == "OR" and epa.STATE_ABBR["district-of-columbia"] == "DC"
    # no epa_source=tri layers -> no fetch, empty result (offline)
    assert epa.build_epa_points("oregon", "coos", [{"id": "x", "epa_source": "other"}],
                                "OR", "Coos") == {}


def test_health_degrades_gracefully_without_key(monkeypatch):
    from county_atlas.tools._county_atlas_tools import health
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    # a PLACES layer but no key to resolve FIPS -> returns {} (stays "not available")
    out = health.build_places_choropleths(
        "oregon", "coos", [{"id": "health.obesity", "places": "OBESITY"}])
    assert out == {}


# --- tier-2 USGS / FEMA / NOAA ------------------------------------------------

def test_usgs_fema_noaa_catalog_flags():
    layers = catalog.load_catalog()["layers"]
    assert any(l.get("usgs_source") == "earthquakes" for l in layers)
    assert any(l.get("usgs_source") == "aquifers" for l in layers)
    assert any(l.get("fema_source") == "nri" and l["geometry"] == "choropleth" for l in layers)
    assert any(l.get("noaa_source") == "stations" and l["geometry"] == "point" for l in layers)


def test_usgs_and_fema_empty_without_matching_layers():
    from county_atlas.tools._county_atlas_tools import fema, usgs
    assert usgs.build_usgs((-124.6, 42.8, -123.8, 43.7), [{"id": "x"}]) == {}
    assert fema.build_nri("41", "011", [{"id": "x"}]) == {}


def test_noaa_station_parse_and_no_layer_shortcircuit():
    from county_atlas.tools._county_atlas_tools import noaa
    line = f"{'USC00350197':<11} {43.5:>8.4f} {-123.5:>9.4f} {100.0:>6.1f}    TEST STATION"
    st = noaa._parse_min(line)
    assert len(st) == 1
    assert abs(st[0]["lat"] - 43.5) < 1e-6 and abs(st[0]["lon"] + 123.5) < 1e-6
    assert st[0]["name"].startswith("TEST STATION")
    # no noaa_source layers -> returns {} before touching the object store (s3=None ok)
    assert noaa.build_station_points((-125, 42, -123, 44), [{"id": "x"}], None, "b") == {}
