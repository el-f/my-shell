"""Shell startup time benchmark module."""

from __future__ import annotations

import json
import shlex
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path

from .utils import is_available, log_error, log_info, log_step, log_success, log_warn

# Shells we know how to benchmark, mapped to their binary name.
_SHELL_BINARIES: dict[str, str] = {
    "nushell": "nu",
    "xonsh": "xonsh",
}

_DEFAULT_RUNS = 5
_SUBPROCESS_TIMEOUT = 30
_HISTORY_KEEP = 20


@dataclass
class BenchmarkResult:
    """Result of benchmarking a single shell's startup time."""

    shell: str
    mean_ms: float
    stddev_ms: float | None
    min_ms: float
    max_ms: float
    runs: int
    used_hyperfine: bool


def _shell_binary(shell: str) -> str | None:
    """Return the binary name for a shell, or None if not available."""
    binary = _SHELL_BINARIES.get(shell)
    if binary is None or not is_available(binary):
        return None
    return binary


def _time_invocations(
    cmd: list[str], runs: int, *, label: str | None = None, context: str = "startup benchmark"
) -> list[float]:
    """Time `runs` invocations of `cmd`, returning elapsed ms per run.

    Raises RuntimeError if any invocation exits non-zero. Shows a live progress
    bar only when `label` is set and stdout is a real terminal.
    """
    times: list[float] = []

    def _one() -> None:
        start = time.perf_counter()
        proc = subprocess.run(cmd, capture_output=True, timeout=_SUBPROCESS_TIMEOUT)
        elapsed = (time.perf_counter() - start) * 1000
        if proc.returncode != 0:
            raise RuntimeError(f"{cmd[0]} exited {proc.returncode} ({context})")
        times.append(elapsed)

    if label is not None and sys.stdout.isatty():
        from rich.progress import BarColumn, Progress, TextColumn

        with Progress(
            TextColumn("[cyan]{task.description}"), BarColumn(), transient=True
        ) as progress:
            task = progress.add_task(label, total=runs)
            for _ in range(runs):
                _one()
                progress.update(task, advance=1)
    else:
        for _ in range(runs):
            _one()
    return times


def _summarize(shell: str, times: list[float], runs: int) -> BenchmarkResult:
    """Build a BenchmarkResult from a list of per-run elapsed times (ms)."""
    stddev = statistics.stdev(times) if len(times) >= 2 else None
    return BenchmarkResult(
        shell=shell,
        mean_ms=round(statistics.mean(times), 2),
        stddev_ms=round(stddev, 2) if stddev is not None else None,
        min_ms=round(min(times), 2),
        max_ms=round(max(times), 2),
        runs=runs,
        used_hyperfine=False,
    )


def _benchmark_basic(
    shell: str, binary: str, runs: int = _DEFAULT_RUNS, *, show_progress: bool = False
) -> BenchmarkResult:
    """Benchmark shell startup by timing subprocess invocations."""
    times = _time_invocations(
        [binary, "-c", "exit"], runs, label=f"Timing {shell}" if show_progress else None
    )
    return _summarize(shell, times, runs)


def _stddev_ms(stddev_s: float | None) -> float | None:
    """hyperfine reports stddev=null for a single run -- keep it None, don't crash."""
    return round(stddev_s * 1000, 2) if stddev_s is not None else None


def _hyperfine_argv(runs: int, argv: list[str]) -> list[str]:
    """Build the hyperfine command line for *argv*.

    -N keeps hyperfine from handing the string to a shell; the quoting makes a
    path containing a space stay one argument.
    """
    return ["hyperfine", "--json", "-N", "--warmup", "2", "--runs", str(runs), shlex.join(argv)]


def _benchmark_hyperfine(
    shell: str,
    binary: str,
    runs: int = _DEFAULT_RUNS,
    *,
    extra_args: list[str] | None = None,
) -> BenchmarkResult:
    """Benchmark shell startup using hyperfine for statistical accuracy."""
    result = subprocess.run(
        _hyperfine_argv(runs, [binary, *(extra_args or []), "-c", "exit"]),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"hyperfine failed: {result.stderr.strip()}")

    data = json.loads(result.stdout)
    bench = data["results"][0]

    # hyperfine reports times in seconds
    mean_s = bench["mean"]
    stddev_s = bench["stddev"]
    min_s = bench["min"]
    max_s = bench["max"]

    return BenchmarkResult(
        shell=shell,
        mean_ms=round(mean_s * 1000, 2),
        stddev_ms=_stddev_ms(stddev_s),
        min_ms=round(min_s * 1000, 2),
        max_ms=round(max_s * 1000, 2),
        runs=runs,
        used_hyperfine=True,
    )


