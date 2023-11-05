# Workflow

## Prerequisites

- A functional Python 3.10+ installation, with `python3.10 -m venv` working.
- [`nox`](https://nox.thea.codes/en/stable/) installed.

## Running a scenario

1. Pick a scenario name from the `scenarios/{scenario_name}.json` files.
1. Pick the requirement for pip to use.
1. Run the `benchmark` session with the scenario name and the pip requirement:

   ```sh
   nox -s benchmark -- {scenario_name} --pip {pip}
   ```

> [!NOTE]
> The ordering of `--pip {pip}` and `{scenario_name}` is not important, but `--pip` takes an argument.

> [!NOTE]
> Here are some examples of what you can do with the pip "requirement" using `scenarios/pyrax_198.json` as the scenario:
>
> - Run with the latest released version of pip:
>
>   ```sh
>   nox -s benchmark -- pyrax_198 --pip pip
>   ```
>
> - Run with a specific version of pip:
>
>   ```sh
>   nox -s benchmark -- pyrax_198 --pip pip==23.0.1
>   ```
>
> - Run with a local clone of pip:
>
>   ```sh
>   nox -s benchmark -- pyrax_198 --pip ~/Developer/github/pip
>   ```
>
> - Run with the latest `main` branch of pip:
>
>   ```sh
>   nox -s benchmark -- pyrax_198 --pip https://github.com/pypa/pip/archive/refs/heads/main.zip
>   ```
>
> - Run with a branch from a fork of pip:
>
>   ```sh
>   nox -s benchmark -- pyrax_198 --pip https://github.com/pradyunsg/pip/archive/refs/heads/awesome-new-resolver.zip
>   ```

## Creating a new scenario

The command to generate a scenario file from a set of requirements is:

```sh
nox -s fetch -- {requirements}
```

> [!NOTE]
> For example, to generate a scenario file for `pyrax==1.9.8`:
>
> ```sh
> nox -s fetch -- pyrax==1.9.8
> ```

This will print a `Wrote to: [path]` message. That message will end with `scenarios/{identifier}-{number}.ignore.json`. This filename is gitignored by default.

Once the scenario is generated and works (run the benchmark to make sure it behaves as expected), rename the relevant scenario file to a human-friendly name and delete any remaining `.ignore.json` scenario files.

### Iteration when source distributions are involved

When source distributions are involved, the scenario creation process becomes an iterative one (at the moment). The reasoning for this is explained in the [Opt-in source distribution support](#opt-in-source-distribution-support) section below.

1. Create a scenario file for the requirements you want to use.

   ```sh
   nox -s fetch -- {requirements}
   ```

   This might print a message like:

   ```text
   FYI: Found 12 packages with no versions...
     rackspace-novaclient
     [trimmed for brevity]
   ```

   These are projects with no compatible wheels for that package, on that platform.

1. Run the scenario generated to see if it works as expected.

   ```sh
   nox -s benchmark -- --pip pip {scenario_name}
   ```

1. If it fails due to something like...

   ```text
   ERROR: Could not find a version that satisfies the requirement rackspace-novaclient (from pyrax) (from versions: none)
   ```

   _and_ that package needs to use source distributions, _and_ you deem them safe-enough to build locally, _then_ you can add the (normalised) package name to the `allow-sdists.ignore` file in the repository root (you'll need to create the file yourself, if it doesn't exist).

> [!NOTE]
> If you really want to iterate quickly and don't care about which sdists you're running (eg: you have a sandbox), copy-paste all the packages with no versions over.

## Additional context

### Opt-in source distribution support

By default, the fetch step will ignore source distributions.

To allow a package to use source distributions during the dependency graph generation, you need to add the package name to the `allow-sdists.ignore` file (in the repository root, you'll need to create it yourself).

This mechanism exists for 2 reasons:

1. Source distributions can execute arbitrary code. This is a bigger security risk that a regular `pip install` since this is traversing the _entire_ dependency graph across _all_ versions for _all_ relevant packages.

   Plus, I needed to `sudo ...` to profile this script as well as pip, so definitely don't want someone-else's-code executing without me having a chance to look at whether I trust what it could be.

2. Source distributions take significantly longer to generate metadata from than wheel distributions. This basically makes the fetch step really slow, which is suboptimal when iterating on this script itself.

Note that the metadata generated _and_ metadata generation failures will be cached, so subsequent runs should be able to reuse this information.

### Various files and directories

#### `scenarios/*.json` files

These files are the main "data" in this repository, serving as shareable inputs to the tooling that can be used to reproduce dependency graphs for failure modes.

#### `src/` directory

The actual "logic" of the tooling is contained in this directory. You should never need to run a script directly from this directory, but rather use the nox sessions at the top level.

#### `allow-sdists.ignore` file

This file is used to permit certain packages to use source distributions to be built from them.

#### `cache.ignore` directory

This contains a cache of the HTTP requests as well as the metadata of packages that have already been looked up. This enables the `fetch` step to run faster by not having to make HTTP requests for packages that have already been looked up.

This is useful when gathering information for a new scenario, since certain scenarios require source distributions to be fully reproducible and what packages are allowed to build source distributions is restricted in the fetch step. This is also useful when developing this tooling itself to see "wait, what was the input".

#### `wheelhouse.ignore` directory

This is where the generated package indexes composed of only wheels are stored. This is what is will be passed into pip.
