# /// script
# dependencies = [
#   "psutil",
#   "tomli"
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
import time
import psutil
import tomli

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
    run_parser.add_argument('--uncaught', action='store_true', help="Only run mutations that were uncaught in the previous run, as listed in the --output file.")

    # Report subcommand
    report_parser = subparsers.add_parser('report', help='Pretty-print a mutation test results JSON file')
    report_parser.add_argument('--json', default='gambit_test_results.json', help='JSON file to pretty-print (default: gambit_test_results.json)')

    generate_parser = subparsers.add_parser('generate', help='Generate a gambit.json file from foundry.toml and .sol files, then run gambit mutate.')
    generate_parser.add_argument('input_dir', type=str, help='Directory to crawl for .sol files (e.g. src/)')
    generate_parser.add_argument('--foundry-toml', type=str, default='foundry.toml', help='Path to foundry.toml')
    generate_parser.add_argument('--output', type=str, default='gambit.json', help='Output gambit.json file')
    generate_parser.add_argument('--sourceroot', type=str, default='.', help='sourceroot value for each entry')
    generate_parser.add_argument('gambit_args', nargs=argparse.REMAINDER, help='Extra arguments to pass to gambit mutate (after --)')

    full_parser = subparsers.add_parser('full', help='Generate mutants and run the full mutation testing suite (combines generate and run)')
    full_parser.add_argument('input_dir', type=str, help='Directory to crawl for .sol files (e.g. src/)')
    full_parser.add_argument('--foundry-toml', type=str, default='foundry.toml', help='Path to foundry.toml')
    full_parser.add_argument('--gambit-json', type=str, default='gambit.json', help='Output gambit.json file (for mutant generation)')
    full_parser.add_argument('--sourceroot', type=str, default='.', help='sourceroot value for each entry')
    full_parser.add_argument('--gambit-dir', default='./gambit_out', help='Directory containing gambit_results.json and mutant files (default: ./gambit_out)')
    full_parser.add_argument('--test-cmd', required=True, help="Test command to run (e.g., 'forge test ...')")
    full_parser.add_argument('--project-root', default='.', help='Root directory of the project source code (default: .)')
    full_parser.add_argument('--output', default='gambit_test_results.json', help='Output file for mutation test failures (undetected mutations)')
    full_parser.add_argument('--timeout', type=float, default=3.0, help='Timeout in seconds for each test command (default: 3.0)')
    full_parser.add_argument('--jobs', type=int, default=multiprocessing.cpu_count(), help='Number of parallel jobs (default: logical CPU count)')
    full_parser.add_argument('--build-cmd', default='forge build', help="Build command to run before mutation testing (default: 'forge build')")
    full_parser.add_argument('--debug', action='store_true', help='Enable debug logging and show test command output.')
    full_parser.add_argument('--uncaught', action='store_true', help='Only run mutations that were uncaught in the previous run, as listed in the --output file.')
    full_parser.add_argument('gambit_args', nargs=argparse.REMAINDER, help='Extra arguments to pass to gambit mutate (after --)')

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
    debug: bool,
    build_cmd: str
) -> Optional[Tuple[Optional[Dict[str, Any]], int, Optional[Dict[str, Any]]]]:
    """
    Returns (mutation dict if NOT detected, else None, mutation index, build_fail_info dict if build fails).
    Logs status to stderr and prints test output if debug is True.
    """
    mutant_path = os.path.join(gambit_dir, mutation['name'])
    if not os.path.isfile(mutant_path):
        log(f"[ERROR] [{idx+1}/{total}] Mutant file missing: {mutant_path}. Skipping.", debug)
        return None, idx, None
    original_rel_path = mutation['original']
    try:
        with tempfile.TemporaryDirectory() as tempdir:
            proj_dir = os.path.join(tempdir, 'proj')
            shutil.copytree(project_root, proj_dir, dirs_exist_ok=True)
            mutated_file_path = os.path.join(proj_dir, original_rel_path)
            if not os.path.isfile(mutated_file_path):
                log(f"[ERROR] [{idx+1}/{total}] Original file not found in tempdir: {mutated_file_path}. Skipping.", debug)
                return None, idx, None
            shutil.copy2(mutant_path, mutated_file_path)
            log(f"[INFO] [{idx+1}/{total}] Starting mutation: {mutation.get('name', '')} in {proj_dir}", debug)
            log(f"[INFO] [{idx+1}/{total}] Running build: {build_cmd}", debug)
            try:
                build_result = subprocess.run(
                    build_cmd,
                    shell=True,
                    cwd=proj_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=BUILD_TIMEOUT
                )
                log_build_output(build_result.stdout, build_result.stderr, debug)
                if build_result.returncode != 0:
                    build_fail_info = {
                        'mutant_name': mutation.get('name', ''),
                        'build_cmd': build_cmd,
                        'stdout': build_result.stdout.decode(errors='replace'),
                        'stderr': build_result.stderr.decode(errors='replace'),
                        'mutant_path': mutant_path,
                        'original_rel_path': original_rel_path,
                    }
                    return None, idx, build_fail_info
            except subprocess.TimeoutExpired as e:
                build_fail_info = {
                    'mutant_name': mutation.get('name', ''),
                    'build_cmd': build_cmd,
                    'stdout': (e.stdout or b'').decode(errors='replace'),
                    'stderr': (e.stderr or b'').decode(errors='replace'),
                    'mutant_path': mutant_path,
                    'original_rel_path': original_rel_path,
                    'timeout': True,
                }
                return None, idx, build_fail_info
            except Exception as e:
                build_fail_info = {
                    'mutant_name': mutation.get('name', ''),
                    'build_cmd': build_cmd,
                    'exception': str(e),
                    'mutant_path': mutant_path,
                    'original_rel_path': original_rel_path,
                }
                return None, idx, build_fail_info
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
                    return mutation, idx, None  # Uncaught mutation
                else:
                    log(f"[RESULT] [{idx+1}/{total}] CAUGHT (test suite FAILED) for mutation: {mutation.get('name', '')} (elapsed: {elapsed:.2f}s)", debug)
            except subprocess.TimeoutExpired as e:
                log(f"[TIMEOUT] [{idx+1}/{total}] Test command timed out after {timeout} seconds for mutation {mutation.get('name', '')}.", debug)
                log_output(idx, total, mutation.get('name', ''), e.stdout or b'', e.stderr or b'', debug)
            except Exception as e:
                log(f"[ERROR] [{idx+1}/{total}] Test command failed for mutation {mutation.get('name', '')}: {e}.", debug)
    except Exception as e:
        log(f"[ERROR] [{idx+1}/{total}] Exception in mutation {mutation.get('name', '')}: {e}.", debug)
    return None, idx, None


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
    # --- Run test suite in main context before mutation testing ---
    log(f"[INFO] Running test suite in main project context: {args.test_cmd}", args.debug)
    try:
        test_result = subprocess.run(
            args.test_cmd,
            shell=True,
            cwd=args.project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=args.timeout
        )
        if test_result.returncode != 0:
            print("\n[ERROR] Test suite failed in main project context. Aborting mutation testing.", file=sys.stderr)
            print(f"  Test command: {args.test_cmd}", file=sys.stderr)
            print("  --- TEST STDOUT ---", file=sys.stderr)
            print(test_result.stdout.decode(errors='replace'), file=sys.stderr)
            print("  --- TEST STDERR ---", file=sys.stderr)
            print(test_result.stderr.decode(errors='replace'), file=sys.stderr)
            print("\n[ABORTED] The test suite must pass on the unmutated code before running mutation testing.", file=sys.stderr)
            print("[SUGGESTION] Please fix your tests or code so that the test suite passes, then re-run mutation testing.", file=sys.stderr)
            print("[INFO] No results have been written to disk.\n", file=sys.stderr)
            sys.exit(3)
    except subprocess.TimeoutExpired as e:
        print(f"\n[ERROR] Test suite timed out after {args.timeout} seconds in main project context. Aborting mutation testing.", file=sys.stderr)
        print(f"  Test command: {args.test_cmd}", file=sys.stderr)
        print("  --- TEST STDOUT ---", file=sys.stderr)
        print((e.stdout or b'').decode(errors='replace'), file=sys.stderr)
        print("  --- TEST STDERR ---", file=sys.stderr)
        print((e.stderr or b'').decode(errors='replace'), file=sys.stderr)
        print("\n[ABORTED] The test suite must pass on the unmutated code before running mutation testing.", file=sys.stderr)
        print("[SUGGESTION] Please fix your tests or code so that the test suite passes, then re-run mutation testing.", file=sys.stderr)
        print("[INFO] No results have been written to disk.\n", file=sys.stderr)
        sys.exit(3)
    except Exception as e:
        print(f"\n[ERROR] Exception while running test suite in main project context: {e}. Aborting mutation testing.", file=sys.stderr)
        print(f"  Test command: {args.test_cmd}", file=sys.stderr)
        print("\n[ABORTED] The test suite must pass on the unmutated code before running mutation testing.", file=sys.stderr)
        print("[SUGGESTION] Please fix your tests or code so that the test suite passes, then re-run mutation testing.", file=sys.stderr)
        print("[INFO] No results have been written to disk.\n", file=sys.stderr)
        sys.exit(3)

    try:
        with open(gambit_input_path, 'r') as f:
            mutations = json.load(f)
    except Exception as e:
        log(f"[ERROR] Failed to read {gambit_input_path}: {e}", args.debug)
        sys.exit(1)
    if not mutations:
        log("[ERROR] No mutations found in input file.", args.debug)
        sys.exit(1)

    # If --uncaught is specified, filter mutations to only those in the output file
    if getattr(args, 'uncaught', False):
        try:
            with open(args.output, 'r') as f:
                uncaught_mutations_prev = json.load(f)
        except Exception as e:
            print(f"[ERROR] --uncaught specified but failed to read or parse {args.output}: {e}", file=sys.stderr)
            sys.exit(1)
        if not isinstance(uncaught_mutations_prev, list):
            print(f"[ERROR] --uncaught specified but {args.output} is not a valid list of mutations.", file=sys.stderr)
            sys.exit(1)
        # Use the 'name' field to match mutations
        uncaught_names = set(m.get('name') for m in uncaught_mutations_prev if 'name' in m)
        if not uncaught_names:
            print(f"[ERROR] --uncaught specified but no valid mutant names found in {args.output}.", file=sys.stderr)
            sys.exit(1)
        filtered_mutations = [m for m in mutations if m.get('name') in uncaught_names]
        if not filtered_mutations:
            print(f"[ERROR] --uncaught specified but none of the uncaught mutants from {args.output} are present in {gambit_input_path}.", file=sys.stderr)
            sys.exit(1)
        log(f"[INFO] --uncaught: Running only {len(filtered_mutations)} uncaught mutants from {args.output}.", args.debug)
        mutations = filtered_mutations

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
                    args.debug,
                    args.build_cmd
                ) for idx, mutation in enumerate(mutations)
            ]
            for future in as_completed(futures):
                try:
                    result, _, build_fail_info = future.result()
                    if build_fail_info is not None:
                        print("\n[ERROR] Build failed for mutant:", file=sys.stderr)
                        print(f"  Mutant name: {build_fail_info.get('mutant_name')}", file=sys.stderr)
                        print(f"  Mutant file: {build_fail_info.get('mutant_path')}", file=sys.stderr)
                        print(f"  Original rel path: {build_fail_info.get('original_rel_path')}", file=sys.stderr)
                        print(f"  Build command: {build_fail_info.get('build_cmd')}", file=sys.stderr)
                        if 'timeout' in build_fail_info and build_fail_info['timeout']:
                            print(f"  [TIMEOUT] Build command timed out after {BUILD_TIMEOUT} seconds.", file=sys.stderr)
                        if 'stdout' in build_fail_info and build_fail_info['stdout']:
                            print("  --- BUILD STDOUT ---", file=sys.stderr)
                            print(build_fail_info['stdout'], file=sys.stderr)
                        if 'stderr' in build_fail_info and build_fail_info['stderr']:
                            print("  --- BUILD STDERR ---", file=sys.stderr)
                            print(build_fail_info['stderr'], file=sys.stderr)
                        if 'exception' in build_fail_info:
                            print(f"  Exception: {build_fail_info['exception']}", file=sys.stderr)
                        print("\n[ABORTED] The build failed for this mutant. This usually means your source code has changed since the mutants were generated, and the mutants are now out of date.", file=sys.stderr)
                        print("[SUGGESTION] Please re-run 'gambit mutate' to regenerate mutants for the current codebase.", file=sys.stderr)
                        print("[INFO] No results have been written to disk.\n", file=sys.stderr)
                        kill_child_processes()
                        os._exit(2)
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


