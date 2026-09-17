import stat
import zipfile
from itertools import combinations

import pytest

from test_terminal_exact_suffix import exact_fixture, modules as modules


@pytest.mark.parametrize("name", ["../bad.json", "/bad", "a//b", "a/./b", "a\\b", "a:b",
                                  "a. /b", "a/b.", "", None])
def test_inventory_names_are_closed(modules, name):
    inputs = modules[0]._terminal._inputs
    with pytest.raises(ValueError):
        inputs.check_names([name])


@pytest.mark.parametrize("name", ["../outside/", "ambiguous. /", "arrangements/../other/"])
def test_zip_directory_paths_block_scan_and_preview(tmp_path, modules, name):
    service, package, _ = exact_fixture(tmp_path, modules, archive=True)
    with zipfile.ZipFile(package, "a") as archive:
        archive.writestr(name, b"")
    assessment = modules[1].validate_feedpak(package)["features"]["repair_eligibility"]
    assert assessment[modules[0]._terminal.RULE_CODE]["status"] != "automatic"
    assert not service.preview(package.name, modules[0]._terminal.RULE_CODE)["available"]


def test_linked_zip_directory_is_not_ignored(tmp_path, modules):
    service, package, _ = exact_fixture(tmp_path, modules, archive=True)
    with zipfile.ZipFile(package, "a") as archive:
        info = zipfile.ZipInfo("linked/")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, b"outside")
    with pytest.raises(ValueError, match="Linked"):
        modules[0]._terminal.package_inventory(package)
    assert not service.preview(package.name, modules[0]._terminal.RULE_CODE)["available"]


@pytest.mark.parametrize("archive", [False, True])
def test_empty_directories_count_toward_shared_inventory_limit(tmp_path, modules, monkeypatch, archive):
    service, package, _ = exact_fixture(tmp_path, modules, archive=archive)
    if archive:
        with zipfile.ZipFile(package, "a") as writer:
            for i in range(10):
                writer.writestr(f"empty{i}/", b"")
    else:
        for i in range(10):
            (package / f"empty{i}").mkdir()
    inputs = modules[0]._terminal._inputs
    monkeypatch.setattr(inputs, "MAX_MEMBERS", 10)
    monkeypatch.setattr(modules[1]._terminal, "MAX_INVENTORY_MEMBERS", 10)
    with pytest.raises(ValueError, match="inventory"):
        inputs.package_inventory(package)
    proof = modules[1].validate_feedpak(package)["features"]["repair_eligibility"]
    assert proof[modules[0]._terminal.RULE_CODE]["status"] != "automatic"
    assert not service.preview(package.name, modules[0]._terminal.RULE_CODE)["available"]


def test_aggregate_structure_and_read_budgets_are_bounded(modules, monkeypatch):
    inputs = modules[0]._terminal._inputs
    monkeypatch.setattr(inputs, "MAX_STRUCTURE_ITEMS", 10)
    budget = inputs.InputBudget({})
    budget.include([0] * 4)
    with pytest.raises(ValueError, match="structure"):
        budget.include([0] * 4)
    with pytest.raises(ValueError, match="structure"):
        inputs.check_structure({}, {"one": [0] * 4, "two": [0] * 4})
    with pytest.raises(ValueError, match="structure"):
        inputs.structure_size([0] * 20)
    with pytest.raises(ValueError, match="read limit"):
        inputs.InputBudget({}).reserve(4 * 1024 * 1024 * 1024 + 1)


def test_declared_input_count_and_shapes_are_bounded(modules, monkeypatch):
    inputs = modules[0]._terminal._inputs
    monkeypatch.setattr(inputs, "MAX_INPUTS", 1)
    with pytest.raises(ValueError, match="Too many"):
        inputs.input_paths({"arrangements": [{"file": "a.json"}, {"file": "b.json"}]})
    for manifest in ({"arrangements": [None]}, {"lyric_tracks": [None]}, {"lyrics": "a.jsonc"}):
        with pytest.raises(ValueError):
            inputs.input_paths(manifest)


