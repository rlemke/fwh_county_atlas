# CLAUDE.md — county-atlas

Facetwork domain: **a map per US county + a US → state → county master index**,
catalog-driven. Registered via the `facetwork.domains` entry point in `pyproject.toml`
(`county-atlas = "county_atlas:domain"`); discovered by `fw runner start --domain
county-atlas` and `fw ffl seed`.

## Shape

```
src/county_atlas/
├── __init__.py                 # exports `domain: DomainPackage`
├── layers.json                 # THE layer catalog (single source of truth)
├── ffl/atlas.ffl               # county.atlas facets + workflows (incl. the fan-out)
├── handlers/county_atlas_handlers.py   # _DISPATCH: ListCounties/BuildCountyAtlas/BuildMasterIndex
└── tools/
    ├── build-county-atlas.py           # offline single-county CLI
    └── _county_atlas_tools/            # shared library
        ├── catalog.py      # load layers.json
        ├── materialize.py  # osmium: county PBF -> per-layer GeoJSON
        ├── render.py       # GeoJSON -> self-contained HTML (SVG map + checkbox tree)
        ├── storage.py      # object-store I/O (county PBF in / atlas out)
        └── atlas.py        # orchestration: list_counties / build_county_atlas / build_master_index
```

## Core ideas (do not break)

- **The catalog drives everything.** Adding a layer = a row in `layers.json`, never new
  code. A layer spec is `{id,label,category,geometry,source,privacy,coverage,tier,style,
  license}`. `source.kind` ∈ `osm_pbf` | `domain` | `http` | `calc`.
- **Only downloadable data.** A layer is in the catalog *only* if a real, national/
  county-resolution, openly-licensed source exists. No local-only/proprietary/real-time/
  suppressed layers — those are documented as excluded, never faked.
- **Privacy is enforced.** `public` renders as-is; `aggregate` must be an area rate, never
  points; `generalized` snaps to grid/service-area; `suppressed` is omitted. The renderer
  must never plot an `aggregate` layer as points.
- **Fan-out mirrors `osm.planet`.** `BuildAtlasFanout` = `ListCounties(...) andThen
  foreach cty in $.counties { BuildCountyAtlas(county_key = $.cty, tier = $$.tier) }`.
  One county per task, distributed; wall-clock ≈ slowest county. Same idiom as
  `osm.planet.BuildAdminFanout` — keep it consistent.
- **Renderer output is self-contained** (inline SVG/CSS/JS, system fonts, zero external
  requests) so it renders in the dashboard, an artifact, or a static host. Production can
  later swap to MapLibre + PMTiles for heavy vector layers (see the framework design doc).
- **Consumes, never produces, county PBFs.** The `osm.planet` pipeline builds
  `north-america/us/<state>/<county>-latest.osm.pbf`; this domain reads them. If a county
  atlas is empty, check that its extract exists first.

## Tiers

`tier` gates build cost: **1** = OSM-derived (free — the PBF exists); **2** = census
choropleths (WIRED — see below) + health/EPA; **3** = expensive calc (isochrones) /
raster. `BuildCountyAtlas(tier=N)` materializes layers with `tier <= N`. The offline CLI
renders tier-1; the handler renders tier-1 + tier-2 census.

## Tier-2 joins (`census.py`, `health.py`, `epa.py`)

Tier-2 layers carry a small field naming their source; the matching module builds them:

- **`census.py`** — a `metric` field (→ a `census_us.tools._lib.metrics` key). **Reuses
  that registry + `compute_metric`** (do not reimplement the ACS math): keyless TIGER
  tract geometry + ACS values (needs `CENSUS_API_KEY`), joined by GEOID, quantile-
  classified into 5 breaks → tract choropleth. `census.resolve()` + `census.fetch_tracts()`
  are the shared FIPS + tract-geometry primitives.
- **`health.py`** — a `places` field (a CDC PLACES `measureid`, e.g. `OBESITY`). Reuses
  `census.fetch_tracts()` geometry + the same choropleth shape; data from the keyless CDC
  Socrata PLACES tract dataset by county FIPS.
- **`epa.py`** — an `epa_source` field (`tri` | `superfund` | `brownfields`). **TRI** is a
  per-county Envirofacts query (`STATE_ABBR` + `COUNTY_NAME`, coords in
  `pref_latitude/longitude`). **Superfund/Brownfields** use the **shared national cache**:
  the EMEF ArcGIS MapServer only serves the national set (`where=1=1`, paginated by
  `resultOffset`), so it's fetched once → cached at `county-atlas/_shared/epa_<ds>.geojson`
  → bbox-clipped per county (EMEF props are lowercase — `primary_name`, `county_name`,
  `fips_code`). All three → point FeatureCollections injected into `materialized`.
- **`usgs.py`** — a `usgs_source` field (`earthquakes` | `aquifers`). USGS FDSN event API
  (bbox query → points) + the Principal-Aquifers ArcGIS FeatureServer (bbox envelope →
  polygons). Both bbox-scoped from the OSM county boundary → `materialized`.
- **`fema.py`** — a `fema_source` field (`nri`). FEMA National Risk Index **Census-Tracts**
  ArcGIS FeatureServer, queried by `STCOFIPS`; returns per-tract `RISK_SCORE` **with tract
  geometry**, so the choropleth is built from NRI's own geometry (no census join).
- **`noaa.py`** — a `noaa_source` field (`stations`). GHCN-Daily station inventory
  (~10 MB) fetched ONCE and cached in the object store at `county-atlas/_shared/`, then
  bbox-filtered per county → station points. Parses via `noaa_weather`'s `parse_stations`
  when installed, else a built-in fixed-width parser. **This is the shared-national-cache
  pattern** EPA Superfund/Brownfields still need.

Shared rules: choropleths render UNDER the OSM overlays; the UI makes them **mutually
exclusive** (one thematic layer at a time) with a live legend. `aggregate` privacy →
tract areas, never points. Any failure (no key/data/network) returns `{}` and the layer
stays "not available" — **never faked**. Adding a new tier-2 source = a `<source>.py`
fetcher + a field on its catalog layers, following this pattern.

**EPA Superfund / Brownfields are NOT wired:** their EMEF ArcGIS MapServer only serves
the national set (`where=1=1`); county/bbox filters 400. Wiring them needs a national
fetch cached once + a client-side bbox filter (a shared `_shared/` cache), not a national
download per county — left as a TODO; the catalog keeps them "not available".

## Tests

`tests/test_county_atlas.py` is offline (no osmium / no S3): catalog contract, the pure
renderer, handler dispatch (3 facets), storage key derivation. osmium + object-store I/O
are covered by an integration run / the CLI. If you add a facet, update the dispatch
assertion in the same change.

## Not yet done

- Tier-3 calculated indicators (isochrone coverage via `osm.Network`, per-capita).
- Master-index state/US pages beyond the flat grouped list.
- Fleet registration (`domains.json` + image bake) — this is a local domain today.
- Perf: NOAA parses ~125k stations per county build (~1–2 s). If it bites at fan-out
  scale, pre-parse the inventory into a compact cached JSON once.