def parse_remappings(foundry_toml_path: str) -> List[str]:
    with open(foundry_toml_path, "rb") as f:
        data = tomli.load(f)
    remappings = []
    profile = data.get("profile", {}).get("default", {})
    if "remappings" in profile:
        remappings = profile["remappings"]
    elif "remappings" in data:
        remappings = data["remappings"]
    if isinstance(remappings, list):
        return remappings
    elif isinstance(remappings, str):
        return [remappings]
    return []


def find_sol_files(input_dir: str) -> List[str]:
    sol_files = []
    for root, _, files in os.walk(input_dir):
        for file in files:
            if file.endswith(".sol"):
                sol_files.append(os.path.join(root, file))
    return sol_files


def make_gambit_json_entries(sol_files: List[str], remappings: List[str], sourceroot: str) -> List[Dict[str, Any]]:
    entries = []
    for sol_file in sol_files:
        entries.append({
            "filename": sol_file,
            "sourceroot": sourceroot,
            "solc_remappings": remappings,
        })
    return entries


def generate_main(args):
    remappings = parse_remappings(args.foundry_toml)
    sol_files = find_sol_files(args.input_dir)
    entries = make_gambit_json_entries(sol_files, remappings, args.sourceroot)
    with open(args.output, "w") as f:
        json.dump(entries, f, indent=4)
    print(f"Wrote {len(entries)} entries to {args.output}")
    # Run gambit mutate
    gambit_args = ["gambit", "mutate", "--json", args.output] + (args.gambit_args or [])
    print(f"Running: {' '.join(gambit_args)}")
    try:
        result = subprocess.run(gambit_args, check=True)
        if result.returncode == 0:
            print("gambit mutate completed successfully.")
        else:
            print(f"gambit mutate exited with code {result.returncode}")
    except FileNotFoundError:
        print("[ERROR] 'gambit' command not found. Please ensure Gambit is installed and in your PATH.", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] gambit mutate failed: {e}", file=sys.stderr)
        sys.exit(e.returncode)


