import importlib.util
import json
import sys
import time
from pathlib import Path

import psutil
import pytest
import yaml


ROOT = Path(__file__).parents[1]
CORPUS = json.loads(
    (ROOT / "tests" / "fixtures" / "adversarial_validator_corpus.json").read_text(
        encoding="utf-8"
    )
)


@pytest.fixture(scope="module")
def validator():
    name = "library_doctor_adversarial_validator"
    spec = importlib.util.spec_from_file_location(name, ROOT / "validator.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(name, None)


@pytest.mark.parametrize(
    ("case"),
    CORPUS["safe_relpaths"],
    ids=lambda case: repr(case["value"]),
)
def test_adversarial_relative_path_corpus(validator, case):
    assert validator._safe_relpath(case["value"]) is case["safe"]


@pytest.mark.parametrize(
    ("case"),
    CORPUS["exact_identity"],
    ids=lambda case: case["name"],
)
def test_adversarial_exact_identity_corpus(validator, case):
    left = validator._exact_json_key(case["left"])
    right = validator._exact_json_key(case["right"])
    assert (left == right) is case["equal"]


def _package(root: Path, case: dict) -> Path:
    package = root / f"{case['name']}.feedpak"
    (package / "arrangements").mkdir(parents=True)
    (package / "stems").mkdir()
    manifest = {
        "feedpak_version": "1.19.0",
        "title": case["name"],
        "artist": "Adversarial corpus",
        "duration": 30.0,
        "arrangements": [{"id": "lead", "file": "arrangements/lead.json"}],
        "stems": [{"id": "full", "file": "stems/full.ogg"}],
    }
    (package / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    (package / "arrangements" / "lead.json").write_text(
        case["content"], encoding="utf-8"
    )
    (package / "stems" / "full.ogg").write_bytes(b"adversarial-audio")
    return package


@pytest.mark.parametrize(
    ("case"),
    CORPUS["arrangement_json"],
    ids=lambda case: case["name"],
)
def test_adversarial_json_corpus_is_bounded_and_reported(validator, tmp_path, case):
    package = _package(tmp_path, case)
    report = validator.validate_feedpak(package, package.name)
    codes = {finding["code"] for finding in report["findings"]}

    assert report["schema"] == "library_doctor.package.v1"
    assert report["package"] == package.name
    if case["required_code"] is not None:
        assert case["required_code"] in codes


@pytest.mark.performance
def test_adversarial_corpus_time_and_rss_budget(validator, tmp_path):
    process = psutil.Process()
    rss_before = process.memory_info().rss
    started = time.perf_counter()

    for case in CORPUS["arrangement_json"]:
        package = _package(tmp_path, case)
        report = validator.validate_feedpak(package, package.name)
        assert report["schema"] == "library_doctor.package.v1"

    elapsed = time.perf_counter() - started
    rss_growth = max(0, process.memory_info().rss - rss_before)
    print(
        f"adversarial_corpus_seconds={elapsed:.6f} "
        f"adversarial_corpus_rss_growth_bytes={rss_growth}"
    )
    assert elapsed < 5.0, f"adversarial corpus took {elapsed:.3f}s (budget 5s)"
    assert rss_growth < 128 * 1024 * 1024, (
        f"adversarial corpus grew RSS by {rss_growth} bytes (budget 128 MiB)"
    )
