"""County Atlas orchestration: list counties, build one atlas, build the master index.

Consumes the per-county OSM extracts produced by ``osm.planet`` and writes a
self-contained HTML atlas + manifest per county back to the object store, then a
US -> state -> county master index over the built atlases.
"""
from __future__ import annotations

import json
import os
import tempfile

from . import catalog, materialize, render, storage

HEAVY_SKIP = {"osm.landuse"}  # catch-all landuse: redundant + heavy, kept in catalog only


def list_counties(prefix: str = "north-america/us", bucket: str = storage.BUCKET) -> list[str]:
    """Every county-extract leaf under ``prefix`` -> ``north-america/us/<state>/<county>``."""
    s3 = storage.s3_client()
    keys: set[str] = set()
    for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=bucket, Prefix=prefix.strip("/") + "/"):
        for o in page.get("Contents", []):
            k = o["Key"]
            if k.endswith("-latest.osm.pbf") and k.count("/") == 3:  # state/county depth
                keys.add(k[: -len("-latest.osm.pbf")])
    return sorted(keys)


def build_county_atlas(county_key: str, tier: int = 1, bucket: str = storage.BUCKET,
                       on_log=None) -> dict:
    """Build one county's atlas: extract -> materialize -> render -> store."""
    log = on_log or (lambda *_a, **_k: None)
    parts = county_key.strip("/").split("/")
    state, county = parts[-2], parts[-1]
    cat = catalog.load_catalog()
    s3 = storage.s3_client()

    fd, pbf = tempfile.mkstemp(suffix=".osm.pbf")
    os.close(fd)
    try:
        storage.download_pbf(s3, county_key, pbf, bucket)
        log(f"BuildCountyAtlas: {state}/{county} extract downloaded")
        layers = catalog.osm_layers(tier_max=tier)
        materialized, counts = materialize.materialize_osm(pbf, layers, skip=HEAVY_SKIP)

        # tier-2: census + CDC-PLACES tract choropleths + EPA facility points.
        # Resolve FIPS + tract geometry ONCE and share across census/health.
        choropleths: dict = {}
        if tier >= 2:
            from . import census, epa, health
            cat_layers = cat["layers"]
            sf = cf = cname = tracts = None
            try:
                sf, cf, cname = census.resolve(state, county)
                tracts = census.fetch_tracts(sf, cf)
            except Exception as exc:
                log(f"tier-2 geo resolve skipped: {exc}")
            if tracts is not None:
                choropleths.update(census.build_census_choropleths(
                    state, county, [l for l in cat_layers if l.get("metric")],
                    on_log=log, tracts=tracts, sf=sf, cf=cf))
                choropleths.update(health.build_places_choropleths(
                    state, county, [l for l in cat_layers if l.get("places")],
                    on_log=log, tracts=tracts, sf=sf, cf=cf))
            ab = epa.STATE_ABBR.get(state)
            if cname and ab:
                for lid, fc in epa.build_epa_points(
                        state, county, [l for l in cat_layers if l.get("epa_source")],
                        ab, cname, on_log=log).items():
                    materialized[lid] = fc
                    counts[lid] = len(fc["features"])

        html = render.build_atlas_html(cat, materialized, counts, state, county,
                                       choropleths=choropleths)
        live = sum(1 for v in counts.values() if v > 0) + len(choropleths)
        manifest = {
            "county_key": county_key, "state": state, "county": county, "tier": tier,
            "live_layers": live, "feature_count": sum(counts.values()),
            "layers": {lid: n for lid, n in counts.items() if n > 0},
            "choropleths": {lid: len(ch["features"]) for lid, ch in choropleths.items()},
        }
        html_uri = storage.put_text(s3, storage.atlas_html_key(county_key), html,
                                    "text/html", bucket)
        storage.put_text(s3, storage.atlas_manifest_key(county_key),
                         json.dumps(manifest), "application/json", bucket)
        log(f"BuildCountyAtlas: {state}/{county} -> {live} live layers, "
            f"{manifest['feature_count']:,} features")
        return {"html_path": html_uri, "layer_count": live,
                "feature_count": manifest["feature_count"]}
    finally:
        if os.path.exists(pbf):
            os.unlink(pbf)


