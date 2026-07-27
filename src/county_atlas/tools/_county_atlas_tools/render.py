"""Render a self-contained interactive county atlas (SVG map + catalog checkbox tree).

Pure function of (catalog, materialized OSM layers, counts): no I/O. The output is a
single self-contained HTML fragment — inline SVG/CSS/JS, system fonts, no external
requests — so it renders anywhere (dashboard, artifact, static host). Layers whose
source is not the county PBF appear in the tree disabled, with their real source
named (honest coverage).
"""
from __future__ import annotations

import math

KM2_ACRE = 247.105
DEFAULT_ON = {"osm.county_boundary", "osm.water", "osm.rivers", "osm.roads_highway",
              "osm.roads_interstate", "osm.parks", "osm.places", "osm.coastline"}


def iter_geoms(geom):
    t = geom.get("type"); c = geom.get("coordinates")
    if t == "Point":
        yield "pt", c
    elif t == "MultiPoint":
        yield from (("pt", p) for p in c)
    elif t == "LineString":
        yield "ln", c
    elif t == "MultiLineString":
        yield from (("ln", l) for l in c)
    elif t == "Polygon":
        yield "pg", c
    elif t == "MultiPolygon":
        yield from (("pg", pg) for pg in c)


def _haversine_km(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    d = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(d))


