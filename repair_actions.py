"""Immutable action values shared by repair planners and appliers."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RepairDefinition:
    rule_code: str
    action_kind: str
    source_kind: str
    item_name: str
    safety: str
    title: str
    description: str
    player_result: str
    user_value: str
    change_kind: str = "remove_duplicates"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DuplicateGroup:
    keep_index: int
    remove_indices: tuple[int, ...]
    entry_sha256: str

    def to_dict(self) -> dict:
        return {
            "keep_index": self.keep_index,
            "remove_indices": list(self.remove_indices),
            "entry_sha256": self.entry_sha256,
        }


@dataclass(frozen=True)
class DeleteArrayItems:
    array_path: tuple[str | int, ...]
    expected_length: int
    duplicate_groups: tuple[DuplicateGroup, ...]

    @property
    def remove_indices(self) -> tuple[int, ...]:
        return tuple(sorted(
            (
                index
                for group in self.duplicate_groups
                for index in group.remove_indices
            ),
            reverse=True,
        ))

    def to_dict(self) -> dict:
        return {
            "operation": "delete_array_items",
            "array_path": list(self.array_path),
            "expected_length": self.expected_length,
            "remove_indices": list(self.remove_indices),
            "duplicate_groups": [
                group.to_dict() for group in self.duplicate_groups
            ],
        }


@dataclass(frozen=True)
class ChordMatchGroup:
    chord_index: int
    chord_note_index: int
    chord_sha256: str
    remove_indices: tuple[int, ...]
    entry_sha256: str

    def to_dict(self) -> dict:
        return {
            "chord_index": self.chord_index,
            "chord_note_index": self.chord_note_index,
            "chord_sha256": self.chord_sha256,
            "remove_indices": list(self.remove_indices),
            "entry_sha256": self.entry_sha256,
        }


@dataclass(frozen=True)
class DeleteNotesMatchingChords:
    note_array_path: tuple[str | int, ...]
    chord_array_path: tuple[str | int, ...]
    expected_note_length: int
    expected_chord_length: int
    match_groups: tuple[ChordMatchGroup, ...]

    @property
    def remove_indices(self) -> tuple[int, ...]:
        return tuple(sorted(
            (
                index
                for group in self.match_groups
                for index in group.remove_indices
            ),
            reverse=True,
        ))

    def to_dict(self) -> dict:
        return {
            "operation": "delete_notes_matching_chords",
            "note_array_path": list(self.note_array_path),
            "chord_array_path": list(self.chord_array_path),
            "expected_note_length": self.expected_note_length,
            "expected_chord_length": self.expected_chord_length,
            "remove_indices": list(self.remove_indices),
            "match_groups": [group.to_dict() for group in self.match_groups],
        }


@dataclass(frozen=True)
class OmitEmptyRootArray:
    field: str
    original_sha256: str
    result_sha256: str

    @property
    def remove_indices(self) -> tuple[int, ...]:
        return ()

    def to_dict(self) -> dict:
        return {
            "operation": "omit_empty_root_array",
            "array_path": [self.field],
            "field": self.field,
            "original_sha256": self.original_sha256,
            "result_sha256": self.result_sha256,
        }


@dataclass(frozen=True)
class RedundantHandshapeMatch:
    handshape_index: int
    chord_index: int
    handshape_sha256: str
    chord_sha256: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DeleteRedundantHandshapes:
    span_kind: str
    handshape_array_path: tuple[str | int, ...]
    chord_array_path: tuple[str | int, ...]
    expected_handshape_length: int
    expected_chord_length: int
    match_groups: tuple[RedundantHandshapeMatch, ...]

    @property
    def remove_indices(self) -> tuple[int, ...]:
        return tuple(sorted(
            (group.handshape_index for group in self.match_groups),
            reverse=True,
        ))

    def to_dict(self) -> dict:
        return {
            "operation": "delete_redundant_handshapes",
            "span_kind": self.span_kind,
            "handshape_array_path": list(self.handshape_array_path),
            "chord_array_path": list(self.chord_array_path),
            "expected_handshape_length": self.expected_handshape_length,
            "expected_chord_length": self.expected_chord_length,
            "remove_indices": list(self.remove_indices),
            "match_groups": [group.to_dict() for group in self.match_groups],
        }


@dataclass(frozen=True)
class MutedFretChange:
    note_index: int
    original_fret: int
    replacement_fret: int
    note_sha256: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class NormalizeMutedNegativeFrets:
    note_array_path: tuple[str | int, ...]
    expected_length: int
    changes: tuple[MutedFretChange, ...]

    @property
    def remove_indices(self) -> tuple[int, ...]:
        return ()

    @property
    def change_count(self) -> int:
        return len(self.changes)

    def to_dict(self) -> dict:
        return {
            "operation": "normalize_muted_negative_frets",
            "note_array_path": list(self.note_array_path),
            "expected_length": self.expected_length,
            "changes": [change.to_dict() for change in self.changes],
        }


@dataclass(frozen=True)
class StableSortBendPoints:
    array_path: tuple[str | int, ...]
    expected_length: int
    original_sha256: str
    sorted_sha256: str
    sorted_indices: tuple[int, ...]
    moved_count: int
    note_time: float | None
    string: int | None

    @property
    def remove_indices(self) -> tuple[int, ...]:
        return ()

    def to_dict(self) -> dict:
        return {
            "operation": "stable_sort_bend_points",
            "array_path": list(self.array_path),
            "expected_length": self.expected_length,
            "original_sha256": self.original_sha256,
            "sorted_sha256": self.sorted_sha256,
            "sorted_indices": list(self.sorted_indices),
            "moved_count": self.moved_count,
            "note_time": self.note_time,
            "string": self.string,
        }


@dataclass(frozen=True)
class StableSortLyricCues:
    expected_length: int
    original_sha256: str
    sorted_sha256: str
    sorted_indices: tuple[int, ...]
    moved_count: int

    @property
    def remove_indices(self) -> tuple[int, ...]:
        return ()

    def to_dict(self) -> dict:
        return {
            "operation": "stable_sort_lyric_cues",
            "array_path": [],
            "expected_length": self.expected_length,
            "original_sha256": self.original_sha256,
            "sorted_sha256": self.sorted_sha256,
            "sorted_indices": list(self.sorted_indices),
            "moved_count": self.moved_count,
        }


@dataclass(frozen=True)
class StableSortTimelineMarkers:
    field: str
    expected_length: int
    original_sha256: str
    sorted_sha256: str
    sorted_indices: tuple[int, ...]
    moved_count: int

    @property
    def remove_indices(self) -> tuple[int, ...]:
        return ()

    def to_dict(self) -> dict:
        return {
            "operation": "stable_sort_timeline_markers",
            "array_path": [self.field],
            "field": self.field,
            "expected_length": self.expected_length,
            "original_sha256": self.original_sha256,
            "sorted_sha256": self.sorted_sha256,
            "sorted_indices": list(self.sorted_indices),
            "moved_count": self.moved_count,
        }


@dataclass(frozen=True)
class StableSortTimedEvents:
    array_path: tuple[str | int, ...]
    time_key: str
    expected_length: int
    original_sha256: str
    sorted_sha256: str
    sorted_indices: tuple[int, ...]
    moved_count: int

    @property
    def remove_indices(self) -> tuple[int, ...]:
        return ()

    def to_dict(self) -> dict:
        return {
            "operation": "stable_sort_timed_events",
            "array_path": list(self.array_path),
            "time_key": self.time_key,
            "expected_length": self.expected_length,
            "original_sha256": self.original_sha256,
            "sorted_sha256": self.sorted_sha256,
            "sorted_indices": list(self.sorted_indices),
            "moved_count": self.moved_count,
        }
