import json
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def _workflow() -> dict:
    return yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )


def test_minimum_host_ci_ref_matches_the_declared_contract():
    contract = json.loads((ROOT / "host-contract.json").read_text(encoding="utf-8"))
    matrix = _workflow()["jobs"]["host-contract"]["strategy"]["matrix"]["include"]

    minimum = [entry for entry in matrix if entry["name"] == "minimum-compatible"]

    assert len(minimum) == 1
    assert minimum[0]["ref"] == contract["minimumCompatibleBuild"]["commit"]


def test_latest_nightly_playwright_gate_runs_on_an_isolated_latest_host():
    job = _workflow()["jobs"]["latest-host-browser"]
    steps = job["steps"]
    host_checkouts = [
        step
        for step in steps
        if step.get("uses") == "actions/checkout@v4"
        and step.get("with", {}).get("repository") == "got-feedBack/feedBack"
    ]
    commands = [step.get("run", "") for step in steps]
    startup = next(
        step for step in steps if step.get("name") == "Start disposable latest host"
    )

    assert len(host_checkouts) == 1
    assert host_checkouts[0]["with"]["ref"] == "main"
    assert any("tools/build_release.py" in command for command in commands)
    assert any("unzip -q" in command for command in commands)
    assert not any("git archive" in command for command in commands)
    assert any(command.strip() == "npm run test:browser" for command in commands)
    assert startup["env"]["DLC_DIR"].startswith("${{ runner.temp }}")
    assert startup["env"]["CONFIG_DIR"].startswith("${{ runner.temp }}")
    assert startup["env"]["FEEDBACK_PLUGINS_DIR"].startswith("${{ runner.temp }}")