def _benchmark_empty_config(
    shell: str,
    binary: str,
    runs: int = _DEFAULT_RUNS,
    *,
    use_hyperfine: bool = False,
    show_progress: bool = False,
) -> BenchmarkResult:
    """Time the shell started with an empty config -- what the shell itself costs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env_flag: list[str] = []
        if shell == "nushell":
            env_flag = ["--config", f"{tmpdir}/empty.nu", "--env-config", f"{tmpdir}/empty-env.nu"]
            # nu refuses to start when the files it is pointed at do not exist
            for fname in ("empty.nu", "empty-env.nu"):
                with open(f"{tmpdir}/{fname}", "w") as f:
                    f.write("")
        elif shell == "xonsh":
            env_flag = ["--rc", f"{tmpdir}/empty.xsh"]
            with open(f"{tmpdir}/empty.xsh", "w") as f:
                f.write("")

        if use_hyperfine:
            return replace(
                _benchmark_hyperfine(shell, binary, runs, extra_args=env_flag),
                shell=f"{shell} (empty config)",
            )
        times = _time_invocations(
            [binary, *env_flag, "-c", "exit"],
            runs,
            label=f"Timing {shell} (empty)" if show_progress else None,
            context="empty config",
        )
        return _summarize(f"{shell} (empty config)", times, runs)


def _benchmark_detailed(
    shell: str,
    binary: str,
    runs: int = _DEFAULT_RUNS,
    use_hyperfine: bool = False,
    *,
    show_progress: bool = False,
) -> list[BenchmarkResult]:
    """Benchmark with empty config vs my-shell config for comparison.

    Returns a list of two BenchmarkResult objects:
      [0] = startup with an empty (no-config) environment
      [1] = startup with the normal my-shell config
    """
    results: list[BenchmarkResult] = [
        _benchmark_empty_config(
            shell, binary, runs, use_hyperfine=use_hyperfine, show_progress=show_progress
        )
    ]

    # --- Normal (my-shell) config ---
    base = (
        _benchmark_hyperfine(shell, binary, runs)
        if use_hyperfine
        else _benchmark_basic(shell, binary, runs, show_progress=show_progress)
    )
    results.append(replace(base, shell=f"{shell} (my-shell config)"))

    return results


def _rate_startup(ms: float) -> str:
    """Rate a shell startup time for human context."""
    if ms < 100:
        return "[green]fast[/]"
    if ms < 300:
        return "[yellow]ok[/]"
    return "[red]slow[/]"


def _now_iso() -> str:
    """Current UTC time as an ISO-8601 string."""
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _load_history(path: Path) -> list[dict]:
    """Load the benchmark history list, or [] when missing/unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except OSError, ValueError:
        # ValueError covers JSONDecodeError and UnicodeDecodeError (corrupt/non-UTF file).
        return []


def _record_history(
    history_dir: Path, results: list[BenchmarkResult], timestamp: str
) -> dict[str, float]:
    """Append this run's means to the history file; return the PREVIOUS mean per shell.

    Keeps only the last _HISTORY_KEEP entries. Best-effort -- never raises.
    """
    path = history_dir / "benchmark-history.json"
    history = _load_history(path)

    # Most recent prior mean per shell, before appending this run.
    previous: dict[str, float] = {
        entry["shell"]: entry["mean_ms"]
        for entry in history
        if "shell" in entry and "mean_ms" in entry
    }

    history.extend({"shell": r.shell, "mean_ms": r.mean_ms, "ts": timestamp} for r in results)
    history = history[-_HISTORY_KEEP:]

    try:
        history_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(history, indent=2), encoding="utf-8")
    except OSError as e:
        log_warn(f"Could not write benchmark history: {e}")

    return previous


def _trend_note(result: BenchmarkResult, previous: dict[str, float]) -> str | None:
    """A '+12 ms vs last run' note, or None when there's no prior data for this shell."""
    prev = previous.get(result.shell)
    if prev is None:
        return None
    delta = round(result.mean_ms - prev, 2)
    if delta > 0:
        return f"[red]+{delta} ms[/] vs last run"
    if delta < 0:
        return f"[green]{delta} ms[/] vs last run"
    return "no change vs last run"


