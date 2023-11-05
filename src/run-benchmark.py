"""Run the resolver benchmark and provide the results.
"""

import argparse
import os
import statistics
import subprocess
import sys
import time

from common.model import WHEELHOUSE_DIR, list_scenarios, load_scenario


def run_scenario(name: str, *, warmups: int, runs: int) -> None:
    scenario = load_scenario(name)

    def cmd_for(stage: str, n: int) -> list[str]:
        return [
            sys.executable,
            "-m",
            "pip",
            "install",
            # Where to discover things
            "--index-url",
            (WHEELHOUSE_DIR / name).as_uri(),
            # Avoid non-resolve stuff
            "--disable-pip-version-check",
            "--dry-run",
            # Isolate from environment
            "--ignore-installed",
            # Dump debugging information in a logfile, and be quiet otherwise.
            "--log",
            os.fspath(WHEELHOUSE_DIR / f"{name}[{stage}-{n}].log"),
            "--quiet",
            # What to resolve for
            *scenario.input.requirements,
        ]

    warmup_times = []
    for n in range(1, warmups + 1):
        cmd = cmd_for("warmup", n)

        print(f" start warmup {n} of {warmups} ".center(80, "="))
        print(cmd)

        start = time.perf_counter()
        result = subprocess.run(cmd)
        end = time.perf_counter()

        print(f" finish warmup {n} ".center(80, "="))
        warmup_times.append(end - start)
        if result.returncode != 0:
            sys.exit(result.returncode)

    print(
        "Mean +- std dev:",
        statistics.mean(warmup_times),
        "+-",
        statistics.pstdev(warmup_times),
    )
    print("Warmup times", warmup_times)

    main_loop_times = []
    for n in range(1, runs + 1):
        cmd = cmd_for("run", n)

        print(f" start run {n} of {runs} ".center(80, "="))

        start = time.perf_counter()
        result = subprocess.run(cmd)
        end = time.perf_counter()

        print(f" finish run {n} ".center(80, "="))
        main_loop_times.append(end - start)
        if result.returncode != 0:
            sys.exit(result.returncode)

    print(
        "Mean +- std dev:",
        statistics.mean(main_loop_times),
        "+-",
        statistics.pstdev(main_loop_times),
    )
    print("Main run times", main_loop_times)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "scenario",
        choices=list_scenarios(),
        help="Name of the scenario to benchmark.",
    )
    parser.add_argument(
        "--warmups",
        type=int,
        default=2,
        help="Number of warmup runs to perform.",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=5,
        help="Number of benchmark runs to perform.",
    )
    args = parser.parse_args()

    run_scenario(args.scenario, warmups=args.warmups, runs=args.runs)


if __name__ == "__main__":
    main()
