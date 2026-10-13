# Official resort trail maps (links only)

Each resort's own official trail map page or file, one YAML file per research batch. `python pipeline/build.py`
validates every file against `pipeline/schemas/trailmaps_official.py` (`OfficialTrailmapBatch`) and copies the link to
`Resort.official_trailmap` in `data/build/resorts.json`.

Official trail maps are copyrighted art owned by the resorts or their artists. The site links to them. Never download,
vendor, rehost, hotlink or embed the images or PDFs.

## Format

```yaml
batch: trailmaps-official-2026-10
researched_date: 2026-10-13
sources:
  - {id: tmo-example, url: https://www.example-resort.com/the-mountain/trail-map, title: Trail Map, accessed_date: 2026-10-13,
     note: From a search result for this page; page not opened}
trailmaps:
  - resort_id: example-resort        # a resort id in data/build/resorts.json
    url: https://www.example-resort.com/the-mountain/trail-map
    format: page                     # pdf | jpg | png | page
    season: "2025/26"                # only when the source names the season, else null
    source_id: tmo-example
    as_of: 2026-10-13
    confidence: estimated            # published once someone opens the page and checks it
    note: null                       # optional
```

## Rules

* `url` is https, on the resort's own domain or its operator's (vail.com, snow.com and the like). The build fails when
  the url's domain differs from its source's domain.
* `format` pdf, jpg or png needs a url ending in that extension; `page` must not point at a file.
* `season` looks like `2025/26` (consecutive years) or is null. Never guess it.
* One record per resort across all files; the source must be defined in some file under `data/raw`.
* Search result rule: a URL taken from a web search result without opening the page carries the source note
  "From a search result for this page; page not opened" and confidence `estimated`. Never invent a URL.
