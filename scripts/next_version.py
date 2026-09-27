"""Next release version for the release workflow (.github/workflows/release.yml).

Usage: python scripts/next_version.py <pyproject version> [existing tags...]

The pyproject version sets the series: with version 0.1.0 and no v0.1.* tags the first release
is v0.1.0; after that each merge takes the next patch (v0.1.1, v0.1.2, ...). Bumping pyproject
to 0.2.0 starts a new series. Prints the version without the leading "v".
"""

import re
import sys

_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def next_version(project_version: str, tags: list[str]) -> str:
    match = _SEMVER.match(project_version)
    if match is None:
        raise ValueError(f"project version must be MAJOR.MINOR.PATCH, got {project_version!r}")
    major, minor, patch = (int(g) for g in match.groups())
    series = re.compile(rf"^v{major}\.{minor}\.(\d+)$")
    released = [int(m[1]) for t in tags if (m := series.match(t.strip()))]
    if not released:
        return f"{major}.{minor}.{patch}"
    return f"{major}.{minor}.{max(max(released) + 1, patch)}"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    print(next_version(sys.argv[1], sys.argv[2:]))
