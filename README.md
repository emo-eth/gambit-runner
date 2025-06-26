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

## Requirements

-   Python 3.8+
-   [psutil](https://pypi.org/project/psutil/)

## Usage

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

## Example

```sh
gambit_runner run --test-cmd 'forge test' --gambit-dir ./gambit_out --project-root . --output results.json --timeout 5 --jobs 4 --build-cmd 'forge build' --debug

gambit_runner report --json results.json
```

## Output

-   The runner writes undetected mutations (mutation test failures) to the specified output JSON file.
-   The `report` command pretty-prints the contents of this file for easy review.

## License

MIT
