"""Core data model.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Annotated, Optional

import rich
from packaging.utils import canonicalize_version, is_normalized_name
from packaging.version import VERSION_PATTERN
from pydantic import AfterValidator, BaseModel, ValidationError, field_validator

_repo_root = Path(__file__).parent.parent.parent
SCENARIOS_DIR = _repo_root / "scenarios"
WHEELHOUSE_DIR = _repo_root / "wheelhouse.ignore"


class DistributionInfo(BaseModel):
    depends_by_extra: dict[str, list[str]]
    requires_python: Optional[str] = None

    @field_validator("depends_by_extra", mode="after")
    @classmethod
    def ensure_no_empty_extras(
        cls, v: dict[Optional[str], list[str]]
    ) -> dict[Optional[str], list[str]]:
        for depends in v.values():
            for dep in depends:
                assert ";" not in dep
        return v

    def as_METADATA(self, name: str, version: str) -> str:
        parts = [
            "Metadata-Version: 2.1",
            f"Name: {name}",
            f"Version: {version}",
        ]
        for extra, depends in self.depends_by_extra.items():
            if extra == "":
                for dep in depends:
                    parts.append(f"Requires-Dist: {dep}")
            else:
                parts.append(f"Provides-Extra: {extra}")
                for dep in depends:
                    parts.append(f"Requires-Dist: {dep} ; extra == '{extra}'")

        if self.requires_python:
            parts.append(f"Requires-Python: {self.requires_python}")

        return "\n".join(parts)


def ensure_normalized_name(name: str) -> str:
    if not is_normalized_name(name):
        raise ValueError("not a normalised package name")
    return name


def ensure_normalized_version(version: str) -> str:
    if re.match(VERSION_PATTERN, version, re.VERBOSE | re.IGNORECASE) is None:
        raise ValueError("not a valid PEP 440 version")
    return version


PackageName = Annotated[str, AfterValidator(ensure_normalized_name)]
VersionString = Annotated[str, AfterValidator(ensure_normalized_version)]


class EnvironmentDetails(BaseModel):
    markers: dict[str, str]
    tags: list[str]


class ScenarioInput(BaseModel):
    requirements: list[str]
    timestamp: datetime.datetime
    allow_sdists_for: list[str]
    environment: EnvironmentDetails


class Scenario(BaseModel):
    input: ScenarioInput
    packages: dict[PackageName, dict[VersionString, DistributionInfo]]

    @field_validator("packages", mode="after")
    @classmethod
    def ensure_unique_versions_when_canonicalized(
        cls, v: dict[PackageName, dict[VersionString, DistributionInfo]]
    ) -> dict[PackageName, dict[VersionString, DistributionInfo]]:
        for name, versions in v.items():
            seen = set()
            for version in versions:
                canonicalized = canonicalize_version(version)
                if canonicalized in seen:
                    raise ValueError(
                        f"{name} has multiple versions with same "
                        f"canonicalized value: {canonicalized}"
                    )
                seen.add(canonicalized)
        return v

    def check_for_issues(self) -> list[str]:
        issues = []
        packages_with_no_version = []
        for name, grouped_by_version in self.packages.items():
            if not grouped_by_version:
                packages_with_no_version.append(name)

        if packages_with_no_version:
            count = len(packages_with_no_version)
            names = "\n  ".join(packages_with_no_version)
            issues.append(f"Found {count} packages with no versions...\n  {names}")
        return issues


def list_scenarios() -> list[str]:
    return [p.stem for p in SCENARIOS_DIR.glob("*.json")]


def load_scenario(name: str) -> Scenario:
    test_case_file = SCENARIOS_DIR / f"{name}.json"
    data = test_case_file.read_text()

    try:
        scenario = Scenario.model_validate_json(data, strict=True)
    except ValidationError as e:
        rich.get_console().print("[bold]ERROR:[/]", e, style="red")
        raise

    return scenario
