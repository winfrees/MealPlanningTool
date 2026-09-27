"""Release versioning (scripts/next_version.py)."""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from next_version import next_version


@pytest.mark.parametrize(
    ("project", "tags", "expected"),
    [
        ("0.1.0", [], "0.1.0"),  # first release
        ("0.1.0", ["v0.1.0"], "0.1.1"),
        ("0.1.0", ["v0.1.0", "v0.1.1", "v0.1.7"], "0.1.8"),
        ("0.2.0", ["v0.1.0", "v0.1.1"], "0.2.0"),  # bumped series starts fresh
        ("0.2.3", ["v0.2.0"], "0.2.3"),  # a manual patch bump is respected
        ("0.1.0", ["v0.1.x", "release-1", "v0.10.4", "v1.1.9"], "0.1.0"),  # others ignored
    ],
)
def test_next_version(project, tags, expected):
    assert next_version(project, tags) == expected


def test_rejects_non_semver():
    with pytest.raises(ValueError, match=r"MAJOR\.MINOR\.PATCH"):
        next_version("0.1", [])


def test_command_line():
    script = Path(__file__).resolve().parent.parent / "scripts" / "next_version.py"
    out = subprocess.run(
        [sys.executable, str(script), "0.1.0", "v0.1.0", "v0.1.1"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "0.1.2"
