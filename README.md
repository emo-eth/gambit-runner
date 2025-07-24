# Gambit Runner

Parallelized Gambit mutation test runner and report pretty-printer.

## Overview

`gambit_runner` is a tool for running mutation tests in parallel on a codebase using [Gambit](https://github.com/Certora/gambit)-generated mutants. It supports running tests for each mutant, collecting undetected mutations, and pretty-printing the results. The tool is designed for speed and robustness, leveraging Python's process pools and the `psutil` library for process management.

## Installation

Install the tool locally using [uv](https://github.com/astral-sh/uv):

```sh
uv tool install .
```

This will make the `gambit_runner` command available in your shell.

## Getting started

First, generate mutants with [Gambit](https://github.com/Certora/gambit):

```sh
gambit mutate <args>
```

Then run the tool:

```sh
gambit_runner run --test-cmd 'forge test'
```

## Features

-   Parallel execution of mutation tests
-   Pre-build step to ensure mutation tests only have to compile incremental changes
-   Timeout and debug logging for each test
-   Pretty-printing of undetected mutation results
-   Simple JSON output for further analysis
-   **Mutant generation** from Solidity source and Foundry remappings (see `generate`/`full`)
-   **One-step workflow**: generate mutants and run mutation testing in a single command (see `full`)
-   **Selective rerun**: rerun only previously undetected mutations with `--uncaught`

## Requirements

-   Python 3.8+
-   [psutil](https://pypi.org/project/psutil/)

## Usage

### Subcommands Overview

-   `run`: Run mutation tests on existing mutants
-   `report`: Pretty-print a mutation test results JSON file
-   `generate`: Generate a `gambit.json` file from Solidity sources and Foundry remappings, then run `gambit mutate`
-   `full`: Generate mutants and run the full mutation testing suite in one step (combines `generate` and `run`)

### 1. Run Mutation Tests

```sh
gambit_runner run --test-cmd 'forge test ...' [--gambit-dir ./gambit_out] [--project-root .] [--output gambit_test_results.json] [--timeout 3.0] [--jobs N] [--build-cmd 'forge build'] [--debug]
```

**Arguments:**

-   `--test-cmd` (required): The test command to run for each mutant (e.g., `'forge test ...'`).
-   `--gambit-dir`: Directory containing `gambit_results.json` and mutant files (default: `./gambit_out`).
-   `--project-root`: Root directory of the project source code (default: `.`).
-   `--output`: Output file for undetected mutations (default: `gambit_test_results.json`).
-   `--timeout`: Timeout in seconds for each test command (default: `3.0`).
-   `--jobs`: Number of parallel jobs (default: logical CPU count).
-   `--build-cmd`: Build command to run before mutation testing (default: `'forge build'`).
-   `--debug`: Enable debug logging and show test command output.

### 2. Pretty-Print Mutation Test Results

```sh
gambit_runner report [--json gambit_test_results.json]
```

**Arguments:**

-   `--json`: JSON file to pretty-print (default: `gambit_test_results.json`).

### 3. Generate Mutants (`generate`)

Generate a `gambit.json` file from your Solidity sources and Foundry remappings, then run `gambit mutate` to produce mutants:

```sh
gambit_runner generate [input_dir] [--foundry-toml foundry.toml] [--output gambit.json] [--sourceroot .] [--ignore-paths test/ mocks/] [--use-existing existing_gambit.json] [-- <extra gambit mutate args>]
```

**Arguments:**

-   `input_dir` (optional): Directory to crawl for `.sol` files (e.g. `src/`) - not needed when using `--use-existing`
-   `--foundry-toml`: Path to `foundry.toml` (default: `foundry.toml`)
-   `--output`: Output `gambit.json` file (default: `gambit.json`)
-   `--sourceroot`: `sourceroot` value for each entry (default: `.`)
-   `--ignore-paths`: Paths to ignore when finding `.sol` files (e.g., `test/` `mocks/`)
-   `--use-existing`: Use existing `gambit.json` file instead of generating a new one
-   All arguments after `--` are passed directly to `gambit mutate`

### 4. Full One-Step Workflow (`full`)

Generate mutants and run the full mutation testing suite in a single command:

```sh
gambit_runner full [input_dir] --test-cmd 'forge test ...' [--foundry-toml foundry.toml] [--gambit-json gambit.json] [--sourceroot .] [--ignore-paths test/ mocks/] [--use-existing existing_gambit.json] [--gambit-dir ./gambit_out] [--project-root .] [--output gambit_test_results.json] [--timeout 3.0] [--jobs N] [--build-cmd 'forge build'] [--debug] [--uncaught] [-- <extra gambit mutate args>]
```

**Arguments:**

-   `input_dir` (optional): Directory to crawl for `.sol` files (e.g. `src/`) - not needed when using `--use-existing`
-   `--test-cmd` (required): The test command to run for each mutant (e.g., `'forge test ...'`)
-   `--foundry-toml`: Path to `foundry.toml` (default: `foundry.toml`)
-   `--gambit-json`: Output `gambit.json` file for mutant generation (default: `gambit.json`)
-   `--sourceroot`: `sourceroot` value for each entry (default: `.`)
-   `--ignore-paths`: Paths to ignore when finding `.sol` files (e.g., `test/` `mocks/`)
-   `--use-existing`: Use existing `gambit.json` file instead of generating a new one
-   `--gambit-dir`: Directory containing `gambit_results.json` and mutant files (default: `./gambit_out`)
-   `--project-root`: Root directory of the project source code (default: `.`)
-   `--output`: Output file for undetected mutations (default: `gambit_test_results.json`)
-   `--timeout`: Timeout in seconds for each test command (default: `3.0`)
-   `--jobs`: Number of parallel jobs (default: logical CPU count)
-   `--build-cmd`: Build command to run before mutation testing (default: `'forge build'`)
-   `--debug`: Enable debug logging and show test command output
-   `--uncaught`: Only run mutations that were uncaught in the previous run, as listed in the `--output` file
-   All arguments after `--` are passed directly to `gambit mutate`

**Example:**

```sh
gambit_runner full src/ --test-cmd 'forge test' --foundry-toml foundry.toml --gambit-json gambit.json --gambit-dir ./gambit_out --project-root . --output results.json --timeout 5 --jobs 4 --build-cmd 'forge build' --debug -- --mutate-all
```

## Example Workflows

**Generate mutants and run mutation tests in one step:**

```sh
gambit_runner full src/ --test-cmd 'forge test' --debug
```

**Full workflow with ignored paths:**

```sh
gambit_runner full src/ --test-cmd 'forge test' --ignore-paths test/ mocks/ --debug
```

**Full workflow using existing gambit.json:**

```sh
gambit_runner full --test-cmd 'forge test' --use-existing existing_gambit.json --debug
```

**Just generate mutants:**

```sh
gambit_runner generate src/ --foundry-toml foundry.toml --output my_gambit.json -- --mutate-all
```

**Generate mutants with ignored paths:**

```sh
gambit_runner generate src/ --ignore-paths test/ mocks/ -- --mutate-all
```

**Use existing gambit.json file:**

```sh
gambit_runner generate --use-existing existing_gambit.json -- --mutate-all
```

**Run mutation tests on existing mutants:**

```sh
gambit_runner run --test-cmd 'forge test' --gambit-dir ./gambit_out --project-root . --output results.json --timeout 5 --jobs 4 --build-cmd 'forge build' --debug
```

**Pretty-print results:**

```sh
gambit_runner report --json results.json
```

## Tips

-   Use a gambit-specific Foundry profile with optimized settings, e.g., `via_ir` disabled for faster test compilation
    -   Be sure to specify the same profile for the `build_cmd` otherwise it will have to re-build the tests from scratch
-   **Be sure to disable `dynamic_test_linking` in `foundry.toml` or inline when running mutation tests**

## Output

-   The runner writes undetected mutations (mutation test failures) to the specified output JSON file.
-   The `report` command pretty-prints the contents of this file for easy review.

## License

MIT