def _overhead_note(results: list[BenchmarkResult]) -> str | None:
    """For detailed runs, turn each (empty, my-shell) pair into an overhead line."""
    empties = {
        r.shell.removesuffix(" (empty config)"): r
        for r in results
        if r.shell.endswith("(empty config)")
    }
    lines = []
    for r in results:
        if not r.shell.endswith("(my-shell config)"):
            continue
        base_key = r.shell.removesuffix(" (my-shell config)")
        base = empties.get(base_key)
        if base is None or base.mean_ms <= 0:
            continue
        overhead = round(r.mean_ms - base.mean_ms, 2)
        pct = round((overhead / base.mean_ms) * 100)
        lines.append(f"{base_key}: my-shell adds +{overhead} ms ({pct}%)")
    return "\n  ".join(lines) if lines else None


def print_benchmark_results(
    results: list[BenchmarkResult],
    *,
    previous: dict[str, float] | None = None,
    show_overhead: bool = False,
) -> None:
    """Print benchmark results in a formatted table using Rich.

    previous: prior mean per shell -> a trend note per row.
    show_overhead: for detailed runs, print the my-shell overhead line.
    """
    from rich.console import Console
    from rich.table import Table

    console = Console(highlight=False)

    table = Table(title="Shell Startup Benchmark", show_lines=True)
    table.add_column("Shell", style="bold cyan")
    table.add_column("Mean (ms)", justify="right")
    table.add_column("Rating", justify="center")
    table.add_column("Std Dev (ms)", justify="right")
    table.add_column("Min (ms)", justify="right")
    table.add_column("Max (ms)", justify="right")
    table.add_column("Runs", justify="right")
    table.add_column("Method", justify="center")

    for r in results:
        stddev_str = f"{r.stddev_ms}" if r.stddev_ms is not None else "-"
        method = "hyperfine" if r.used_hyperfine else "perf_counter"
        table.add_row(
            r.shell,
            f"{r.mean_ms}",
            _rate_startup(r.mean_ms),
            stddev_str,
            f"{r.min_ms}",
            f"{r.max_ms}",
            str(r.runs),
            method,
        )

    console.print()
    console.print(table)
    console.print()

    if show_overhead:
        note = _overhead_note(results)
        if note:
            console.print(f"  [bold]Overhead[/]  {note}")
            console.print()

    if previous:
        for r in results:
            trend = _trend_note(r, previous)
            if trend:
                console.print(f"  {r.shell}: {trend}")
        console.print()


def run_benchmark(
    shells: list[str] | None = None,
    detailed: bool = False,
    *,
    runs: int = _DEFAULT_RUNS,
    quiet: bool = False,
    history_dir: Path | None = None,
) -> list[BenchmarkResult]:
    """Time shell startup. detailed compares an empty config against the my-shell one;
    history_dir records the run and shows a trend against the previous one.
    """
    if shells is None:
        shells = list(_SHELL_BINARIES.keys())

    use_hyperfine = is_available("hyperfine")
    if not quiet:
        log_info(
            "hyperfine detected -- using it for statistical accuracy"
            if use_hyperfine
            else "hyperfine not found -- falling back to manual timing"
        )

    all_results: list[BenchmarkResult] = []
    show_progress = not quiet

    for shell in shells:
        binary = _shell_binary(shell)
        if binary is None:
            if not quiet:
                log_warn(f"{shell}: binary not found, skipping")
            continue

        if not quiet:
            log_step(f"Benchmarking {shell}...")

        try:
            if detailed:
                all_results.extend(
                    _benchmark_detailed(
                        shell, binary, runs, use_hyperfine, show_progress=show_progress
                    )
                )
            elif use_hyperfine:
                all_results.append(_benchmark_hyperfine(shell, binary, runs))
            else:
                all_results.append(
                    _benchmark_basic(shell, binary, runs, show_progress=show_progress)
                )

            if not quiet:
                log_success(f"{shell}: {all_results[-1].mean_ms} ms mean startup")
        except subprocess.TimeoutExpired:
            log_error(f"{shell}: timed out during benchmark")
        except Exception as e:
            log_error(f"{shell}: benchmark failed -- {e}")

    if all_results:
        previous = (
            _record_history(history_dir, all_results, _now_iso())
            if history_dir is not None
            else None
        )
        print_benchmark_results(all_results, previous=previous, show_overhead=detailed)
    elif not quiet:
        log_warn("No shells were benchmarked")

    return all_results
