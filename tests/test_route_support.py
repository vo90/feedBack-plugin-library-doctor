import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]


def _support_module():
    name = "library_doctor_route_support_unit_test"
    spec = importlib.util.spec_from_file_location(name, ROOT / "route_support.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_receipt_status_mapping_preserves_mutation_and_lookup_contracts():
    support = _support_module()
    errors = support.RouteErrors(lambda code, message, **facts: {
        "code": code,
        "message": message,
        **facts,
    })

    assert errors.receipt_status_code("receipt_not_found") == 404
    assert errors.receipt_status_code("idempotency_store_unavailable") == 503
    assert errors.receipt_status_code("idempotency_store_full") == 503
    assert errors.receipt_status_code(
        "idempotency_store_full",
        store_full_is_unavailable=False,
    ) == 409
    assert errors.receipt_status_code("request_id_conflict") == 409


def test_repair_error_mapping_keeps_file_state_and_recovery_direction():
    support = _support_module()
    errors = support.RouteErrors(lambda code, message, **facts: {
        "code": code,
        "message": message,
        **facts,
    })
    error = type("RepairError", (), {
        "code": "source_changed",
        "file_state": "unchanged",
        "__str__": lambda self: "The package changed.",
    })()

    assert errors.repair_detail(error) == {
        "code": "source_changed",
        "message": "The package changed.",
        "file_state": "unchanged",
        "retryable": True,
        "next_action": "scan_again",
    }

    recovery = type("RepairError", (), {
        "code": "recovery_required",
        "file_state": "recovery_required",
        "__str__": lambda self: "Resolve the interrupted repair.",
    })()
    assert errors.repair_detail(recovery) == {
        "code": "recovery_required",
        "message": "Resolve the interrupted repair.",
        "file_state": "recovery_required",
        "retryable": False,
        "next_action": "resolve_recovery",
    }
