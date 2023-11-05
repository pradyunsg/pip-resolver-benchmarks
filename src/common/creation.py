"""Creates the actual wheel files in a directory to pass to the resolver.
"""

from __future__ import annotations

import base64
import hashlib
import zipfile
from pathlib import Path

from rich.progress import BarColumn, MofNCompleteColumn, Progress

from .model import DistributionInfo, Scenario

WHEEL = """\
Wheel-Version: 1.0
Generator: pip-resolver-benchmark
Root-Is-Purelib: true
Tag: py2-none-any
Tag: py3-none-any
"""


def _make_wheel(
    name: str, version: str, wheel: DistributionInfo, output_dir: Path
) -> Path:
    """Generate a wheel from a test case.

    The wheel is intentionally tagged as a universal wheel, so that the test case
    can be run on any platform (even).
    """
    underscore_name_version = (
        f"{name.replace('-', '_')}-{str(version).replace('-', '_')}"
    )
    archive_path = output_dir / name / f"{underscore_name_version}-py2.py3-none-any.whl"
    dist_info = f"{underscore_name_version}.dist-info"

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, mode="w") as archive:
        files = [
            (f"{dist_info}/METADATA", wheel.as_METADATA(name, version)),
            (f"{dist_info}/WHEEL", WHEEL),
            (f"{dist_info}/top_level.txt", name),
            (f"{dist_info}/entry_points.txt", ""),
        ]
        records = []
        for path, content in files:
            digest = hashlib.new("sha256", content.encode("utf-8")).digest()
            content_hash = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

            records.append((path, str(len(content)), f"sha256={content_hash}"))
            archive.writestr(path, content)

        records.append((f"{dist_info}/RECORD", "", ""))
        archive.writestr(f"{dist_info}/RECORD", "\n".join(",".join(r) for r in records))

    return archive_path


def _write_link_listing(path: Path, links: list[str]) -> None:
    """Write a link listing file."""
    parts = [
        "<!DOCTYPE html>" "<html>" "<head><title>Links for all packages</title></head>"
    ]
    for link in links:
        parts.append(f'<a href="{link}">{link}</a><br/>')
    parts.append("</html>")

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, mode="w") as fp:
        for part in parts:
            fp.write(part)


def _make_project_listing(name: str, wheel_names: list[Path], output_dir: Path) -> None:
    """Generate a project info file for a given package."""
    _write_link_listing(
        output_dir / name / "index.html",
        [wheel.name for wheel in wheel_names],
    )


def populate_with_scenario(scenario: Scenario, output_dir: Path) -> None:
    """Populate a directory with wheels for a given scenario."""
    output_dir.mkdir(parents=True, exist_ok=True)

    progress = Progress(
        "[progress.description]{task.description}",
        BarColumn(),
        MofNCompleteColumn(),
        refresh_per_second=30,
    )
    with progress:
        task_id = progress.add_task(
            "Generating wheels...",
            total=sum(
                len(distributions) for distributions in scenario.packages.values()
            ),
        )
        for name, distributions in scenario.packages.items():
            wheel_names = []
            for version, wheel_info in distributions.items():
                wheel_names.append(_make_wheel(name, version, wheel_info, output_dir))
                progress.advance(task_id)
            _make_project_listing(name, wheel_names, output_dir)
