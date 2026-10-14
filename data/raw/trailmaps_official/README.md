# Official resort trail maps (page links and map files)

Each resort's own official trail map page or file, one YAML file per research batch, plus the map file itself when one
was found. `python pipeline/build.py` validates every file against `pipeline/schemas/trailmaps_official.py`
(`OfficialTrailmapBatch`) and copies the record to `Resort.official_trailmap` in `data/build/resorts.json`.

Official trail maps are copyrighted art owned by the resorts or their artists. Owner decision 2026-10-14: the trail
map dialog shows the map file straight from the resort's servers (hotlinked) with the credit "Trail map © <resort>"
and a link to the resort's page. Never download, copy, vendor, rehost or modify the images or PDFs, and never commit
an image file of them.

## Format

```yaml
batch: trailmaps-official-2026-10
researched_date: 2026-10-13
sources:
  - {id: tmo-example, url: https://www.example-resort.com/the-mountain/trail-map, title: Trail Map, accessed_date: 2026-10-13,
     note: From a search result for this page; page not opened}
  - {id: tmi-example, url: https://www.example-resort.com/media/maps/winter-2025-26-trail-map.pdf,
     title: Example Resort winter trail map 2025-26 (PDF), accessed_date: 2026-10-14,
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
    # The map file the trail map dialog shows (optional; the five fields come together):
    image_url: https://www.example-resort.com/media/maps/winter-2025-26-trail-map.pdf
    image_format: pdf                # jpg | png | webp | pdf
    image_source_id: tmi-example     # the search result or page where this exact file URL was seen
    image_as_of: 2026-10-14
    image_confidence: estimated
    image_note: null                 # optional, shown under the map (for example an older file)
```

## Rules

* `url` is https, on the resort's own domain or its operator's (vail.com, snow.com and the like). The build fails when
  the url's domain differs from its source's domain.
* `format` pdf, jpg or png needs a url ending in that extension; `page` must not point at a file.
* `season` looks like `2025/26` (consecutive years) or is null. Never guess it.
* One record per resort across all files; the source must be defined in some file under `data/raw`.
* `image_url` is https and ends in the extension of `image_format` (`.jpg` or `.jpeg`, `.png`, `.webp`, `.pdf`;
  a query string is fine), or an image CDN names the format in a `fmt=` parameter (scene7 style). Prefer an image
  (jpg, png or webp) at the highest resolution the resort publishes; take the PDF when there is no image.
* `image_url` sits on the resort's own site (the registered domain of `url` or its source) or on an operator image CDN
  listed in `config/definitions.yaml` under `trailmap.official_image_domains`. Add a CDN there, with a comment naming
  the operator, before using it. The file's own source (`image_source_id`) must be on the same site or CDN.
* Accept only file URLs seen in a search result or on a page. Never build one from a pattern; for an operator CDN
  template, confirm the pattern with one search and take only the per resort URLs actually seen.
* Search result rule: a URL taken from a web search result without opening the page carries the source note
  "From a search result for this page; page not opened" and confidence `estimated`. Never invent a URL.
* A file whose name carries an older season gets an `image_note` such as "The file name carries an older date; the
  resort may have a newer map."

## 2026-10-14 file batch

42 web searches (the search tool returns titles and URLs only, and never returned an image file), so every file found
is a PDF. 17 records gained `image_url`: Vail (Front Side 2024/25), Park City, Okemo, Stevens Pass (2024/25), Wilmot,
Mount Sunapee, Hidden Valley (PA), Liberty, Afton Alps, Crystal Mountain, Sugarbush, Snowshoe, Palisades Tahoe Alpine
(back side), June Mountain, Grand Targhee, Cranmore and Mount Bohemia. Seen but not used: Steamboat 2022/23 and
Breckenridge 2022/23 (both resorts changed terrain since), Beaver Creek's 2021 groomer map, Mammoth's beginner guide.
Named in a result without a URL (so not used): Magic Mountain `Magic_Trail Map_2026_site.jpg` and Buck Hill
`W2526 Buck Hill Trail Map FOR WEB`. Opening the resort pages when the network allows will find the image files.