def build_master_index(prefix: str = "north-america/us", bucket: str = storage.BUCKET,
                       on_log=None) -> dict:
    """Group the built county manifests by state and render US + per-state index pages."""
    log = on_log or (lambda *_a, **_k: None)
    s3 = storage.s3_client()
    by_state: dict[str, list[dict]] = {}
    for page in s3.get_paginator("list_objects_v2").paginate(
            Bucket=bucket, Prefix=f"{storage.OUTPUT_PREFIX}/"):
        for o in page.get("Contents", []):
            if not o["Key"].endswith("/manifest.json"):
                continue
            try:
                m = json.loads(s3.get_object(Bucket=bucket, Key=o["Key"])["Body"].read())
            except Exception:
                continue
            by_state.setdefault(m["state"], []).append(m)

    total = sum(len(v) for v in by_state.values())
    rows = []
    for st in sorted(by_state):
        counties = sorted(by_state[st], key=lambda m: m["county"])
        links = "".join(
            f'<a href="{st}/{m["county"]}/index.html">{m["county"].replace("-"," ").title()}'
            f'<span>{m["live_layers"]}</span></a>' for m in counties)
        rows.append(f'<details><summary>{st.replace("-"," ").title()}'
                    f'<span class="n">{len(counties)}</span></summary>'
                    f'<div class="counties">{links}</div></details>')
    html = _INDEX_TEMPLATE.format(states=len(by_state), counties=total, rows="".join(rows))
    idx_uri = storage.put_text(s3, f"{storage.OUTPUT_PREFIX}/index.html", html,
                               "text/html", bucket)
    log(f"BuildMasterIndex: {total} counties across {len(by_state)} states")
    return {"index_path": idx_uri, "county_count": total}


_INDEX_TEMPLATE = r"""<div class="app"><style>
:root{{--ground:#eef1ec;--panel:#f7f9f6;--ink:#18211c;--ink2:#4a5a51;--line:#d3dbd4;--accent:#0f6b5c;--card:#fff;}}
@media (prefers-color-scheme:dark){{:root{{--ground:#0d1411;--panel:#131b17;--ink:#e4ebe5;--ink2:#8ea298;--line:#25322b;--accent:#3fb39c;--card:#17211c;}}}}
:root[data-theme="dark"]{{--ground:#0d1411;--panel:#131b17;--ink:#e4ebe5;--ink2:#8ea298;--line:#25322b;--accent:#3fb39c;--card:#17211c;}}
:root[data-theme="light"]{{--ground:#eef1ec;--panel:#f7f9f6;--ink:#18211c;--ink2:#4a5a51;--line:#d3dbd4;--accent:#0f6b5c;--card:#fff;}}
*{{box-sizing:border-box}}.app{{background:var(--ground);color:var(--ink);min-height:100vh;font-family:ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif;padding:0 0 40px}}
.hdr{{padding:22px 28px;border-bottom:1px solid var(--line);background:var(--panel)}}
.crumb{{font:600 11px/1 ui-monospace,monospace;letter-spacing:.14em;text-transform:uppercase;color:var(--ink2)}}
.title{{font-size:28px;font-weight:680;letter-spacing:-.02em;margin-top:6px}}.title span{{color:var(--accent)}}
.wrap{{max-width:900px;margin:0 auto;padding:22px 28px}}
details{{border-bottom:1px solid var(--line)}}summary{{cursor:pointer;list-style:none;display:flex;align-items:center;padding:12px 4px;font-weight:640;font-size:15px}}summary::-webkit-details-marker{{display:none}}
summary::before{{content:"";width:6px;height:6px;border-right:1.7px solid var(--ink2);border-bottom:1.7px solid var(--ink2);transform:rotate(-45deg);margin-right:10px;transition:transform .15s}}details[open]>summary::before{{transform:rotate(45deg)}}
summary .n{{margin-left:auto;font:500 12px/1 ui-monospace,monospace;color:var(--ink2)}}
.counties{{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:6px;padding:6px 0 16px 16px}}
.counties a{{display:flex;align-items:center;text-decoration:none;color:var(--ink);background:var(--card);border:1px solid var(--line);border-radius:6px;padding:8px 10px;font-size:13px}}
.counties a:hover{{border-color:var(--accent)}}.counties a span{{margin-left:auto;font:600 11px/1 ui-monospace,monospace;color:var(--accent)}}
</style>
<div class="hdr"><div class="crumb">County Atlas</div><div class="title">United States <span>County Atlas</span></div></div>
<div class="wrap"><p style="color:var(--ink2);font-size:13px;margin:0 0 18px">{counties} counties across {states} states. Number by each county = information layers rendered. Pick a state, then a county.</p>{rows}</div></div>"""
