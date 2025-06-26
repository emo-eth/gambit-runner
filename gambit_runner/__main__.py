# /// script
# dependencies = [
#   "psutil",
# ]
# ///

#!/usr/bin/env python3
"""
# gambit_runner_parallel.py
#
# Parallelized Gambit mutation test runner and report pretty-printer.
#
# Usage:
#   uv run gambit_runner_parallel.py run -- --test-cmd 'forge test ...' [--gambit-dir ./gambit_out] [--project-root .] [--output gambit_test_results.json] [--timeout 3.0] [--jobs N] [--build-cmd 'forge build'] [--debug]
#   uv run gambit_runner_parallel.py report [--json gambit_test_results.json]
#
# Requires: psutil (install with 'uv pip install psutil')
#
# No external dependencies (stdlib only, except for psutil for robust shutdown).
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import List, Dict, Any, Optional, Tuple
import multiprocessing
import threading
import time
import psutil

# Use 'spawn' to avoid fork-related issues with process pools
multiprocessing.set_start_method('spawn', force=True)

PROGRESS_BAR_WIDTH = 30
LOG_LOCK = multiprocessing.Lock()

BUILD_TIMEOUT = 60  # seconds


def log(msg: str, debug: bool):
    if debug:
        with LOG_LOCK:
            print(msg, file=sys.stderr, flush=True)


def log_output(idx: int, total: int, mutation_name: str, stdout: bytes, stderr: bytes, debug: bool):
    if debug:
        with LOG_LOCK:
            print(f"\n[OUTPUT] [{idx+1}/{total}] Mutation: {mutation_name}", file=sys.stderr)
            if stdout:
                print(f"--- STDOUT ---\n{stdout.decode(errors='replace')}", file=sys.stderr)
            if stderr:
                print(f"--- STDERR ---\n{stderr.decode(errors='replace')}", file=sys.stderr)
            print(f"[END OUTPUT] [{idx+1}/{total}] Mutation: {mutation_name}\n", file=sys.stderr)


def log_build_output(stdout: bytes, stderr: bytes, debug: bool):
    if debug:
        with LOG_LOCK:
            print(f"\n[BUILD OUTPUT]", file=sys.stderr)
            if stdout:
                print(f"--- STDOUT ---\n{stdout.decode(errors='replace')}", file=sys.stderr)
            if stderr:
                print(f"--- STDERR ---\n{stderr.decode(errors='replace')}", file=sys.stderr)
            print(f"[END BUILD OUTPUT]\n", file=sys.stderr)


def pretty_print_mutations(mutations: List[Dict[str, Any]]):
    print("\n=== Mutations to be tested ===\n")
    for idx, mutation in enumerate(mutations, 1):
        print(f"Mutation {idx}/{len(mutations)}:")
        desc = mutation.get('description', '')
        if desc:
            print(f"  Description: {desc}")
        print(f"  Name: {mutation.get('name', '')}")
        diff = mutation.get('diff', '')
        diff_lines = diff.splitlines()
        if diff_lines:
            print("  Diff (truncated):")
            for line in diff_lines:
                print(f"    {line}")
        print("  " + "-" * 40)
    print(f"Total mutations: {len(mutations)}\n")


def parse_args():
    parser = argparse.ArgumentParser(description="Parallel Gambit mutation test runner and report pretty-printer.")
    subparsers = parser.add_subparsers(dest='subcommand', required=True)

    # Run subcommand
    run_parser = subparsers.add_parser('run', help='Run mutation tests (default)')
    run_parser.add_argument('--test-cmd', required=True, help="Test command to run (e.g., 'forge test ...')")
    run_parser.add_argument('--gambit-dir', default='./gambit_out', help="Directory containing gambit_results.json and mutant files (default: ./gambit_out)")
    run_parser.add_argument('--project-root', default='.', help="Root directory of the project source code (default: .)")
    run_parser.add_argument('--output', default='gambit_test_results.json', help="Output file for mutation test failures (undetected mutations)")
    run_parser.add_argument('--timeout', type=float, default=3.0, help="Timeout in seconds for each test command (default: 3.0)")
    run_parser.add_argument('--jobs', type=int, default=multiprocessing.cpu_count(), help="Number of parallel jobs (default: logical CPU count)")
    run_parser.add_argument('--build-cmd', default='forge build', help="Build command to run before mutation testing (default: 'forge build')")
    run_parser.add_argument('--debug', action='store_true', help="Enable debug logging and show test command output.")

    # Report subcommand
    report_parser = subparsers.add_parser('report', help='Pretty-print a mutation test results JSON file')
    report_parser.add_argument('--json', default='gambit_test_results.json', help='JSON file to pretty-print (default: gambit_test_results.json)')

    return parser.parse_args()


def make_progress_bar(current: int, total: int, width: int = PROGRESS_BAR_WIDTH) -> str:
    filled = int(width * current / total) if total else 0
    return '[' + '#' * filled + '-' * (width - filled) + f'] {current}/{total}'


def print_progress(current: int, total: int, uncaught: int, in_place: bool):
    bar = make_progress_bar(current, total)
    line = f"{bar}  Uncaught mutations: {uncaught}"
    if in_place and sys.stdout.isatty():
        sys.stdout.write('\r' + line + ' ' * 10)
        sys.stdout.flush()
    else:
        print(line)


def kill_child_processes():
    parent = psutil.Process(os.getpid())
    for child in parent.children(recursive=True):
        try:
            child.kill()
        except Exception:
            pass


def run_mutation_test(
    mutation: Dict[str, Any],
    gambit_dir: str,
    project_root: str,
    test_cmd: str,
    timeout: float,
    idx: int,
    total: int,
    debug: bool
) -> Optional[Tuple[Optional[Dict[str, Any]], int]]:
    """
    Returns (mutation dict if NOT detected, else None, mutation index).
    Logs status to stderr and prints test output if debug is True.
    """
    mutant_path = os.path.join(gambit_dir, mutation['name'])
    if not os.path.isfile(mutant_path):
        log(f"[ERROR] [{idx+1}/{total}] Mutant file missing: {mutant_path}. Skipping.", debug)
        return None, idx
    original_rel_path = mutation['original']
    try:
        with tempfile.TemporaryDirectory() as tempdir:
            proj_dir = os.path.join(tempdir, 'proj')
            shutil.copytree(project_root, proj_dir, dirs_exist_ok=True)
            mutated_file_path = os.path.join(proj_dir, original_rel_path)
            if not os.path.isfile(mutated_file_path):
                log(f"[ERROR] [{idx+1}/{total}] Original file not found in tempdir: {mutated_file_path}. Skipping.", debug)
                return None, idx
            shutil.copy2(mutant_path, mutated_file_path)
            log(f"[INFO] [{idx+1}/{total}] Starting mutation: {mutation.get('name', '')} in {proj_dir}", debug)
            log(f"[INFO] [{idx+1}/{total}] Running: {test_cmd}", debug)
            start_time = time.time()
            try:
                result = subprocess.run(
                    test_cmd,
                    shell=True,
                    cwd=proj_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=timeout
                )
                elapsed = time.time() - start_time
                log_output(idx, total, mutation.get('name', ''), result.stdout, result.stderr, debug)
                if result.returncode == 0:
                    log(f"[RESULT] [{idx+1}/{total}] UNCAUGHT (test suite PASSED) for mutation: {mutation.get('name', '')} (elapsed: {elapsed:.2f}s)", debug)
                    return mutation, idx  # Uncaught mutation
                else:
                    log(f"[RESULT] [{idx+1}/{total}] CAUGHT (test suite FAILED) for mutation: {mutation.get('name', '')} (elapsed: {elapsed:.2f}s)", debug)
            except subprocess.TimeoutExpired as e:
                log(f"[TIMEOUT] [{idx+1}/{total}] Test command timed out after {timeout} seconds for mutation {mutation.get('name', '')}.", debug)
                log_output(idx, total, mutation.get('name', ''), e.stdout or b'', e.stderr or b'', debug)
            except Exception as e:
                log(f"[ERROR] [{idx+1}/{total}] Test command failed for mutation {mutation.get('name', '')}: {e}.", debug)
    except Exception as e:
        log(f"[ERROR] [{idx+1}/{total}] Exception in mutation {mutation.get('name', '')}: {e}.", debug)
    return None, idx


def run_main(args):
    gambit_input_path = os.path.join(args.gambit_dir, 'gambit_results.json')

    # Build step before running mutations
    log(f"[INFO] Running build command: {args.build_cmd}", args.debug)
    try:
        build_result = subprocess.run(
            args.build_cmd,
            shell=True,
            cwd=args.project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=BUILD_TIMEOUT
        )
        log_build_output(build_result.stdout, build_result.stderr, args.debug)
        if build_result.returncode != 0:
            print("[ERROR] Build failed. Aborting.", file=sys.stderr)
            sys.exit(1)
    except subprocess.TimeoutExpired as e:
        print(f"[ERROR] Build command timed out after {BUILD_TIMEOUT} seconds. Aborting.", file=sys.stderr)
        log_build_output(e.stdout or b'', e.stderr or b'', args.debug)
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Exception during build: {e}. Aborting.", file=sys.stderr)
        sys.exit(1)

    try:
        with open(gambit_input_path, 'r') as f:
            mutations = json.load(f)
    except Exception as e:
        log(f"[ERROR] Failed to read {gambit_input_path}: {e}", args.debug)
        sys.exit(1)
    if not mutations:
        log("[ERROR] No mutations found in input file.", args.debug)
        sys.exit(1)

    total = len(mutations)
    uncaught_mutations: List[Dict[str, Any]] = []
    completed = 0
    in_place = sys.stdout.isatty()

    start_time = time.time()

    print_progress(0, total, 0, in_place)

    try:
        with ProcessPoolExecutor(max_workers=args.jobs) as executor:
            futures = [
                executor.submit(
                    run_mutation_test,
                    mutation,
                    args.gambit_dir,
                    args.project_root,
                    args.test_cmd,
                    args.timeout,
                    idx,
                    total,
                    args.debug
                ) for idx, mutation in enumerate(mutations)
            ]
            for future in as_completed(futures):
                try:
                    result, idx = future.result()
                    completed += 1
                    if result:
                        uncaught_mutations.append(result)
                    print_progress(completed, total, len(uncaught_mutations), in_place)
                except KeyboardInterrupt:
                    print("\n[INFO] KeyboardInterrupt received. Shutting down workers...", file=sys.stderr)
                    executor.shutdown(wait=False, cancel_futures=True)
                    kill_child_processes()
                    print("[INFO] Exiting due to user interrupt.", file=sys.stderr)
                    os._exit(130)
    except KeyboardInterrupt:
        print("\n[INFO] KeyboardInterrupt received. Exiting.", file=sys.stderr)
        kill_child_processes()
        os._exit(130)

    elapsed = time.time() - start_time

    if in_place and sys.stdout.isatty():
        sys.stdout.write('\n')
    print(f"Done. {len(uncaught_mutations)} out of {total} mutations were NOT detected by the test suite (mutation test failures).")
    pretty_print_mutations(uncaught_mutations)
    print(f"Results written to {args.output}")
    print(f"Elapsed time: {elapsed:.2f} seconds")
    try:
        with open(args.output, 'w') as f:
            json.dump(uncaught_mutations, f, indent=2)
        log(f"[INFO] Wrote mutation test failures to {args.output}", args.debug)
    except Exception as e:
        log(f"[ERROR] Failed to write output file: {e}", args.debug)


def report_main(args):
    json_path = args.json
    try:
        with open(json_path, 'r') as f:
            mutations = json.load(f)
    except Exception as e:
        print(f"[ERROR] Failed to read {json_path}: {e}", file=sys.stderr)
        sys.exit(1)
    if not mutations:
        print(f"[ERROR] No mutations found in {json_path}.", file=sys.stderr)
        sys.exit(1)
    pretty_print_mutations(mutations)


def main() -> None:
    args = parse_args()
    if args.subcommand == 'run':
        run_main(args)
    elif args.subcommand == 'report':
        report_main(args)
    else:
        raise ValueError(f"Unknown subcommand: {args.subcommand}")


if __name__ == "__main__":
    main() 