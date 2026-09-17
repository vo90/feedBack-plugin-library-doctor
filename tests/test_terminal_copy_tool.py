import json

import pytest

from test_terminal_exact_suffix import exact_fixture, modules as modules
from tools import verify_terminal_copy


def test_isolated_copy_tool_exercises_backup_rescan_and_undo(tmp_path, modules):
    _, package, _ = exact_fixture(tmp_path, modules, archive=True)
    original = verify_terminal_copy.digest(package)
    output = tmp_path / "isolated"
    result = verify_terminal_copy.verify(package, output, 8, 20)
    assert result["passed"] and result["source_unchanged"]
    assert result["removed_count"] == 8
    assert result["undo_restored_every_member"]
    assert result["playback_checked"] is False
    assert json.loads((output / "result.json").read_text()) == result
    assert verify_terminal_copy.digest(package) == original


@pytest.mark.parametrize("existing", [False, True])
def test_copy_tool_refuses_unsafe_output_or_non_feedpak(tmp_path, existing):
    source = tmp_path / "not-a-song.txt"
    source.write_text("untouched")
    output = tmp_path / "output"
    if existing:
        output.mkdir()
    with pytest.raises(ValueError):
        verify_terminal_copy.verify(source, output)
    assert source.read_text() == "untouched"
