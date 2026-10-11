# CLAUDE.md

Conventions for working in the instructorgrindset.com repository. Read `ROADMAP.md` (Career Map plan) and `docs/map/ARCHITECTURE.md` (data contract) before changing anything under `/map/`, `/pipeline`, `/config`, or `/data`.

## Site

* Hand built static HTML served by GitHub Pages from the repository root (`CNAME` = instructorgrindset.com). There is no site build step.
* Shared styles: `/assets/global.css` (design tokens) and `/assets/sidebar.css`. The sidebar is injected at runtime from `/partials/sidebar.html`; copy the placeholder and fetch snippet from an existing page such as `/income-calculator/index.html`.
* `_config.yml` keeps repository only material (pipeline, raw data, tests, docs, this file) out of the published site. Add new non public folders there.

## Career Map rules

* **Data lives only in `/data`.** Raw inputs in `/data/raw`, compiled output in `/data/build`. Frontend code never hardcodes resort facts.
* **Every record needs a source.** Every displayed number carries `source_id`, `as_of`, and `confidence`. Pipeline validation fails on a missing source.
* **No invented numbers.** If a value cannot be sourced, leave it `null`. Never fill gaps with guesses, mock data, or memory. Synthetic data is allowed only inside `/tests` fixtures and must never reach `/data/build`.
* **Editorial text** is drafted only from cited sources and stays `status: draft` until Alexandre marks it `reviewed`. Drafts never render in production.
* **Ownership** (Vail, Alterra) is verified from the companies' own sites or filings each build cycle, never from memory.
* **Individual crowdsourced reports are never displayed**, only aggregates of 3 or more.
* All thresholds, weights, and assumptions live in `/config/definitions.yaml`.
* Map JavaScript and vendored libraries load only on `/map/` routes. The homepage must stay unaffected.

## User facing copy (Alexandre's style)

* No hyphens and no em dashes in prose. Write "full time", "high net worth", "I 70".
* No negative definitions ("X is not Y"). Say what a thing is.
* Clean, short sentences.

## Commands

* `pip install -r requirements.txt`
* `python pipeline/build.py` compiles `/data/raw` + `/config` into `/data/build`.
* `python -m pytest` runs every pipeline and score test. Every transform and every score formula needs a test.
* Preview the site: `python -m http.server 8000` from the repository root, then open `http://localhost:8000/map/`.

## Workflow

* One roadmap phase per session; plan first.
* Research sessions write only to `/data/raw` with sources.
* Commit after each completed acceptance criterion.
