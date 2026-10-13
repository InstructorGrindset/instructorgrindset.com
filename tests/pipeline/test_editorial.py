import datetime as dt
import json

import pytest
import yaml
from pydantic import ValidationError

from pipeline.editorial import approve, approve_in_file, load_editorial, render_review, resort_editorial
from pipeline.schemas.editorial import EditorialEntry, lint_text
from pipeline.sources import SourceRegistry

CTX = {"source_ids": {"ed-a", "ed-b"}}

EDITORIAL_YAML = """\
batch: editorial-fixture
researched_date: 2026-10-12
# synthetic test fixture
sources:
  - {id: ed-fixture-a, url: https://example.org/ed-a, title: Fixture editorial source A, accessed_date: 2026-10-12}
entries:
  - target: cluster
    id: i70-corridor
    overview_md: "Fixture towns house the workforce. Visitors arrive from the fixture metro."
    alexandre_take_md: null
    status: draft
    source_ids: [ed-fixture-a]
  - target: resort
    id: fixture-peak
    overview_md: "Fixture Peak sits above a fixture town."
    culture_md: "Locals ski early and often."
    alexandre_take_md: null
    status: reviewed
    source_ids: [ed-fixture-a]
  - target: resort
    id: fixture-basin
    overview_md: |
      Fixture Basin draws a full-time crowd. It is not a destination resort.
    culture_md: null
    alexandre_take_md: null
    status: draft   # waiting for Alexandre
    source_ids: [ed-fixture-a]
"""


def entry(**kw):
    base = {"target": "resort", "id": "r", "overview_md": "Text.", "status": "draft", "source_ids": ["ed-a"]}
    return EditorialEntry.model_validate({**base, **kw}, context=CTX)


def test_lint():
    assert lint_text("Clean short sentences.") == ([], [])
    errors, _ = lint_text("Big mountain — big town. Also an en dash – here.")
    assert len(errors) == 2
    _, warnings = lint_text("A full-time job on I-70 in 2025-26. See [the page](https://example.org/a-b-c).")
    assert warnings == ["hyphenated word 'full-time' (write it without a hyphen)",
                        "hyphenated word 'I-70' (write it without a hyphen)"]
    _, warnings = lint_text("The town is not quiet. Lifts aren't slow. It isn't flat.")
    assert len(warnings) == 3 and all("negative definition" in w for w in warnings)
    assert lint_text(None) == ([], [])


def test_entry_validation():
    with pytest.raises(ValidationError, match="style: overview_md: em or en dash"):
        entry(overview_md="One — two.")
    with pytest.raises(ValidationError, match="not defined"):
        entry(source_ids=["nowhere"])
    with pytest.raises(ValidationError):
        entry(source_ids=[])
    with pytest.raises(ValidationError):
        entry(status="published")
    with pytest.raises(ValidationError, match="resort entries only"):
        entry(target="cluster", culture_md="Culture.")
    with pytest.raises(ValidationError, match="reviewed entries only"):
        entry(reviewed_by="Alexandre")
    e = entry(overview_md="A full-time crowd.", status="reviewed", reviewed_by="Alexandre", reviewed_date="2026-10-12")
    assert e.lint_warnings() == ["overview_md: hyphenated word 'full-time' (write it without a hyphen)"]


def _raw(tmp_path, text=EDITORIAL_YAML):
    (tmp_path / "editorial").mkdir(parents=True, exist_ok=True)
    (tmp_path / "editorial" / "fixture.yaml").write_text(text)
    return tmp_path


def test_load_editorial(tmp_path):
    raw = _raw(tmp_path)
    reg = SourceRegistry()
    data, errors = load_editorial(raw, reg, {"fixture-peak", "fixture-basin"}, {"i70-corridor"})
    assert errors == [] and "ed-fixture-a" in reg
    assert set(data.reviewed("resort")) == {"fixture-peak"} and data.reviewed("cluster") == {}
    assert sorted(e.entry.id for e in data.drafts) == ["fixture-basin", "i70-corridor"]
    basin = next(e for e in data.entries if e.entry.id == "fixture-basin")
    assert len(basin.warnings) == 2
    out = resort_editorial(data.reviewed("resort")["fixture-peak"])
    assert out == {"overview_md": "Fixture Peak sits above a fixture town.", "culture_md": "Locals ski early and often.",
                   "alexandre_take_md": None, "status": "reviewed", "source_ids": ["ed-fixture-a"], "reviewed_date": None}
    assert resort_editorial(basin.entry) is None


def test_load_editorial_errors(tmp_path):
    raw = _raw(tmp_path, EDITORIAL_YAML.replace("id: fixture-basin", "id: ghost-resort"))
    (raw / "editorial" / "second.yaml").write_text(EDITORIAL_YAML.replace("editorial-fixture", "editorial-two")
                                                    .replace("ed-fixture-a, url", "ed-fixture-b, url").replace("ed-fixture-a]", "ed-fixture-b]"))
    _, errors = load_editorial(raw, SourceRegistry(), {"fixture-peak", "fixture-basin"}, {"i70-corridor"})
    text = "\n".join(errors)
    assert "resort 'ghost-resort': no such resort" in text
    assert "cluster 'i70-corridor': also defined in" in text


