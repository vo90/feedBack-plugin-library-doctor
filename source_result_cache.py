"""Bounded content-keyed chart work; never caches plans or mutation authority."""
import hashlib
import json
from collections import OrderedDict
from threading import RLock


class SourceResultCache:
    """Keep encoded JSON only; callers receive independent decoded values."""

    def __init__(self, *, max_bytes=16 * 1024 ** 2, max_entries=64, max_item_bytes=4 * 1024 ** 2):
        self.max_bytes, self.max_entries, self.max_item_bytes = max_bytes, max_entries, max_item_bytes
        self._items, self._bytes, self._lock = OrderedDict(), 0, RLock()

    def get(self, key):
        with self._lock:
            value = self._items.get(key)
            if value is None:
                return None
            self._items.move_to_end(key)
            return json.loads(value)

    def put(self, key, value):
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf8")
        if len(encoded) > min(self.max_item_bytes, self.max_bytes) or self.max_entries < 1:
            return False
        with self._lock:
            previous = self._items.pop(key, None)
            self._bytes -= len(previous) if previous is not None else 0
            self._items[key] = encoded
            self._bytes += len(encoded)
            while self._bytes > self.max_bytes or len(self._items) > self.max_entries:
                self._bytes -= len(self._items.popitem(last=False)[1])
            return True

    def patch(self, chart, document, target_sha256, source_sha256, entry):
        # Archive/member identity is included even when chart bytes coincide.
        key = ("patch", source_sha256, entry["member"], entry["sha256"], target_sha256,
               chart.recovery_patch)
        outcome = self.get(key)
        if outcome is None:
            try:
                outcome = {"result": chart.recovery_patch(document, entry["song"])}
            except chart.SourceBendError as exc:
                outcome = {"error": "bend", "message": str(exc)}
            except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError) as exc:
                outcome = {"error": "mismatch", "message": str(exc)}
            self.put(key, outcome)
        if outcome.get("error") == "bend":
            raise chart.SourceBendError(outcome["message"])
        if outcome.get("error") == "mismatch":
            raise ValueError(outcome["message"])
        return tuple(outcome["result"])

    def reports(self, service, module, raw, manifest, member):
        contexts = service._reviewed_validation_contexts(manifest, member)
        key = ("validation", hashlib.sha256(raw).hexdigest(), member,
               module._digest_json(contexts), service._validator_version,
               service._validate_reviewed_arrangement)
        result = self.get(key)
        if result is None:
            result = service._reviewed_validation_reports(module._parse_json(raw), manifest, member)
            self.put(key, result)
        return result
