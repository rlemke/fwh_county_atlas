# county-atlas

A Facetwork domain that builds **a map per US county** (~3,143) plus a **US → state →
county master index**. It is a *catalog-driven* atlas: the layer set is defined once in
[`layers.json`](src/county_atlas/layers.json), and one generic renderer draws whatever
layers a county has. It consumes the per-county OSM extracts produced by the
`osm.planet` pipeline (`north-america/us/<state>/<county>-latest.osm.pbf`) and fans out
one build per county across the fleet.

Full architecture: [`docs/architecture/county-atlas.md`](https://github.com/rlemke/facetwork/blob/main/docs/architecture/county-atlas.md) in the framework repo.

## Feature specifications

| Area | Doc |
|------|-----|
| **Layer catalog** — the 91-layer registry, source kinds, privacy tiers, coverage | [`layers.json`](src/county_atlas/layers.json) |
| **Fan-out** — `ListCounties` → `foreach` → `BuildCountyAtlas`, one county per task | [`ffl/atlas.ffl`](src/county_atlas/ffl/atlas.ffl) |
| **Renderer** — GeoJSON layers → self-contained interactive HTML (SVG map + checkbox tree) | [`render.py`](src/county_atlas/tools/_county_atlas_tools/render.py) |
| **Materializer** — osmium: county PBF → per-layer GeoJSON | [`materialize.py`](src/county_atlas/tools/_county_atlas_tools/materialize.py) |
| **Tier-2 census join** — ACS → tract geometry → per-county choropleths (reuses `census-us` metric registry) | [`census.py`](src/county_atlas/tools/_county_atlas_tools/census.py) |
| **Tier-2 health join** — CDC PLACES tract prevalence → choropleths | [`health.py`](src/county_atlas/tools/_county_atlas_tools/health.py) |
| **Tier-2 EPA join** — TRI facilities → per-county points | [`epa.py`](src/county_atlas/tools/_county_atlas_tools/epa.py) |
| **Tier-2 USGS join** — earthquakes (bbox) + aquifers (ArcGIS) | [`usgs.py`](src/county_atlas/tools/_county_atlas_tools/usgs.py) |
| **Tier-2 FEMA join** — National Risk Index tract choropleth | [`fema.py`](src/county_atlas/tools/_county_atlas_tools/fema.py) |
| **Tier-2 NOAA join** — GHCN weather stations (shared cache) | [`noaa.py`](src/county_atlas/tools/_county_atlas_tools/noaa.py) |
| **Storage** — county PBF in / atlas out, backend-aware object store | [`storage.py`](src/county_atlas/tools/_county_atlas_tools/storage.py) |

## What's in the catalog

91 layers across 14 categories — **included only when a real, downloadable,
national/county-resolution, openly-licensed source exists**:

- **46 from the county OSM PBF** (ODbL) — boundaries, roads, parks, hydrology,
  landmarks, transit, government/healthcare/education POIs, land use. These render
  **live** from the extract.
- **24 from Facetwork domains** — `census-us` (ACS + TIGER demographics/income/housing/
  tracts), `health` (CDC PLACES/NCHS), `noaa-weather` (climate).
- **17 direct national fetches** — EPA (Superfund/Brownfields/TRI/air/water), USGS
  (elevation/faults/aquifers/quakes), FEMA (NRI hazard risk/flood), HUD, USDA.
- **4 calculated** — isochrone coverage, per-capita, ratios (reuse `osm.Network`).

Layers with no uniform national open source (parcels, voting precincts, traffic volumes,
crash points, encampments, response times…) are **excluded**, not faked. Privacy is an
enforced catalog field: `aggregate` layers may only render as area rates, never points.

## Facets & workflows (`county.atlas`)

| Facet / workflow | Purpose |
|---|---|
| `ListCounties(prefix, bucket)` | Enumerate county-extract leaves — the fan-out unit |
| `BuildCountyAtlas(county_key, tier)` | Build one county: download → materialize → render → store |
| `BuildMasterIndex(prefix)` | US → state → county index over the built atlases |
| `workflows.BuildCountyAtlasMap(county_key)` | Build a single county's atlas |
| `workflows.BuildAtlasFanout(prefix, tier)` | **Fan-out** — one `BuildCountyAtlas` per county, fleet-wide |
| `workflows.BuildMasterIndexMap(prefix)` | Build the master index after a fan-out |

Output → `county-atlas/<state>/<county>/index.html` (+ `manifest.json`) in the object
store; the master index at `county-atlas/index.html`.

## Install & run

```bash
fw install domain county-atlas          # clone + pip install -e + verify
fw runner start --domain county-atlas -- --log-format text
```

Fan out every county (needs the `osm.planet` county extracts already present):

```bash
fw ffl run --workflow county.atlas.workflows.BuildAtlasFanout \
  --inputs '{"prefix":"north-america/us","tier":1}'
fw ffl run --workflow county.atlas.workflows.BuildMasterIndexMap
```

Preview a single county offline from a local PBF:

```bash
python src/county_atlas/tools/build-county-atlas.py coos.osm.pbf oregon coos out.html
```

## Requirements

- **osmium-tool** on the runner (tier-1 materialization).
- The `osm.planet` per-county extracts in the shared object store.
- `boto3` + the standard `FW_S3_*` env for object-store access.
- **Tier-2 census:** `CENSUS_API_KEY` (present in the fleet runner env) + `pyshp` +
  the `census-us` domain installed (its ACS metric registry is reused). Run
  `BuildCountyAtlas(tier=2)` to light up the Census ACS tract choropleths.

## License

Apache 2.0. Layer data under each source's license (OSM ODbL; Census/EPA/USGS/FEMA/CDC/
NOAA public domain).