def _line_km(coords):
    return sum(_haversine_km(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def _ring_km2(ring, lat0):
    k = 111.320
    xs = [p[0] * k * math.cos(math.radians(lat0)) for p in ring]
    ys = [p[1] * k for p in ring]
    s = sum(xs[i] * ys[i + 1] - xs[i + 1] * ys[i] for i in range(-1, len(xs) - 1))
    return abs(s) / 2.0


def _bbox(feats):
    xs, ys = [], []
    for f in feats:
        for _k, coords in iter_geoms(f.get("geometry") or {}):
            stack = [coords]
            while stack:
                v = stack.pop()
                if v and isinstance(v[0], (int, float)):
                    xs.append(v[0]); ys.append(v[1])
                elif v:
                    stack.extend(v)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def build_atlas_html(catalog: dict, materialized: dict, counts: dict,
                     state: str, county: str, choropleths: dict | None = None,
                     extra_indicators: list | None = None) -> str:
    layers = catalog["layers"]
    categories = catalog["categories"]
    choropleths = choropleths or {}

    allfeats = [f for gj in materialized.values() for f in gj.get("features", [])]
    bnd = materialized.get("osm.county_boundary", {}).get("features", [])
    bbox = _bbox(bnd) or _bbox(allfeats) or (-1, -1, 1, 1)
    minlon, minlat, maxlon, maxlat = bbox
    lat0 = (minlat + maxlat) / 2
    cos = math.cos(math.radians(lat0)) or 1e-6
    W, pad = 1000.0, 12
    wu = (maxlon - minlon) * cos or 1e-6
    hu = (maxlat - minlat) or 1e-6
    scale = W / wu
    H = hu * scale

    def px(lon): return int((lon - minlon) * cos * scale + pad + 0.5)
    def py(lat): return int((maxlat - lat) * scale + pad + 0.5)

    def snap(coords):
        out = []
        for p in coords:
            xy = (px(p[0]), py(p[1]))
            if not out or out[-1] != xy:
                out.append(xy)
        return out

    def ring_path(ring):
        pts = snap(ring)
        if len(pts) < 3:
            return ""
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        if (max(xs) - min(xs)) < 2 and (max(ys) - min(ys)) < 2:
            return ""
        return "M" + "L".join(f"{x},{y}" for x, y in pts) + "Z"

    def line_path(ln):
        pts = snap(ln)
        return "M" + "L".join(f"{x},{y}" for x, y in pts) if len(pts) >= 2 else ""

    order = {"pg": 0, "ln": 1, "pt": 2}
    groups = []
    for gc in ("pg", "ln", "pt"):
        for lyr in layers:
            lid = lyr["id"]
            if lid not in materialized:
                continue
            st = lyr.get("style", {})
            parts = []
            for f in materialized[lid]["features"]:
                for gk, coords in iter_geoms(f.get("geometry") or {}):
                    if gk != gc:
                        continue
                    if gk == "pg":
                        d = "".join(ring_path(r) for r in coords if len(r) > 2)
                        if d:
                            parts.append(f'<path d="{d}" fill="{st.get("fill","none")}" '
                                         f'fill-opacity="{st.get("opacity",0.5)}" '
                                         f'stroke="{st.get("line","#5b6b62")}" stroke-width="0.6"/>')
                    elif gk == "ln":
                        d = line_path(coords)
                        if d:
                            dash = ' stroke-dasharray="3 2"' if st.get("dash") else ""
                            parts.append(f'<path d="{d}" fill="none" stroke="{st.get("line","#6b7d72")}" '
                                         f'stroke-width="{st.get("width",0.8)}"{dash}/>')
                    else:
                        parts.append(f'<circle cx="{px(coords[0])}" cy="{py(coords[1])}" r="2.4" '
                                     f'fill="{st.get("color","#c0392b")}" stroke="#fff" stroke-width="0.5"/>')
            if parts:
                vis = "" if lid in DEFAULT_ON else ' style="display:none"'
                groups.append((order[gc], f'<g data-layer="{lid}"{vis}>{"".join(parts)}</g>'))
    svg_osm = "".join(g for _o, g in sorted(groups, key=lambda x: x[0]))

    # tier-2 choropleth groups (drawn UNDER the OSM overlays) + legend metadata
    import json as _json
    choro_svg, choro_legend = [], {}
    for lid, ch in choropleths.items():
        parts = []
        for f in ch["features"]:
            for gk, coords in iter_geoms(f.get("geometry") or {}):
                if gk != "pg":
                    continue
                d = "".join(ring_path(r) for r in coords if len(r) > 2)
                if not d:
                    continue
                cls = f.get("cls", -1)
                fill = ch["legend"]["nodata"] if cls < 0 else ch["legend"]["colors"][cls]
                parts.append(f'<path d="{d}" fill="{fill}" fill-opacity="0.85" '
                             f'stroke="#fff" stroke-width="0.3"/>')
        if parts:
            choro_svg.append(f'<g data-layer="{lid}" data-choro="1" style="display:none">'
                             f'{"".join(parts)}</g>')
            choro_legend[lid] = ch["legend"]
    svg_body = "".join(choro_svg) + svg_osm
    choro_js = _json.dumps(choro_legend)

    def line_total(*ids):
        return sum(_line_km(c) for i in ids for f in materialized.get(i, {}).get("features", [])
                   for gk, c in iter_geoms(f.get("geometry") or {}) if gk == "ln")

    def poly_total(i):
        return sum(_ring_km2(c[0], lat0) for f in materialized.get(i, {}).get("features", [])
                   for gk, c in iter_geoms(f.get("geometry") or {}) if gk == "pg" and c)

    coast_km = line_total("osm.coastline")
    indicators = [
        ("County area", f"{poly_total('osm.county_boundary'):,.0f} km²" or "—", "OSM boundary"),
        ("Road network", f"{line_total('osm.roads_interstate','osm.roads_highway','osm.roads_local'):,.0f} km", "OSM highway=*"),
        ("Rivers & streams", f"{line_total('osm.rivers'):,.0f} km", "OSM waterway"),
        ("Coastline", f"{coast_km:,.0f} km" if coast_km else "—", "OSM natural=coastline"),
        ("Park & open space", f"{poly_total('osm.parks')*KM2_ACRE:,.0f} ac", "OSM leisure/protected"),
        ("Surface water", f"{poly_total('osm.water'):,.0f} km²", "OSM natural=water"),
        ("Populated places", f"{counts.get('osm.places',0):,}", "OSM place=*"),
        ("Schools", f"{counts.get('osm.schools',0):,}", "OSM amenity=school"),
        ("Hospitals", f"{counts.get('osm.hospitals',0):,}", "OSM amenity=hospital"),
        ("Fire stations", f"{counts.get('osm.fire',0):,}", "OSM amenity=fire_station"),
        ("Campgrounds", f"{counts.get('osm.campgrounds',0):,}", "OSM tourism=camp_site"),
        ("Trail network", f"{line_total('osm.trails'):,.0f} km", "OSM highway=path"),
    ]
    if extra_indicators:  # tier-3 per-capita panel stats
        indicators += list(extra_indicators)

    priv_badge = {"aggregate": "AGG", "generalized": "GEN", "suppressed": "SUP"}
    tree = []
    for cat in categories:
        rows, n_live = [], 0
        for lyr in layers:
            if lyr["category"] != cat:
                continue
            lid = lyr["id"]
            is_choro = lid in choropleths
            live = is_choro or (lid in materialized and counts.get(lid, 0) > 0)
            n_live += 1 if live else 0
            st = lyr.get("style", {})
            sw = st.get("fill") or st.get("line") or st.get("color") or "#8a978f"
            if sw == "none":
                sw = st.get("line", "#8a978f")
            badges = []
            pb = priv_badge.get(lyr["privacy"])
            if pb:
                badges.append(f'<span class="b b-priv">{pb}</span>')
            if lyr["coverage"] == "sparse":
                badges.append('<span class="b b-cov">sparse</span>')
            if is_choro:
                ramp = "".join(f'<i style="background:{c}"></i>'
                               for c in choropleths[lid]["legend"]["colors"])
                rows.append(f'<label class="row live choro"><input type="checkbox" data-choro="{lid}">'
                            f'<span class="ramp">{ramp}</span>'
                            f'<span class="lbl">{lyr["label"]}</span>'
                            f'<span class="cnt">{len(choropleths[lid]["features"])}</span>'
                            f'{"".join(badges)}</label>')
            elif live:
                rows.append(f'<label class="row live"><input type="checkbox" data-layer="{lid}"'
                            f'{" checked" if lid in DEFAULT_ON else ""}>'
                            f'<span class="sw" style="background:{sw}"></span>'
                            f'<span class="lbl">{lyr["label"]}</span>'
                            f'<span class="cnt">{counts.get(lid,0):,}</span>{"".join(badges)}</label>')
            else:
                src = lyr["source"]
                where = (src.get("domain") or src.get("url", "").split("//")[-1].split("/")[0]
                         or src["kind"])
                rows.append(f'<label class="row off" title="needs data source: {where}">'
                            f'<input type="checkbox" disabled>'
                            f'<span class="sw" style="background:{sw};opacity:.35"></span>'
                            f'<span class="lbl">{lyr["label"]}</span>'
                            f'<span class="src">{where}</span>{"".join(badges)}</label>')
        tree.append(f'<details{" open" if n_live else ""}><summary>{cat}'
                    f'<span class="csum">{n_live} live</span></summary>{"".join(rows)}</details>')

    n_live = sum(1 for l in layers
                 if (l["id"] in materialized and counts.get(l["id"], 0) > 0)
                 or l["id"] in choropleths)
    span_km = wu / cos * 111.320
    nice = [1, 2, 5, 10, 20, 25, 50, 100]
    bar_km = min(nice, key=lambda v: abs(v - span_km / 5))
    bar_px = round(bar_km / 111.320 * cos * scale, 1)

    return _TEMPLATE.format(
        county=county.replace("-", " ").title(), state=state.replace("-", " ").title(),
        svgw=round(W + pad * 2, 1), svgh=round(H + pad * 2, 1), svg_body=svg_body,
        tree="".join(tree),
        indicators="".join(f'<div class="stat"><div class="sv">{v}</div>'
                           f'<div class="sk">{k}</div><div class="ss">{s}</div></div>'
                           for k, v, s in indicators),
        n_live=n_live, n_total=len(layers), bar_px=bar_px, bar_km=bar_km,
        plate_x=pad, plate_y=pad, plate_w=round(W, 1), plate_h=round(H, 1),
        choro_js=choro_js, n_choro=len(choropleths))


_TEMPLATE = r"""<div class="app"><style>
:root{{--ground:#eef1ec;--panel:#f7f9f6;--card:#fff;--ink:#18211c;--ink2:#4a5a51;--line:#d3dbd4;--accent:#0f6b5c;--accent2:#0b4f44;--plate:#e7ece6;--neat:#3a4842;--grat:#c9d2cb;--badge:#e2e8e2;--shadow:0 1px 2px rgba(20,40,30,.06),0 8px 24px rgba(20,40,30,.05);}}
@media (prefers-color-scheme:dark){{:root{{--ground:#0d1411;--panel:#131b17;--card:#17211c;--ink:#e4ebe5;--ink2:#8ea298;--line:#25322b;--accent:#3fb39c;--accent2:#66c8b3;--plate:#101815;--neat:#5a6f66;--grat:#1c2620;--badge:#1e2a24;--shadow:0 1px 2px rgba(0,0,0,.3),0 10px 30px rgba(0,0,0,.35);}}}}
:root[data-theme="light"]{{--ground:#eef1ec;--panel:#f7f9f6;--card:#fff;--ink:#18211c;--ink2:#4a5a51;--line:#d3dbd4;--accent:#0f6b5c;--accent2:#0b4f44;--plate:#e7ece6;--neat:#3a4842;--grat:#c9d2cb;--badge:#e2e8e2;}}
:root[data-theme="dark"]{{--ground:#0d1411;--panel:#131b17;--card:#17211c;--ink:#e4ebe5;--ink2:#8ea298;--line:#25322b;--accent:#3fb39c;--accent2:#66c8b3;--plate:#101815;--neat:#5a6f66;--grat:#1c2620;--badge:#1e2a24;}}
*{{box-sizing:border-box}}.app{{background:var(--ground);color:var(--ink);min-height:100vh;font-family:ui-sans-serif,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:14px;line-height:1.45;-webkit-font-smoothing:antialiased}}
.hdr{{display:flex;align-items:baseline;gap:16px;flex-wrap:wrap;padding:18px 24px 14px;border-bottom:1px solid var(--line);background:var(--panel)}}
.crumb{{font:600 11px/1 ui-monospace,monospace;letter-spacing:.12em;text-transform:uppercase;color:var(--ink2)}}.crumb b{{color:var(--accent)}}
.title{{font-size:26px;font-weight:680;letter-spacing:-.02em}}.title span{{color:var(--ink2);font-weight:500}}
.cov{{margin-left:auto;font:500 12px/1.3 ui-monospace,monospace;color:var(--ink2);text-align:right}}.cov b{{color:var(--ink);font-size:15px}}
.grid{{display:grid;grid-template-columns:300px minmax(0,1fr) 250px}}@media(max-width:1080px){{.grid{{grid-template-columns:1fr}}}}
.pane{{padding:16px 18px;overflow-y:auto;max-height:calc(100vh - 66px)}}.pane.left{{border-right:1px solid var(--line)}}.pane.right{{border-left:1px solid var(--line);background:var(--panel)}}
.ptitle{{font:600 10px/1 ui-monospace,monospace;letter-spacing:.16em;text-transform:uppercase;color:var(--ink2);margin:2px 0 12px}}
details{{border-top:1px solid var(--line);padding:2px 0}}details:first-of-type{{border-top:none}}
summary{{cursor:pointer;list-style:none;display:flex;align-items:center;gap:8px;padding:8px 2px;font-weight:600;font-size:12.5px;user-select:none}}summary::-webkit-details-marker{{display:none}}
summary::before{{content:"";width:6px;height:6px;border-right:1.6px solid var(--ink2);border-bottom:1.6px solid var(--ink2);transform:rotate(-45deg);transition:transform .15s;flex:0 0 auto;margin-right:2px}}details[open]>summary::before{{transform:rotate(45deg)}}
.csum{{margin-left:auto;font:500 10px/1 ui-monospace,monospace;color:var(--accent)}}
.row{{display:flex;align-items:center;gap:8px;padding:4px 2px 4px 16px;font-size:13px;border-radius:6px}}.row.live{{cursor:pointer}}.row.live:hover{{background:var(--card)}}
.row input{{accent-color:var(--accent);margin:0;flex:0 0 auto}}.row.off{{color:var(--ink2)}}
.sw{{width:11px;height:11px;border-radius:3px;flex:0 0 auto;border:1px solid rgba(0,0,0,.12)}}
.lbl{{flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.cnt{{font:500 11px/1 ui-monospace,monospace;color:var(--ink2);font-variant-numeric:tabular-nums}}
.src{{font:500 10px/1 ui-monospace,monospace;color:var(--ink2);opacity:.8;max-width:96px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.b{{font:600 9px/1.5 ui-monospace,monospace;letter-spacing:.05em;padding:1px 4px;border-radius:4px;background:var(--badge);color:var(--ink2)}}.b-priv{{color:var(--accent2)}}
.mapwrap{{padding:16px}}.plate{{background:var(--plate);border:1px solid var(--line);border-radius:4px;box-shadow:var(--shadow);position:relative;overflow:hidden}}
svg.map{{display:block;width:100%;height:auto}}.neat{{fill:none;stroke:var(--neat);stroke-width:1.2}}
.scalebar{{position:absolute;left:16px;bottom:14px;font:600 10px/1.4 ui-monospace,monospace;color:var(--ink)}}.scalebar .bar{{height:5px;border:1.4px solid var(--ink);border-top:none;margin-top:2px}}
.narrow{{position:absolute;right:16px;top:12px;font:700 13px/1 ui-monospace,monospace;color:var(--ink2);text-align:center}}.narrow::before{{content:"\25B2";display:block;font-size:11px;color:var(--accent)}}
.stat{{padding:10px 0;border-top:1px solid var(--line)}}.stat:first-child{{border-top:none}}
.sv{{font:640 20px/1 ui-monospace,monospace;font-variant-numeric:tabular-nums}}.sk{{font-size:12px;font-weight:600;margin-top:3px}}.ss{{font:500 10px/1.3 ui-monospace,monospace;color:var(--ink2);margin-top:1px}}
.note{{font-size:11px;color:var(--ink2);line-height:1.5;margin-top:14px;padding-top:12px;border-top:1px solid var(--line)}}
.foot{{padding:12px 24px;border-top:1px solid var(--line);font:500 11px/1.5 ui-monospace,monospace;color:var(--ink2);background:var(--panel)}}.foot b{{color:var(--ink)}}
.ramp{{display:flex;width:22px;height:11px;border-radius:3px;overflow:hidden;flex:0 0 auto;border:1px solid rgba(0,0,0,.12)}}.ramp i{{flex:1}}
.legendbox{{position:absolute;left:16px;top:14px;background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:9px 11px;font:500 11px/1.55 ui-monospace,monospace;box-shadow:var(--shadow);max-width:200px}}
.legendbox .lt{{font-weight:700;color:var(--ink);margin-bottom:5px;font-size:11px;white-space:normal}}
.legendbox>div{{display:flex;align-items:center;gap:6px;color:var(--ink2);font-variant-numeric:tabular-nums}}.legendbox i{{width:12px;height:12px;border-radius:2px;flex:0 0 auto}}
</style>
<div class="hdr"><div><div class="crumb">United States <b>&rsaquo;</b> {state} <b>&rsaquo;</b> {county} County</div>
<div class="title">{county} County<span>, {state}</span></div></div>
<div class="cov"><b>{n_live}</b> layers rendered live<br>of {n_total} in catalog</div></div>
<div class="grid">
<aside class="pane left"><div class="ptitle">Information layers</div>{tree}
<div class="note">Checked layers draw on the map. Muted rows need a tier-2/3 source
(Census, EPA, USGS, CDC…) named at right — catalogued, not fetched here. <b>AGG</b> = shown
only as an area rate, never points.</div></aside>
<main class="mapwrap"><div class="plate"><svg class="map" viewBox="0 0 {svgw} {svgh}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="{county} County map">
<rect x="{plate_x}" y="{plate_y}" width="{plate_w}" height="{plate_h}" class="neat"/><g id="layers">{svg_body}</g></svg>
<div class="legendbox" id="lgd" style="display:none"></div>
<div class="narrow">N</div><div class="scalebar">{bar_km} km<div class="bar" style="width:{bar_px}px"></div></div></div></main>
<aside class="pane right"><div class="ptitle">County indicators</div>{indicators}
<div class="note">OSM-derived measures from this county's extract. Per-capita and access
indicators need the Census population denominator — a tier-2 join, catalogued but not run here.</div></aside>
</div>
<div class="foot">Source: <b>OpenStreetMap</b> (ODbL) via the self-hosted county extract &nbsp;·&nbsp;
catalog: <b>{n_total}</b> layers across 14 categories &nbsp;·&nbsp; non-OSM layers from
Census / CDC / EPA / USGS / FEMA / NOAA (public domain).</div>
<script>
document.querySelectorAll('input[data-layer]').forEach(function(cb){{cb.addEventListener('change',function(){{var g=document.querySelector('g[data-layer="'+cb.dataset.layer+'"]');if(g)g.style.display=cb.checked?'':'none';}});}});
var CHORO={choro_js};
function afmt(v,f){{if(v==null)return '—';if(f=='pct')return Math.round(v)+'%';if(f=='dollar')return v>=1000?'$'+Math.round(v/1000)+'k':'$'+Math.round(v);if(f=='count'||f=='density')return Math.round(v).toLocaleString();if(f=='km')return v.toFixed(1)+' km';if(f=='years')return v.toFixed(1);if(f=='index')return v.toFixed(2);return ''+Math.round(v);}}
function showLegend(lid){{var c=CHORO[lid],lg=document.getElementById('lgd');if(!c){{lg.style.display='none';return;}}var b=c.breaks,cols=c.colors,h='<div class="lt">'+c.label+'</div>';for(var i=0;i<cols.length;i++){{var lo=i==0?null:b[i-1],hi=i<b.length?b[i]:null;var lab=lo==null?'< '+afmt(hi,c.fmt):hi==null?'≥ '+afmt(lo,c.fmt):afmt(lo,c.fmt)+'–'+afmt(hi,c.fmt);h+='<div><i style="background:'+cols[i]+'"></i>'+lab+'</div>';}}h+='<div><i style="background:'+c.nodata+'"></i>no data</div>';lg.innerHTML=h;lg.style.display='';}}
document.querySelectorAll('input[data-choro]').forEach(function(cb){{cb.addEventListener('change',function(){{var lid=cb.dataset.choro;if(cb.checked){{document.querySelectorAll('input[data-choro]').forEach(function(o){{if(o!==cb){{o.checked=false;var g2=document.querySelector('g[data-layer="'+o.dataset.choro+'"]');if(g2)g2.style.display='none';}}}});var g=document.querySelector('g[data-layer="'+lid+'"]');if(g)g.style.display='';showLegend(lid);}}else{{var g=document.querySelector('g[data-layer="'+lid+'"]');if(g)g.style.display='none';document.getElementById('lgd').style.display='none';}}}});}});
</script>
</div>"""
