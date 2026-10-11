import pytest
import yaml
from pydantic import ValidationError

from pipeline.config import Definitions, load_clusters, load_definitions

EXPECTED_CLUSTERS = [
    "i70-corridor", "roaring-fork", "northern-colorado", "southwest-colorado", "wasatch", "lake-tahoe",
    "eastern-sierra", "southern-california", "tetons", "wood-river", "southwest-montana",
    "northern-new-mexico", "cascades", "inland-northwest", "northern-vermont", "southern-vermont",
    "white-mountains", "western-maine", "appalachia", "alaska",
]


def test_definitions_section_6_values(cfg):
    d = cfg.definitions
    assert d.snow.powder_day_in == 6 and d.snow.banner_day_in == 12 and d.snow.window_hours == 24
    assert (d.density.dry_max_pct, d.density.moist_min_pct) == (7, 12)
    assert d.season.lookback_seasons == 10
    assert d.display.priority == ["published", "reported", "estimated"]
    assert d.reports.aggregation_min == 3
    assert d.staleness.max_age_months == 18


def test_weights_and_assumptions_are_unset(cfg):
    assert all(v is None for v in cfg.definitions.professionalism.weights.model_dump().values())
    assert not cfg.definitions.professionalism.weights_set
    assert all(v is None for v in cfg.definitions.lucrativeness.assumptions.model_dump().values())


def test_density_classes(cfg):
    c = cfg.definitions.density.classify
    assert [c(5), c(7), c(10), c(12), c(13)] == ["dry", "average", "average", "average", "moist"]


def test_size_rank(cfg):
    r = cfg.definitions.display.size_rank
    assert r(None) == 1 and r(100) == 1 and r(500) == 2 and r(2000) == 3 and r(4000) == 4 and r(5317) == 5


def test_invalid_definitions_rejected(tmp_path, repo_root):
    data = yaml.safe_load((repo_root / "config/definitions.yaml").read_text())
    data["snow"]["banner_day_in"] = 4
    with pytest.raises(ValidationError, match="banner"):
        Definitions.model_validate(data)
    data = yaml.safe_load((repo_root / "config/definitions.yaml").read_text())
    data["unexpected"] = 1
    p = tmp_path / "d.yaml"
    p.write_text(yaml.safe_dump(data))
    with pytest.raises(ValidationError, match="Extra inputs"):
        load_definitions(p)


def test_clusters_config(cfg):
    assert [c.id for c in cfg.clusters.clusters] == EXPECTED_CLUSTERS
    assert cfg.clusters.membership.tier_b_radius_km == 80
    tahoe = cfg.clusters.by_id()["lake-tahoe"]
    assert tahoe.states == ["CA", "NV"]
    for c in cfg.clusters.clusters:
        assert "-" not in c.name, "cluster display names follow the no hyphen style rule"


def test_duplicate_and_reserved_cluster_ids(tmp_path, repo_root):
    data = yaml.safe_load((repo_root / "config/clusters.yaml").read_text())
    data["clusters"].append(dict(data["clusters"][0]))
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(data))
    with pytest.raises(ValidationError, match="duplicate"):
        load_clusters(p)
    data = yaml.safe_load((repo_root / "config/clusters.yaml").read_text())
    data["clusters"].append({"id": "other-co", "name": "X", "states": ["CO"]})
    p.write_text(yaml.safe_dump(data))
    with pytest.raises(ValidationError, match="fallback prefix"):
        load_clusters(p)
