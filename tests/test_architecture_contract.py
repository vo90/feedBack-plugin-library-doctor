import ast
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]


def _imports(source: str) -> set[str]:
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_backend_module_ownership_and_size_boundaries_are_explicit():
    contract = json.loads(
        (ROOT / "architecture-contract.json").read_text(encoding="utf-8")
    )

    assert contract["schema"] == "library_doctor.architecture_contract.v1"
    assert set(contract["modules"]) == {
        "routes.py",
        "route_support.py",
        "scanner.py",
        "library_doctor_report_cache.py",
        "library_doctor_scan_policy.py",
        "repair.py",
        "repair_catalog.py",
        "repair_actions.py",
        "repair_workspace.py",
        "repair_recovery.py",
        "repair_transaction.py",
        "repair_yaml.py",
    }
    for relative, boundary in contract["modules"].items():
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert len(source.splitlines()) <= boundary["maxLines"], relative
        assert boundary["responsibility"].endswith(".")
        imported = _imports(source)
        for forbidden in boundary["forbiddenImports"]:
            assert not any(
                name == forbidden or name.startswith(f"{forbidden}.")
                for name in imported
            ), f"{relative} must not import {forbidden}"


def test_extracted_backend_boundaries_remain_wired_through_stable_seams():
    routes = (ROOT / "routes.py").read_text(encoding="utf-8")
    scanner = (ROOT / "scanner.py").read_text(encoding="utf-8")
    repair = (ROOT / "repair.py").read_text(encoding="utf-8")

    assert 'load_sibling("route_support")' in routes
    assert 'with_name("library_doctor_scan_policy.py")' in scanner
    assert "choose_worker_policy = _scan_policy.choose_worker_policy" in scanner
    assert 'with_name("library_doctor_report_cache.py")' in scanner
    assert "_ReportCache = _report_cache.ReportCache" in scanner
    assert 'with_name("repair_actions.py")' in repair
    assert "RepairDefinition = _actions.RepairDefinition" in repair
    assert 'with_name("repair_catalog.py")' in repair
    assert "_REPAIR_DEFINITIONS = _catalog.SAFE_REPAIR_DEFINITIONS" in repair
    assert 'with_name("repair_workspace.py")' in repair
    assert "create_candidate_workspace" in repair
    assert 'with_name("repair_recovery.py")' in repair
    assert "_recovery.RecoveryPolicy" in repair
    assert 'with_name("repair_transaction.py")' in repair
    assert "_transaction.TransactionJournal" in repair
    assert 'with_name("repair_yaml.py")' in repair
    assert "_yaml.UniqueSafeLoader" in repair
