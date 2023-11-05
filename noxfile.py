import argparse

import nox


@nox.session(python="3.11")  # specify a Python version, so that it can be overridden.
def compile(session: nox.Session) -> None:
    session.install("pip-tools")
    session.run(
        "pip-compile",
        "requirements.in",
        "--no-emit-index-url",
        "-o",
        f"requirements-{session.python}.txt",
    )


@nox.session(python="3.11")  # specify a Python version, so that it can be overridden.
def fetch(session: nox.Session) -> None:
    session.install("-r", f"requirements-{session.python}.txt")
    session.run(
        "python",
        "src/fetch-info.py",
        "--sdists-file",
        "./allow-sdists.ignore",
        *session.posargs,
    )


@nox.session(python="3.11")  # specify a Python version, so that it can be overridden.
def benchmark(session: nox.Session) -> None:
    parser = argparse.ArgumentParser(
        prog="nox -s benchmark",
        usage="%(prog)s -- SCENARIO --pip PIP",
    )
    parser.add_argument("scenario")
    parser.add_argument("--pip", required=True)
    args = parser.parse_args(session.posargs)

    session.install("-r", f"requirements-{session.python}.txt")

    session.run("python", "src/create-wheels.py", args.scenario)
    session.install("--upgrade", args.pip, silent=False)
    session.run("python", "src/run-benchmark.py", args.scenario)
