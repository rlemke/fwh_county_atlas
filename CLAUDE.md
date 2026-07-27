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

`tier` gates build cost: **1** = OSM-derived (free — the PBF exists); **2** = census/
health/EPA choropleths (reuse existing domains); **3** = expensive calc (isochrones) /
raster. `BuildCountyAtlas(tier=N)` materializes layers with `tier <= N`. The offline CLI
and current handler render tier-1 live; tiers 2–3 are catalogued and light up when their
source join is wired.

## Tests

`tests/test_county_atlas.py` is offline (no osmium / no S3): catalog contract, the pure
renderer, handler dispatch (3 facets), storage key derivation. osmium + object-store I/O
are covered by an integration run / the CLI. If you add a facet, update the dispatch
assertion in the same change.

## Not yet done

- Tier-2 census/health join (the choropleth + per-capita indicators light up).
- Master-index state/US pages beyond the flat grouped list.
- Fleet registration (`domains.json` + image bake) — this is a local domain today.