def test_approve_in_place(tmp_path):
    raw = _raw(tmp_path)
    path = approve(raw, "resort", "fixture-basin", "Alexandre", dt.date(2026, 10, 12))
    text = path.read_text()
    assert "# synthetic test fixture" in text and "# waiting for Alexandre" not in text.split("fixture-basin")[0]
    data = yaml.safe_load(text)
    basin = next(e for e in data["entries"] if e["id"] == "fixture-basin")
    assert (basin["status"], basin["reviewed_by"], basin["reviewed_date"]) == ("reviewed", "Alexandre", dt.date(2026, 10, 12))
    assert basin["overview_md"].startswith("Fixture Basin draws")  # block text untouched
    others = [e for e in data["entries"] if e["id"] != "fixture-basin"]
    assert [e["status"] for e in others] == ["draft", "reviewed"]
    # approving again replaces the review fields instead of duplicating them
    approve(raw, "resort", "fixture-basin", "Owner", dt.date(2026, 10, 13))
    text = path.read_text()
    assert text.count("reviewed_by") == 1 and '"Owner"' in text
    data, errors = load_editorial(raw, SourceRegistry(), {"fixture-peak", "fixture-basin"}, {"i70-corridor"})
    assert errors == [] and set(data.reviewed("resort")) == {"fixture-peak", "fixture-basin"}
    with pytest.raises(LookupError, match="no editorial entry"):
        approve(raw, "cluster", "wasatch", "Alexandre")


def test_approve_status_first_key(tmp_path):
    p = tmp_path / "e.yaml"
    p.write_text("batch: b\nresearched_date: 2026-10-12\nentries:\n  - status: draft\n    target: cluster\n    id: wasatch\n"
                 "    overview_md: Text.\n    source_ids: [x]\nsources: []\n")
    assert approve_in_file(p, "cluster", "wasatch", "Alexandre", dt.date(2026, 10, 12))
    e = yaml.safe_load(p.read_text())["entries"][0]
    assert e["status"] == "reviewed" and e["reviewed_by"] == "Alexandre"
    assert yaml.safe_load(p.read_text())["sources"] == []


def test_approve_cli(tmp_path, monkeypatch):
    import pipeline.editorial as ed
    raw = _raw(tmp_path / "data" / "raw")
    monkeypatch.setattr(ed, "REPO_ROOT", tmp_path)
    assert ed.main(["approve", "cluster", "i70-corridor", "--by", "Alexandre", "--date", "2026-10-12"]) == 0
    assert yaml.safe_load((raw / "editorial" / "fixture.yaml").read_text())["entries"][0]["status"] == "reviewed"
    assert ed.main(["approve", "cluster", "nowhere"]) == 1


def test_render_review(tmp_path):
    raw = _raw(tmp_path)
    reg = SourceRegistry()
    data, _ = load_editorial(raw, reg, {"fixture-peak", "fixture-basin"}, {"i70-corridor"})
    md = render_review(data, reg, {"i70-corridor": "I 70 Corridor"}, {("resort", "fixture-basin"): "Fixture Basin"})
    assert "0 of 1 Tier A clusters have a reviewed overview." in md and "I 70 Corridor (`i70-corridor`)" in md
    assert "### Resort: Fixture Basin (`fixture-basin`)" in md and "> Fixture Basin draws a full-time crowd." in md
    assert "[Fixture editorial source A](https://example.org/ed-a)" in md
    assert "hyphenated word 'full-time'" in md
    assert "python -m pipeline.editorial approve resort fixture-basin --by Alexandre" in md
    assert "fixture-peak" not in md  # reviewed entries are not listed as drafts
    assert json.dumps(md)  # plain text


def test_approve_all(tmp_path, monkeypatch):
    import pipeline.editorial as ed
    raw = _raw(tmp_path / "data" / "raw")
    monkeypatch.setattr(ed, "REPO_ROOT", tmp_path)
    assert ed.main(["approve-all", "--by", "site owner", "--date", "2026-10-13"]) == 0
    text = (raw / "editorial" / "fixture.yaml").read_text()
    entries = yaml.safe_load(text)["entries"]
    assert [e["status"] for e in entries] == ["reviewed"] * 3
    drafts_before = [e for e in entries if e["id"] in ("i70-corridor", "fixture-basin")]
    assert all(e["reviewed_by"] == "site owner" and e["reviewed_date"] == dt.date(2026, 10, 13) for e in drafts_before)
    assert "reviewed_by" not in next(e for e in entries if e["id"] == "fixture-peak")  # already reviewed: untouched
    assert "# synthetic test fixture" in text
    assert ed.approve_all(raw, "site owner") == (0, [])
