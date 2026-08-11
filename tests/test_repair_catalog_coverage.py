import ast
import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]


def _load_repair():
    name = f"library_doctor_catalog_coverage_{id(ROOT)}"
    spec = importlib.util.spec_from_file_location(name, ROOT / "repair.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _coverage_map():
    return json.loads(
        (
            ROOT
            / "tests"
            / "fixtures"
            / "repair_catalog_coverage.json"
        ).read_text(encoding="utf-8")
    )


def test_every_catalog_rule_has_an_explicit_executable_test_owner():
    catalog_codes = {
        item["rule_code"] for item in _load_repair().repair_catalog()
    }
    coverage = _coverage_map()

    assert coverage["schema"] == "library_doctor.repair_test_coverage.v1"
    assert set(coverage["rules"]) == catalog_codes
    assert all(owners for owners in coverage["rules"].values())


def test_every_catalog_coverage_owner_names_a_real_test_function():
    owners = {
        owner
        for rule_owners in _coverage_map()["rules"].values()
        for owner in rule_owners
    }
    for owner in sorted(owners):
        relative_path, separator, function_name = owner.partition("::")
        assert separator == "::", f"invalid pytest node id: {owner}"
        path = ROOT / relative_path
        assert path.is_file(), f"missing test module: {relative_path}"
        functions = {
            node.name
            for node in ast.parse(path.read_text(encoding="utf-8")).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert function_name in functions, f"missing test function: {owner}"


def test_media_catalog_entries_share_the_reviewed_preview_contract():
    repair = _load_repair()
    media = {
        item["rule_code"]: item
        for item in repair.repair_catalog()
        if item["rule_code"].startswith("media.preview-")
    }

    assert set(media) == {
        "media.preview-missing",
        "media.preview-too-short",
        "media.preview-too-long",
        "media.preview-regenerate",
    }
    assert {item["safety"] for item in media.values()} == {"review_required"}
    assert {item["source_kind"] for item in media.values()} == {"full_mix"}
    assert {item["change_kind"] for item in media.values()} == {"replace_media"}
