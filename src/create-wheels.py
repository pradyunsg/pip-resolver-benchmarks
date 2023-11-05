"""Run the resolver benchmark and provide the results.

The results can be dumped to a JSON file or printed as a summary to stdout.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import rich

from common.creation import populate_with_scenario
from common.model import WHEELHOUSE_DIR, list_scenarios, load_scenario


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir-base",
        type=Path,
        default=WHEELHOUSE_DIR,
        help="Base directory to output the generated wheels.",
    )
    parser.add_argument(
        "scenario_names",
        nargs="+",
        metavar="scenario",
        choices=list_scenarios(),
        help="Name of the scenario to generate. If not specified, all scenarios are generated.",
    )
    args = parser.parse_args()

    scenarios = {name: load_scenario(name) for name in args.scenario_names}
    rich.print(f"Loaded {len(scenarios)} scenario{'s' if len(scenarios) != 1 else ''}.")

    if args.scenario_names:
        unknown = set(args.scenario_names) - set(scenarios.keys())
        if unknown:
            print(f"Unknown scenarios: {unknown}", file=sys.stderr)
            print("Available scenarios are", list(scenarios.keys()), file=sys.stderr)
            sys.exit(1)

        scenarios = {name: scenarios[name] for name in args.scenario_names}

    output_dir_base = args.output_dir_base

    for name, scenario in scenarios.items():
        rich.print(f"[blue]{name}[/]")
        output_dir = output_dir_base / name
        if output_dir.exists():
            rich.print(f"Removing existing directory: {output_dir}")
            shutil.rmtree(output_dir)

        populate_with_scenario(scenario, output_dir)


if __name__ == "__main__":
    main()
