# FFL Examples — `county-atlas`

Every numbered scenario is a **complete, compilable FFL file**. Copy one into
`my.ffl` and run it:

```bash
fw ffl run --primary my.ffl \
  --library ~/fw_handlers/fwh_county_atlas/src/county_atlas/ffl/atlas.ffl \
  --workflow my.atlas.<WorkflowName>
```

A runner serving the `county` namespace must be up
(`fw runner start --domain county-atlas`). Every block below is compile-checked
against `src/county_atlas/ffl/atlas.ffl`.

New to the language? Start with the
[FFL grammar](https://github.com/rlemke/facetwork/blob/main/docs/reference/language/grammar.md)
and the [canonical examples](https://github.com/rlemke/facetwork/tree/main/examples/canonical).

---

## The facets at a glance

This domain is the fleet's biggest fan-out — one atlas per US county, ~3,143 of
them from a single `foreach`. The FFL below is where that parallelism is expressed.

| Declaration | Signature | Does |
|---|---|---|
| `county.atlas.ListCounties` | `(prefix = "north-america/us", bucket = "osm-extracts") => (counties: [String], count: Long)` | List the county-extract leaves — the fan-out unit |
| `county.atlas.BuildCountyAtlas` | `(county_key: String, tier: Long = 1, bucket = "osm-extracts") => (html_path, layer_count, feature_count)` | Build ONE county's atlas from its OSM extract |
| `county.atlas.BuildMasterIndex` | `(prefix, bucket) => (index_path, county_count)` | US → state → county index over every atlas written so far |
| `county.atlas.workflows.BuildCountyAtlasMap` | `(county_key: String, tier: Long = 1)` | One county, end to end |
| `county.atlas.workflows.BuildAtlasFanout` | `(prefix, tier, bucket) => (built: [String])` | The national fan-out |
| `county.atlas.workflows.BuildMasterIndexMap` | `(prefix)` | The index, after a fan-out |

---

## 1. Run what ships — no FFL to write

```bash
fw ffl seed --include county-atlas

# one county
fw ffl run --primary ~/fw_handlers/fwh_county_atlas/src/county_atlas/ffl/atlas.ffl \
  --workflow county.atlas.workflows.BuildCountyAtlasMap \
  --inputs '{"county_key": "north-america/us/oregon/coos", "tier": 1}'

# the national fan-out, then the index
fw ffl run --primary …/atlas.ffl --workflow county.atlas.workflows.BuildAtlasFanout
fw ffl run --primary …/atlas.ffl --workflow county.atlas.workflows.BuildMasterIndexMap
```

Write FFL when you want a different *shape* of run — a subset of counties, a
different tier, your own error handling, or an index step chained onto the fan-out.

## 2. The smallest workflow you can write

Every FFL workflow needs a `namespace`, a `use` per namespace it calls into, and a
`yield` back to itself.

```ffl
namespace my.atlas {

    use county.atlas

    /** Build one county's atlas. */
    workflow OneCounty(county_key: String = "north-america/us/oregon/coos") => (html_path: String, layers: Long) andThen {

        atlas = county.atlas.BuildCountyAtlas(county_key = $.county_key, tier = 1)

        yield OneCounty(html_path = atlas.html_path, layers = atlas.layer_count)
    }
}
```

Rules visible above: `=>` sits on the **same line** as the closing `)`; references
are always `step.field`; `$.county_key` reads the workflow's own parameter.

## 3. Fan out over every county — `foreach` on a step

`andThen foreach v in <list>` turns one step into N runtime steps that runners
claim in parallel. Because the `foreach` hangs off the **`counties` step**, inside
the body `$` is that step (so `$.c` is the loop variable) and `$$` reaches the
workflow's parameters.

```ffl
namespace my.atlas {

    use county.atlas

    /** One atlas task per county leaf — the national fan-out. */
    workflow AtlasEverywhere(prefix: String = "north-america/us", tier: Long = 1) => (built: [String]) andThen {

        counties = county.atlas.ListCounties(prefix = $.prefix) andThen foreach c in $.counties {

            atlas = county.atlas.BuildCountyAtlas(county_key = $.c, tier = $$.tier)

            yield AtlasEverywhere(built = [atlas.html_path])
        }
    }
}
```

Wall clock is the slowest county, not the sum of 3,143 — add runners to go faster.
The dashboard's execution graph shows the fan-out live.

## 4. Fan out over *your own* list

The list doesn't have to come from a facet. Take it as a `Json` parameter and the
county set becomes a CLI argument — the fastest way to rebuild a handful of
counties after a renderer change. Here the `foreach` hangs off the **workflow**, so
the loop variable and the workflow's parameters share one `$`.

```ffl
namespace my.atlas {

    use county.atlas

    /** Rebuild exactly the counties you name. */
    workflow AtlasFor(counties: Json, tier: Long = 2) => (built: [String]) andThen foreach c in $.counties {

        atlas = county.atlas.BuildCountyAtlas(county_key = $.c, tier = $.tier)

        yield AtlasFor(built = [atlas.html_path])
    }
}
```

```bash
fw ffl run --primary my.ffl --library …/atlas.ffl --workflow my.atlas.AtlasFor \
  --inputs '{"counties": ["north-america/us/oregon/coos", "north-america/us/oregon/lane"], "tier": 2}'
```

## 5. Fan-in — index after the atlases exist

`BuildMasterIndex` reads whatever atlases are already in the object store, so it
needs no value from the fan-out. Independent steps may run in any order, so create
the ordering by referencing an upstream field.

```ffl
namespace my.atlas {

    use county.atlas

    /** One county, then the master index. NOTE: these two steps are independent. */
    workflow AtlasThenIndex(county_key: String = "north-america/us/oregon/coos") => (index_path: String) andThen {

        atlas = county.atlas.BuildCountyAtlas(county_key = $.county_key)

        idx = county.atlas.BuildMasterIndex(prefix = "north-america/us", bucket = "osm-extracts")

        yield AtlasThenIndex(index_path = idx.index_path)
    }
}
```

> ⚠️ Line order is **not** run order. Nothing in `idx` references `atlas`, so the
> two may run concurrently and the index can miss the county just built. Ordering
> in FFL comes from references: the map domains pass an upstream field into a
> `dependency_signal` parameter for exactly this reason. `BuildMasterIndex` takes
> no such parameter, so run the index as its own submission after the fan-out
> finishes — that is what the shipped `BuildMasterIndexMap` workflow is for.

## 6. Call-time mixins — timeouts and retries

`BuildCountyAtlas` ships `with Timeout(minutes = 20)`. Big counties (Los Angeles,
Cook) need more; the **call site** can override it for one use without forking the
facet.

```ffl
namespace my.atlas {

    use county.atlas

    /** Extra headroom for a large county extract, with retries. */
    workflow BigCounty(county_key: String = "north-america/us/california/los-angeles") => (html_path: String) andThen {

        atlas = county.atlas.BuildCountyAtlas(county_key = $.county_key, tier = 3) with Timeout(minutes = 90) with Retry(maxAttempts = 2, backoffSeconds = 120)

        yield BigCounty(html_path = atlas.html_path)
    }
}
```

## 7. One bad county shouldn't kill 3,000 — `catch`

`catch` fires when a step errors after its retries are exhausted. Inside a
`foreach` it is per-iteration, so the rest of the fan-out proceeds.

```ffl
namespace my.atlas {

    use county.atlas

    /** Best-effort national build: record failures, keep going. */
    workflow BestEffortAtlas(prefix: String = "north-america/us", tier: Long = 1) => (built: [String]) andThen {

        counties = county.atlas.ListCounties(prefix = $.prefix) andThen foreach c in $.counties {

            atlas = county.atlas.BuildCountyAtlas(county_key = $.c, tier = $$.tier) catch {
                yield BestEffortAtlas(built = ["failed"])
            }

            yield BestEffortAtlas(built = [atlas.html_path])
        }
    }
}
```

## 8. Branch on a result — `when`

A `when` block hangs off the step it inspects: inside a case `$` is that step and
`$$` reaches the workflow. Every `when` needs a default case, last.

```ffl
namespace my.atlas {

    use county.atlas

    /** Flag counties whose extract produced suspiciously few features. */
    workflow QualityCheckedAtlas(county_key: String = "north-america/us/oregon/coos", min_features: Long = 1000) => (status: String, html_path: String) andThen {

        atlas = county.atlas.BuildCountyAtlas(county_key = $.county_key) andThen when {
            case $.feature_count >= $$.min_features => {
                yield QualityCheckedAtlas(status = "ok", html_path = $.html_path)
            }
            case _ => {
                yield QualityCheckedAtlas(status = "sparse_extract", html_path = $.html_path)
            }
        }
    }
}
```

## 9. Reuse the shipped workflows

Workflows compose like facets — wrap them instead of forking them.

```ffl
namespace my.atlas {

    use county.atlas.workflows

    /** Wrap the shipped single-county workflow and reshape its result. */
    workflow CountyWithHeadline(county_key: String = "north-america/us/oregon/coos") => (headline: String) andThen {

        built = county.atlas.workflows.BuildCountyAtlasMap(county_key = $.county_key, tier = 1)

        yield CountyWithHeadline(headline = "atlas: " ++ built.status)
    }
}
```

---

## Cheat sheet

| You want to… | Write |
|---|---|
| Read a workflow/step parameter | `$.name` (`$$.name` one level out) |
| Read a previous step's result | `stepname.field` |
| Fan out from a facet's list result | `step = List(…) andThen foreach v in $.field { … }` (then `$$` = workflow) |
| Fan out from a CLI list | `workflow W(items: Json) … andThen foreach i in $.items { … }` (`$` = workflow) |
| Order two independent steps | reference a field of the first from the second |
| More time / retries for one call | `… with Timeout(minutes = 90) with Retry(maxAttempts = 2, backoffSeconds = 120)` |
| Handle a step failure | `step = Facet(…) catch { yield … }` |
| Branch | `step = Facet(…) andThen when { case <bool> => { … } case _ => { … } }` |
| Concatenate strings | `a ++ b` |

**Validate before you run:** `afl my.ffl --check` or MCP `fw_validate`. Every error
carries a `rule_id` — fetch `fw://docs/rules/{rule_id}` for a wrong/right pair.

## See also

- The repo [`README.md`](../README.md) — the layer catalog and what an atlas contains
- [County Atlas design + as-built](https://github.com/rlemke/facetwork/blob/main/docs/architecture/county-atlas.md)
  (§9 records the 3,167-county national fan-out)
- [FFL grammar](https://github.com/rlemke/facetwork/blob/main/docs/reference/language/grammar.md) ·
  [canonical examples](https://github.com/rlemke/facetwork/tree/main/examples/canonical) ·
  [relative `$`-scoping](https://github.com/rlemke/facetwork/blob/main/docs/architecture/ffl-relative-scoping.md)
- `src/county_atlas/ffl/atlas.ffl` — the source of truth for every signature above
