"""Fetch scenario information from a bunch of top-level package names.

Note that this can (and does) take a while to run, since this script...

- can execute arbitrary code, as a part of generating sdist metadata.
- will create and throw away a _lot_ of temporary files.
- will make a lot of HTTP requests.
- will fetch the metadata for *all* the versions of *all* the packages that *could* be
  in the dependency graph of the input packages.

"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import tempfile
from collections import defaultdict, deque
from pathlib import Path
from types import TracebackType
from typing import Annotated, Iterable, Optional, Type, Union
from urllib.parse import urljoin

import hishel
import rich
import rich.progress
from mousebender.simple import create_project_url, parse_project_details
from packaging.markers import (
    Marker,
    MarkerAtom,
    MarkerList,
    Variable,
    default_environment,
)
from packaging.metadata import Metadata
from packaging.requirements import Requirement
from packaging.tags import Tag, sys_tags
from packaging.utils import (
    BuildTag,
    NormalizedName,
    canonicalize_name,
    parse_sdist_filename,
    parse_wheel_filename,
    InvalidSdistFilename
)
from packaging.version import Version
from rich.live import Live
from rich.markup import escape
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from typing_extensions import TypeAlias

from common.hacks import create_session, metadata_from_wheel_url
from common.model import (
    SCENARIOS_DIR,
    DistributionInfo,
    EnvironmentDetails,
    Scenario,
    ScenarioInput,
)

TAGS = tuple(sys_tags())
TAGS_SET = set(TAGS)

ParsedWheelName: TypeAlias = tuple[NormalizedName, Version, BuildTag, frozenset[Tag]]
ParsedSDistName: TypeAlias = tuple[NormalizedName, str]
ParsedDistributionDetail: TypeAlias = tuple[
    Union[ParsedWheelName, ParsedSDistName], Annotated[str, "url to distro"]
]


def determine_configured_index() -> str:
    """Return the index URL to generating the test case."""
    return "https://pypi.org/simple/"
    try:
        output = subprocess.run(
            [sys.executable, "-m", "pip", "config", "get", "global.index-url"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout
    except subprocess.CalledProcessError:
        return "https://pypi.org/simple/"
    else:
        return output.removesuffix("\n")


class SdistFailure(Exception):
    """Raised when an sdist could not be processed.

    This is mainly used for flow-control, since an sdist that does not build
    typically indicates that older sdists will also not build (and reaching there
    would trigger a pip error anyway).
    """


#
# User Interface
#
class UserVisibleProgress:
    def __init__(self) -> None:
        super().__init__()
        self.progress_overall = Progress(
            "[progress.description]{task.description:<40}",
            TimeElapsedColumn(),
            BarColumn(),
            MofNCompleteColumn(" done of "),
        )
        self.progress_job = Progress(
            "[progress.description]{task.description:<46}",
            SpinnerColumn(),
            BarColumn(),
            TextColumn(
                "{task.completed:4.0f} of {task.total:4.0f}",
                style="progress.download",
            ),
        )

        progress_table = Table.grid()
        progress_table.add_row(self.progress_overall)
        progress_table.add_row(self.progress_job)

        self.live = Live(progress_table, refresh_per_second=30)

    def __enter__(self) -> None:
        self.task_overall = self.progress_overall.add_task(
            "Overall iterations",
        )
        self.task_grouping_files = self.progress_job.add_task(
            "Filter and group dists (by version)",
        )
        self.task_fetch_metadata = self.progress_job.add_task(
            "Fetch metadata (for best dists)"
        )
        self.task_dependency_processing = self.progress_job.add_task(
            "Process dependency information"
        )

        self.live.__enter__()
        return self

    def __exit__(self, *args) -> None:
        self.live.__exit__(*args)
        self.progress_overall.remove_task(self.task_overall)
        self.progress_job.remove_task(self.task_grouping_files)
        self.progress_job.remove_task(self.task_fetch_metadata)
        self.progress_job.remove_task(self.task_dependency_processing)

    def set_overall_count(self, total: int, /) -> None:
        self.progress_overall.update(self.task_overall, total=total)

    def set_total_dists_count(self, count: int, /) -> None:
        self.progress_job.update(self.task_grouping_files, total=count)

    def set_total_version_count(self, count: int, /) -> None:
        self.progress_job.update(self.task_fetch_metadata, total=count)

    def set_total_metadata_fetched_count(self, count: int, /) -> None:
        self.progress_job.update(self.task_dependency_processing, total=count)

    def on_start_package(self, package: str, /) -> None:
        self.progress_overall.update(self.task_overall, description=package)
        self.progress_job.update(self.task_grouping_files, completed=0, total=0)
        self.progress_job.update(self.task_fetch_metadata, completed=0, total=0)
        self.progress_job.update(self.task_dependency_processing, completed=0, total=0)

    def on_file_grouped(self) -> None:
        self.progress_job.update(self.task_grouping_files, advance=1)

    def on_metadata_fetched(self) -> None:
        self.progress_job.update(self.task_fetch_metadata, advance=1)

    def on_dependencies_processed(self) -> None:
        self.progress_job.update(self.task_dependency_processing, advance=1)

    def on_package_finished(self) -> None:
        self.progress_overall.update(self.task_overall, advance=1)


def _pick_best_candidate_dist(
    dist_details: list[ParsedDistributionDetail],
) -> Optional[str]:
    """Return the best-matching distribution from the given list.

    This will pick the wheel matching the earliest compatible tag, or the sdist if no
    wheels are available and an sdist is available.
    """
    source_dist_details = [
        (parsed_filename, url)
        for parsed_filename, url in dist_details
        if len(parsed_filename) == 2
    ]

    retval_url = None
    current_index = len(TAGS)
    for parsed_filename, url in dist_details:
        if len(parsed_filename) == 2:
            continue

        tags = parsed_filename[-1]
        if tags.isdisjoint(TAGS_SET):
            continue
        index = min(TAGS.index(tag) for tag in tags.intersection(TAGS_SET))
        if index < current_index:
            retval_url = url
            continue

    if retval_url is None and source_dist_details:
        retval_url = source_dist_details[0][1]

    return retval_url


class PackageIndex:
    """Encapsulates all the interactions that need to happen with a package index."""

    def __init__(
        self, index_url: str, *, cache_dir: Path, ui: UserVisibleProgress
    ) -> None:
        self.index_url = index_url
        self.metadata_cache_dir = cache_dir / "metadata"

        self._ui = ui
        self._session = create_session()
        self._client = hishel.CacheClient(
            storage=hishel.FileStorage(base_path=cache_dir / "http")
        )

    def __enter__(self) -> "PackageIndex":
        self._client.__enter__()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]] = None,
        exc_value: Optional[BaseException] = None,
        traceback: Optional[TracebackType] = None,
    ) -> None:
        self._client.__exit__(exc_type, exc_value, traceback)

    def fetch_all_dists_by_version(
        self, project_name: str, *, sdist_permitted: bool
    ) -> dict[str, list[ParsedDistributionDetail]]:
        """Return a mapping of version to metadata for the given package.

        This uses the metadata for the best-match wheel for the package.
        """
        package_url = create_project_url(self.index_url, project_name)
        response = self._client.get(
            package_url, headers={"Accept": "application/vnd.pypi.simple.v1+json"}
        )
        if response.status_code != 200:
            rich.print(
                f"Failed to fetch project details for {project_name}:",
                response.status_code,
                response.reason_phrase,
            )
            return {}
        data, content_type = response.text, response.headers["content-type"]
        project_details = parse_project_details(data, content_type, name=project_name)

        self._ui.set_total_dists_count(len(project_details["files"]))

        dist_details: defaultdict[str, list[ParsedDistributionDetail]] = defaultdict(
            list
        )
        for file_ in reversed(project_details["files"]):
            filename = file_["filename"]
            url = file_["url"]
            if filename.endswith(".whl"):
                parsed_wheel_name = parse_wheel_filename(filename)
                version_t = parsed_wheel_name[1]
                dist_details[str(version_t)].append((parsed_wheel_name, url))
            elif sdist_permitted and filename.endswith(".tar.gz"):
                if filename.startswith(project_name):
                    sdist_name = project_name
                    version = filename[len(project_name) + 1 : -len(".tar.gz")]
                else:
                    try:
                        sdist_name, version_t = parse_sdist_filename(filename)
                    except InvalidSdistFilename:
                        rich.print(
                            f"Failed to parse sdist name from file: {filename}"
                        )
                        continue
                    version = str(version_t)
                dist_details[version].append(((sdist_name, version), url))

            self._ui.on_file_grouped()

        return dist_details

    def fetch_best_candidate_metadata(
        self,
        project_name: str,
        version: str,
        dist_details: list[ParsedDistributionDetail],
    ) -> Optional[Metadata]:
        best_matching_dist = _pick_best_candidate_dist(dist_details)
        if best_matching_dist is None:
            return None

        package_url = create_project_url(self.index_url, project_name)
        dist_url = urljoin(package_url, best_matching_dist)

        parsed = self._use_cached_or_fetch_metadata(
            best_matching_dist, project_name, dist_url, version
        )
        try:
            # Look up eagerly to pre-compute & check that it is valid.
            parsed.requires_dist
            parsed.requires_python
        except Exception:
            return None
        return parsed

    def _use_cached_or_fetch_metadata(
        self,
        best_matching_dist: str,
        project_name: str,
        dist_url: str,
        version: str,
    ) -> Optional[Metadata]:
        cache_file = self.metadata_cache_dir / project_name / f"{version}-METADATA"
        if cache_file.exists():
            metadata_text = cache_file.read_text()
            if metadata_text.startswith("{"):
                return Metadata.from_raw(json.loads(metadata_text), validate=False)
            return Metadata.from_email(metadata_text, validate=False)

        cache_file.parent.mkdir(parents=True, exist_ok=True)
        if best_matching_dist.endswith(".whl"):
            return self._fetch_metadata_for_wheel(
                project_name,
                url=dist_url,
                store_at=cache_file,
            )
        return self._fetch_metadata_for_sdist_with_pip(
            dist_url,
            store_at=cache_file,
        )

    def _fetch_metadata_for_wheel(
        self, project_name: str, *, url: str, store_at: Path
    ) -> Optional[Metadata]:
        metadata = metadata_from_wheel_url(project_name, url, self._session)
        if metadata is None:
            return None

        try:
            parsed = Metadata.from_email(metadata, validate=False)
        except Exception:
            return None

        store_at.write_text(metadata)

        return parsed

    def _fetch_metadata_for_sdist_with_pip(
        self, url: str, *, store_at: Path
    ) -> Optional[Metadata]:
        failure_marker = store_at.with_name(store_at.name + ".fails")
        if failure_marker.exists():
            raise SdistFailure()

        with tempfile.NamedTemporaryFile(mode="w+t") as f:
            args = [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--dry-run",
                "--report",
                f.name,
                "--no-deps",
                "--ignore-installed",
                url,
            ]
            filename = url.rpartition("/")[2]
            rich.get_console().rule(f"pip: fetch metadata for {filename}")
            try:
                subprocess.run(
                    args,
                    check=True,
                    capture_output=True,
                    env={**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1"},
                )
            except subprocess.CalledProcessError as e:
                print(e.stdout.decode())
                print(e.stderr.decode())
                rich.get_console().rule("Error above", style="red")
                failure_marker.touch()
                raise SdistFailure()

            report = json.loads(f.read())
            metadata_json = report["install"][0]["metadata"]

        try:
            parsed = Metadata.from_raw(metadata_json, validate=False)
        except Exception:
            return None

        store_at.write_text(json.dumps(metadata_json))

        return parsed


def determine_extra_from_marker(marker: Optional[Marker]) -> str:
    """Return the extra that the given marker applies to."""
    if marker is None:
        return ""

    def _extract_extras(expression: Union[MarkerList, MarkerAtom, str]) -> Iterable[str]:
        for item in expression:
            if isinstance(item, list):
                yield from _extract_extras(item)
            elif isinstance(item, tuple):
                lhs, _, rhs = item
                if isinstance(lhs, Variable) and lhs.value == "extra":
                    yield canonicalize_name(rhs.value)
                elif isinstance(rhs, Variable) and rhs.value == "extra":
                    yield canonicalize_name(lhs.value)

    extras = list(_extract_extras(marker._markers))
    if len(extras) == 1:
        return extras[0]
    return ""


def fetch_one_package(
    project_name: str,
    *,
    index: PackageIndex,
    ui: UserVisibleProgress,
    sdist_permitted: bool,
) -> dict[str, DistributionInfo]:
    """Fetch the version information and metadata for one package."""
    package_entry = {}

    # Fetch all distribution URLs for the package.
    dists_by_version = index.fetch_all_dists_by_version(
        project_name, sdist_permitted=sdist_permitted
    )
    ui.set_total_version_count(len(dists_by_version))

    # Fetch the metadata of the "best" dist for each version.
    version_with_metadata: dict[str, Metadata] = {}
    for version, dist_details in dists_by_version.items():
        try:
            metadata = index.fetch_best_candidate_metadata(
                project_name=project_name,
                version=version,
                dist_details=dist_details,
            )
        except SdistFailure:
            rich.print(f"Skipping {project_name}=={version}: sdist causes error")
            metadata = None
        ui.on_metadata_fetched()
        if metadata is None:
            continue
        version_with_metadata[version] = metadata
    ui.set_total_metadata_fetched_count(len(version_with_metadata))

    # Process all the metadata
    for version, metadata in version_with_metadata.items():
        depends_by_extra: dict[str, list[str]] = {}
        for dep in metadata.requires_dist:
            dep_to_append = str(dep)
            extra = determine_extra_from_marker(dep.marker)
            if dep.marker:
                dep_to_append, _, _ = dep_to_append.partition(";")

            if extra not in depends_by_extra:
                depends_by_extra[extra] = []
            depends_by_extra[extra].append(dep_to_append)

        package_entry[version] = DistributionInfo(
            depends_by_extra=depends_by_extra,
            requires_python=None
            if not str(metadata.requires_python)
            else str(metadata.requires_python),
        )
        ui.on_dependencies_processed()

    return package_entry


def process_all_packages(
    input_packages: list[Requirement],
    index: PackageIndex,
    allow_sdists_for: set[str],
    ui: UserVisibleProgress,
) -> Scenario:
    """Explore and return the dependency graph of the given packages.

    This will explore the dependency graph of the given packages, and return a
    Scenario object containing the information about the packages' metadata.
    """
    scenario = Scenario(
        input=ScenarioInput(
            requirements=[str(r) for r in input_packages],
            timestamp=datetime.datetime.now(),
            allow_sdists_for=allow_sdists_for,
            environment=EnvironmentDetails(
                markers=default_environment(),
                tags=list(map(str, TAGS)),
            ),
        ),
        packages={},
    )

    to_process: deque[tuple[NormalizedName, frozenset]] = deque()
    seen = defaultdict(set)  # name: set of extras already explored

    def _add_req(requirement: Requirement) -> None:
        name = canonicalize_name(requirement.name)
        extras = set(map(canonicalize_name, requirement.extras)) | {""}
        unseen_extras = frozenset(extras - seen[name])
        if unseen_extras:
            to_process.append((name, unseen_extras))
            seen[name] |= unseen_extras

    for req in input_packages:
        _add_req(req)

    done = 0
    while to_process:
        ui.set_overall_count(len(to_process) + done)

        project_name, extras_to_explore = to_process.popleft()
        ui.on_start_package(project_name)

        if project_name not in scenario.packages:
            scenario.packages[project_name] = fetch_one_package(
                project_name,
                index=index,
                ui=ui,
                sdist_permitted=project_name in allow_sdists_for,
            )

        if extras_to_explore:
            for dist_info in scenario.packages[project_name].values():
                for extra in extras_to_explore:
                    seen[project_name].add(extra)
                    for r in dist_info.depends_by_extra.get(extra, []):
                        _add_req(Requirement(r))

        ui.on_package_finished()
        done += 1

    return scenario


def _load_allow_sdists_file(path: Optional[Path]) -> set[str]:
    if path is None:
        return set()
    if not path.exists():
        return set()

    data = path.read_text()
    cleaned_lines = map(lambda line: line.partition("#")[0].strip(), data.splitlines())
    cleaned_lines_with_details = list(
        filter(lambda lineno_line: lineno_line[1], enumerate(cleaned_lines))
    )
    bad_package_names = [
        (line_index + 1, package)
        for line_index, package in cleaned_lines_with_details
        if canonicalize_name(package) != package
    ]
    if bad_package_names:
        rich.print(
            "[red][bold]ERROR[/bold] Found non-canonical names in allow-sdists file.",
            *(
                f"Line {line_num:<3}: {package}"
                for line_num, package in bad_package_names
            ),
            sep="\n",
        )

    return set(map(lambda lineno_line: lineno_line[1], cleaned_lines_with_details))


def main() -> None:
    index_url = determine_configured_index()
    rich.print("Using index URL:", index_url)

    parser = argparse.ArgumentParser()
    parser.add_argument("packages", nargs="+", type=Requirement)
    parser.add_argument("--sdists-file", dest="sdists_file", type=Path, default=None)
    args = parser.parse_args()

    input_packages = args.packages
    allow_sdists_for = _load_allow_sdists_file(args.sdists_file)
    rich.print(f"Allowing sdists for {len(allow_sdists_for)} packages.")

    ui = UserVisibleProgress()
    index = PackageIndex(index_url, cache_dir=Path("./cache.ignore"), ui=ui)
    with index, ui:
        scenario = process_all_packages(
            input_packages=input_packages,
            allow_sdists_for=allow_sdists_for,
            index=index,
            ui=ui,
        )
        ui.live.refresh()

    rich.print("All done -- dumping the gathered information.")

    # Figure out where to write things.
    basename = "-".join(r.name for r in input_packages)
    n = 0
    while (dest_json := SCENARIOS_DIR / f"{basename}-{n}.ignore.json").exists():
        n += 1

    # Dump the information.
    dest_json.parent.mkdir(parents=True, exist_ok=True)
    dest_json.write_text(scenario.model_dump_json(exclude_defaults=True, indent=2))
    rich.print("Wrote to:", dest_json)

    for issue in scenario.check_for_issues():
        rich.print(f"[red][bold]FYI:[/bold] {escape(issue)}")


if __name__ == "__main__":
    main()
