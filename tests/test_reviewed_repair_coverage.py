import ast
import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]
REQUIRED_OWNERSHIP = {
    "candidate",
    "decision",
    "blocker",
    "preservation",
    "stale_tamper",
    "validation",
    "undo",
    "api",
    "frontend",
}


def _load_reviewed():
    name = f"library_doctor_reviewed_coverage_{id(ROOT)}"
    spec = importlib.util.spec_from_file_location(name, ROOT / "reviewed_repair.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _coverage():
    return json.loads(
        (ROOT / "tests" / "fixtures" / "reviewed_repair_coverage.json")
        .read_text(encoding="utf-8")
    )


def test_every_reviewed_adapter_has_complete_test_ownership():
    adapters = {
        item["adapter_id"] for item in _load_reviewed().reviewed_repair_catalog()
    }
    coverage = _coverage()

    assert coverage["schema"] == "library_doctor.reviewed_repair_test_coverage.v1"
    assert set(coverage["adapters"]) == adapters
    for ownership in coverage["adapters"].values():
        assert set(ownership) == REQUIRED_OWNERSHIP
        assert all(ownership[kind] for kind in REQUIRED_OWNERSHIP)


def test_reviewed_python_coverage_owners_name_real_test_functions():
    owners = {
        owner
        for ownership in _coverage()["adapters"].values()
        for kind, kind_owners in ownership.items()
        if kind != "frontend"
        for owner in kind_owners
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


def test_reviewed_frontend_coverage_owners_name_real_tests():
    for ownership in _coverage()["adapters"].values():
        for owner in ownership["frontend"]:
            relative_path, separator, test_name = owner.partition("::")
            assert separator == "::", f"invalid frontend test owner: {owner}"
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            assert f"test('{test_name}'" in source, f"missing frontend test: {owner}"