def test_duplicate_and_case_colliding_names_are_rejected(modules):
    for names in (["a", "a"], ["a", "A"]):
        with pytest.raises(ValueError, match="ambiguous"):
            modules[0]._terminal._inputs.check_names(names)


def test_walk_io_failures_cannot_silently_omit_members(modules):
    with pytest.raises(OSError, match="denied"):
        modules[0]._terminal._inputs._walk_error(OSError("denied"))


def declare(manifest, role, path):
    if role == "arrangement":
        manifest.setdefault("arrangements", []).append({"id": "alias", "file": path})
    elif role == "lyric_track":
        manifest.setdefault("lyric_tracks", []).append({
            "id": "original", "file": path, "language": "en", "kind": "original",
        })
    else:
        manifest[role] = path


CROSS_ROLES = [pair for pair in combinations(
    ("arrangement", "song_timeline", "lyrics", "lyric_track", "rigs"), 2
) if pair != ("lyrics", "lyric_track")]


@pytest.mark.parametrize("roles", CROSS_ROLES)
def test_cross_role_aliases_are_rejected_by_shared_input_inventory(modules, roles):
    manifest = {}
    for role in roles:
        declare(manifest, role, "shared.json")
    inputs = modules[0]._terminal._inputs
    with pytest.raises(ValueError, match="conflicting input roles"):
        inputs.input_paths(manifest)
    with pytest.raises(ValueError, match="conflicting input roles"):
        inputs.check_inventory(manifest, ["manifest.yaml", "shared.json"])


@pytest.mark.parametrize("archive", [False, True])
@pytest.mark.parametrize("roles", CROSS_ROLES)
def test_cross_role_alias_scan_and_preview_agree(tmp_path, modules, archive, roles):
    def mutate(manifest, documents):
        shared = "arrangements/lead.json" if "arrangement" in roles else "shared.json"
        if shared not in documents:
            documents[shared] = []
        for role in roles:
            # The fixture already has the lead declaration; do not add a duplicate id.
            if role != "arrangement":
                declare(manifest, role, shared)
    service, package, _ = exact_fixture(tmp_path, modules, archive=archive, mutate=mutate)
    rule = modules[0]._terminal.RULE_CODE
    assessment = modules[1].validate_feedpak(package)["features"]["repair_eligibility"][rule]
    assert assessment["status"] != "automatic"
    assert "conflicting input roles" in assessment["message"]
    preview = service.preview(package.name, rule)
    assert not preview["available"]
    assert any("conflicting input roles" in blocker["message"] for blocker in preview["blockers"])


@pytest.mark.parametrize("archive", [False, True])
def test_repeated_lyrics_role_is_deduplicated_and_remains_repairable(tmp_path, modules, archive):
    def mutate(manifest, documents):
        declare(manifest, "lyrics", "lyrics.json")
        declare(manifest, "lyric_track", "lyrics.json")
        documents["lyrics.json"] = [{"t": 1.0, "d": 1.0, "w": "Synthetic"}]
    service, package, _ = exact_fixture(tmp_path, modules, archive=archive, mutate=mutate)
    rule = modules[0]._terminal.RULE_CODE
    assessment = modules[1].validate_feedpak(package)["features"]["repair_eligibility"][rule]
    assert assessment["status"] == "automatic", assessment
    assert service.preview(package.name, rule)["available"]
    manifest = {"lyrics": "lyrics.json", "lyric_tracks": [{"file": "lyrics.json"}]}
    assert modules[0]._terminal.input_paths(manifest) == ["lyrics.json"]


def test_case_aliases_are_not_silently_treated_as_distinct_declared_inputs(modules):
    inputs = modules[0]._terminal._inputs
    for other_role in ("lyric_track", "rigs", "song_timeline"):
        manifest = {"lyrics": "lyrics.json"}
        declare(manifest, other_role, "LYRICS.json")
        with pytest.raises(ValueError, match="conflicting input roles or path spellings"):
            inputs.input_paths(manifest)