def full_main(args):
    # Step 1: Generate mutants (like generate_main)
    remappings = parse_remappings(args.foundry_toml)
    sol_files = find_sol_files(args.input_dir)
    entries = make_gambit_json_entries(sol_files, remappings, args.sourceroot)
    with open(args.gambit_json, "w") as f:
        json.dump(entries, f, indent=4)
    print(f"Wrote {len(entries)} entries to {args.gambit_json}")
    # Run gambit mutate
    gambit_args = ["gambit", "mutate", "--json", args.gambit_json] + (args.gambit_args or [])
    print(f"Running: {' '.join(gambit_args)}")
    try:
        result = subprocess.run(gambit_args, check=True)
        if result.returncode == 0:
            print("gambit mutate completed successfully.")
        else:
            print(f"gambit mutate exited with code {result.returncode}")
    except FileNotFoundError:
        print("[ERROR] 'gambit' command not found. Please ensure Gambit is installed and in your PATH.", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] gambit mutate failed: {e}", file=sys.stderr)
        sys.exit(e.returncode)
    # Step 2: Run mutation testing (like run_main)
    # Prepare a namespace with the right attributes for run_main
    class RunArgs:
        pass
    run_args = RunArgs()
    run_args.test_cmd = args.test_cmd
    run_args.gambit_dir = args.gambit_dir
    run_args.project_root = args.project_root
    run_args.output = args.output
    run_args.timeout = args.timeout
    run_args.jobs = args.jobs
    run_args.build_cmd = args.build_cmd
    run_args.debug = args.debug
    run_args.uncaught = args.uncaught
    run_main(run_args)


def main() -> None:
    args = parse_args()
    if args.subcommand == 'run':
        run_main(args)
    elif args.subcommand == 'report':
        report_main(args)
    elif args.subcommand == 'generate':
        generate_main(args)
    elif args.subcommand == 'full':
        full_main(args)
    else:
        raise ValueError(f"Unknown subcommand: {args.subcommand}")


if __name__ == "__main__":
    main() 