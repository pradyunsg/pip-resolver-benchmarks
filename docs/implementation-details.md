# Implementation details

## Overview

This repository is "focused" around `scenarios/*.json` files. These files describe a set of requirements and the dependency graph for the dependency resolver.

There are 3 main scripts for handling these files:

- `src/fetch-info.py`: traverse the package index, and generate a `scenarios/*.json` file by fetching/generating metadata on all the files.
- `src/create-wheels.py`: create wheels for all the packages in a `scenarios/*.json` file, in a local directory that serves as a package index for pip.
- `src/run-benchmark.py`: run a "benchmark" for a `scenarios/*.json` file, using the pip installed in the environment.

Note that you don't need to run these scripts directly, there are nox sessions that wrap them and provide a higher level interface (documented in [the workflow doc](workflow.md)).

## `scenarios/*.json` files

The schema for these files is defined in `src/common/model.py`.

These contain the information about:

- The "input"

  - root requirements used to generate the graph.
  - timestamp for the start of graph generation.
  - what packages were permitted to use sdists during the graph generation.
  - details on the platform for where this was generated.

- The "packages"

  - the collected metadata for each package + version, with environment markers filtered out.
