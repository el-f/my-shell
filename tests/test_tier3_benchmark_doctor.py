"""Tests for benchmark history/trend/overhead and doctor --json/--fix/startup."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.benchmark import (
    BenchmarkResult,
    _load_history,
    _overhead_note,
    _rate_startup,
    _record_history,
    _summarize,
    _trend_note,
    run_benchmark,
)


def _res(shell: str, mean: float) -> BenchmarkResult:
    return BenchmarkResult(shell, mean, None, mean, mean, 3, False)


@pytest.mark.parametrize(
    "ms,expected",
    [
        (50.0, "fast"),
        (99.9, "fast"),
        (100.0, "ok"),
        (250.0, "ok"),
        (300.0, "slow"),
        (900.0, "slow"),
    ],
)
def test_rate_startup_thresholds(ms, expected):
    assert expected in _rate_startup(ms)


def test_summarize_computes_stats():
    r = _summarize("nushell", [10.0, 20.0, 30.0], 3)
    assert r.mean_ms == 20.0
    assert r.min_ms == 10.0
    assert r.max_ms == 30.0
    assert r.runs == 3
    assert r.used_hyperfine is False


def test_summarize_single_run_has_no_stddev():
    assert _summarize("nushell", [12.0], 1).stddev_ms is None


def test_hyperfine_null_stddev_is_none_not_crash():
    """hyperfine emits stddev=null for --runs 1; parsing must yield None, not TypeError."""
    import json

    from core.benchmark import _benchmark_hyperfine

    hf = {"results": [{"mean": 0.05, "stddev": None, "min": 0.05, "max": 0.05}]}
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = json.dumps(hf)
    with patch("core.benchmark.subprocess.run", return_value=proc):
        result = _benchmark_hyperfine("nushell", "nu", runs=1)
    assert result.stddev_ms is None
    assert result.mean_ms == 50.0


def test_load_history_ignores_non_utf8(tmp_path: Path):
    """A corrupt (non-UTF-8) history file returns [] instead of crashing benchmark."""
    path = tmp_path / "benchmark-history.json"
    path.write_bytes(b"\xff\xfe not valid utf-8 \x80")
    assert _load_history(path) == []


def test_record_history_roundtrip_and_previous(tmp_path: Path):
    prev = _record_history(tmp_path, [_res("nushell", 40.0)], "2026-01-01T00:00:00+00:00")
    assert prev == {}
    prev2 = _record_history(tmp_path, [_res("nushell", 55.0)], "2026-01-02T00:00:00+00:00")
    assert prev2 == {"nushell": 40.0}
    hist = _load_history(tmp_path / "benchmark-history.json")
    assert len(hist) == 2
    assert hist[-1]["mean_ms"] == 55.0


def test_record_history_keeps_last_20(tmp_path: Path):
    for i in range(25):
        _record_history(tmp_path, [_res("nushell", float(i))], f"2026-01-01T00:00:{i:02d}+00:00")
    hist = _load_history(tmp_path / "benchmark-history.json")
    assert len(hist) == 20
    assert hist[-1]["mean_ms"] == 24.0


def test_load_history_missing_returns_empty(tmp_path: Path):
    assert _load_history(tmp_path / "nope.json") == []


def test_trend_note_directions():
    prev = {"nushell": 50.0}
    assert "+10.0 ms" in _trend_note(_res("nushell", 60.0), prev)
    assert "-10.0 ms" in _trend_note(_res("nushell", 40.0), prev)
    assert _trend_note(_res("nushell", 50.0), prev) == "no change vs last run"
    assert _trend_note(_res("xonsh", 30.0), prev) is None


def test_overhead_note_pairs():
    results = [_res("nushell (empty config)", 40.0), _res("nushell (my-shell config)", 60.0)]
    assert "nushell: my-shell adds +20.0 ms (50%)" in _overhead_note(results)


def test_overhead_note_none_without_pairs():
    assert _overhead_note([_res("nushell", 40.0)]) is None


# run_benchmark


def _mock_ok_proc() -> MagicMock:
    proc = MagicMock()
    proc.returncode = 0
    return proc


def test_run_benchmark_records_history(tmp_path: Path):
    with (
        patch("core.benchmark.is_available", side_effect=lambda n: n == "nu"),
        patch("core.benchmark.subprocess.run", return_value=_mock_ok_proc()),
    ):
        results = run_benchmark(shells=["nushell"], quiet=True, history_dir=tmp_path)
    assert len(results) == 1
    hist = _load_history(tmp_path / "benchmark-history.json")
    assert len(hist) == 1
    assert hist[0]["shell"] == "nushell"


def test_run_benchmark_no_history_dir_writes_nothing(tmp_path: Path):
    with (
        patch("core.benchmark.is_available", side_effect=lambda n: n == "nu"),
        patch("core.benchmark.subprocess.run", return_value=_mock_ok_proc()),
    ):
        run_benchmark(shells=["nushell"], quiet=True)
    assert not (tmp_path / "benchmark-history.json").exists()


def test_run_benchmark_threads_runs(tmp_path: Path):
    with (
        patch("core.benchmark.is_available", side_effect=lambda n: n == "nu"),
        patch("core.benchmark.subprocess.run", return_value=_mock_ok_proc()) as mock_run,
    ):
        results = run_benchmark(shells=["nushell"], runs=2, quiet=True)
    assert results[0].runs == 2
    assert mock_run.call_count == 2


def test_run_benchmark_quiet_suppresses_status_logs(capsys):
    with (
        patch("core.benchmark.is_available", side_effect=lambda n: n == "nu"),
        patch("core.benchmark.subprocess.run", return_value=_mock_ok_proc()),
    ):
        run_benchmark(shells=["nushell"], quiet=True)
    out = capsys.readouterr().out
    assert "Benchmarking" not in out  # log_step suppressed by quiet


def test_doctor_json_is_valid():
    import json

    from core.doctor import CheckResult, doctor_json

    results = [
        CheckResult("A", "pass", "ok"),
        CheckResult("B", "fail", "bad", fix="do x"),
    ]
    data = json.loads(doctor_json(results))
    assert data["summary"]["pass"] == 1
    assert data["summary"]["fail"] == 1
    assert data["results"][1]["fix"] == "do x"


def test_doctor_json_cli_emits_pure_json(capsys):
    """`doctor --json` prints ONLY JSON on stdout -- no progress-log preamble.

    run_doctor logs 'Running health checks...' to stdout; --json must not let that
    leak into the machine-readable output (CI parses it).
    """
    import json

    from core import cli
    from core.doctor import CheckResult

    def fake_run_doctor(*args, **kwargs):
        print("Running health checks...")  # the stdout log that must be suppressed
        return [CheckResult("A", "pass", "ok")]

    with patch("core.doctor.run_doctor", side_effect=fake_run_doctor):
        cli.doctor(output_json=True, fix=False)

    out = capsys.readouterr().out
    assert "Running health checks" not in out
    data = json.loads(out)  # must parse -- no preamble
    assert data["summary"]["pass"] == 1


def test_doctor_json_fix_suppresses_fix_stdout(capsys):
    """`doctor --json --fix` keeps fix_doctor_issues' stdout out of the JSON too."""
    import json

    from core import cli
    from core.doctor import CheckResult

    def fake_fix():
        print("Installing 3 missing tool(s)...")  # fix logs to stdout

    with (
        patch("core.doctor.fix_doctor_issues", side_effect=fake_fix),
        patch("core.doctor.run_doctor", return_value=[CheckResult("A", "pass", "ok")]),
    ):
        cli.doctor(output_json=True, fix=True)

    out = capsys.readouterr().out
    assert "Installing" not in out
    assert json.loads(out)["summary"]["pass"] == 1


@pytest.mark.parametrize(
    "ms,status",
    [(100.0, "pass"), (299.0, "pass"), (400.0, "warn"), (500.0, "warn"), (600.0, "fail")],
)
def test_check_startup_time_thresholds(ms, status):
    from core.doctor import _check_startup_time

    br = BenchmarkResult("nushell", ms, None, ms, ms, 3, False)
    free = BenchmarkResult("nushell", 0.0, None, 0.0, 0.0, 3, False)
    with (
        patch("core.benchmark._shell_binary", return_value="nu"),
        patch("core.benchmark._benchmark_basic", return_value=br),
        patch("core.benchmark._benchmark_empty_config", return_value=free),
    ):
        result = _check_startup_time("nushell")
    assert result.status == status


def test_check_startup_time_no_binary_is_info():
    from core.doctor import _check_startup_time

    with patch("core.benchmark._shell_binary", return_value=None):
        result = _check_startup_time("nushell")
    assert result.status == "info"


def test_check_startup_time_slow_shell_is_rated_on_my_shell_overhead():
    """A shell that is slow on its own is not a my-shell failure.

    xonsh on Windows needs ~2.3 s to import itself. Rating the absolute number
    fails forever no matter what my-shell deploys.
    """
    from core.doctor import _check_startup_time

    deployed = BenchmarkResult("xonsh", 2350.0, None, 2350.0, 2350.0, 3, False)
    empty = BenchmarkResult("xonsh", 2300.0, None, 2300.0, 2300.0, 3, False)
    with (
        patch("core.benchmark._shell_binary", return_value="xonsh"),
        patch("core.benchmark._benchmark_basic", return_value=deployed),
        patch("core.benchmark._benchmark_empty_config", return_value=empty),
    ):
        result = _check_startup_time("xonsh")
    assert result.status == "pass"
    assert "2300" in result.message  # the baseline is named, not hidden


def test_check_startup_time_fails_when_my_shell_config_is_the_slow_part():
    from core.doctor import _check_startup_time

    deployed = BenchmarkResult("xonsh", 2900.0, None, 2900.0, 2900.0, 3, False)
    empty = BenchmarkResult("xonsh", 2300.0, None, 2300.0, 2300.0, 3, False)
    with (
        patch("core.benchmark._shell_binary", return_value="xonsh"),
        patch("core.benchmark._benchmark_basic", return_value=deployed),
        patch("core.benchmark._benchmark_empty_config", return_value=empty),
    ):
        result = _check_startup_time("xonsh")
    assert result.status == "fail"


def test_check_startup_time_fast_shell_skips_the_baseline_run():
    """The baseline costs a second shell start -- only pay it on the slow path."""
    from core.doctor import _check_startup_time

    fast = BenchmarkResult("nushell", 35.0, None, 35.0, 35.0, 3, False)
    with (
        patch("core.benchmark._shell_binary", return_value="nu"),
        patch("core.benchmark._benchmark_basic", return_value=fast),
        patch("core.benchmark._benchmark_empty_config") as baseline,
    ):
        result = _check_startup_time("nushell")
    assert result.status == "pass"
    baseline.assert_not_called()


def test_check_startup_time_keeps_absolute_rating_when_baseline_fails():
    from core.doctor import _check_startup_time

    deployed = BenchmarkResult("xonsh", 2350.0, None, 2350.0, 2350.0, 3, False)
    with (
        patch("core.benchmark._shell_binary", return_value="xonsh"),
        patch("core.benchmark._benchmark_basic", return_value=deployed),
        patch("core.benchmark._benchmark_empty_config", side_effect=RuntimeError("boom")),
    ):
        result = _check_startup_time("xonsh")
    assert result.status == "fail"


def test_disabled_xonsh_that_is_installed_warns():
    """The canary: a shell on the machine that my-shell silently does not manage."""
    from core.doctor import _check_shell_binaries

    with (
        patch("core.doctor.load_settings", return_value={"shells": {"xonsh": False}}),
        patch("core.doctor.is_available", return_value=True),
    ):
        results = _check_shell_binaries()
    xonsh = next(r for r in results if r.name == "xonsh binary")
    assert xonsh.status == "warn"
    assert "installed" in xonsh.message
    assert xonsh.fix and "xonsh = true" in xonsh.fix


def test_disabled_xonsh_that_is_absent_stays_info():
    from core.doctor import _check_shell_binaries

    def _available(binary):
        return binary != "xonsh"

    with (
        patch("core.doctor.load_settings", return_value={"shells": {"xonsh": False}}),
        patch("core.doctor.is_available", side_effect=_available),
    ):
        results = _check_shell_binaries()
    xonsh = next(r for r in results if r.name == "xonsh binary")
    assert xonsh.status == "info"


def test_fix_doctor_issues_installs_missing_tools():
    from core.doctor import fix_doctor_issues

    installed: list[str] = []
    with (
        patch("core.doctor.is_available", return_value=False),
        patch("core.config.is_integration_enabled", return_value=True),
        patch("core.config.load_settings", return_value={}),
        patch("core.install.install_tool", side_effect=installed.append),
    ):
        fix_doctor_issues()
    assert "atuin" in installed  # an integration tool
    assert "fzf" in installed  # an optional tool


def test_fix_doctor_issues_noop_when_all_available(capsys):
    from core.doctor import fix_doctor_issues

    with (
        patch("core.doctor.is_available", return_value=True),
        patch("core.config.is_integration_enabled", return_value=True),
        patch("core.config.load_settings", return_value={}),
        patch("core.install.install_tool") as mock_install,
    ):
        fix_doctor_issues()
    mock_install.assert_not_called()
    assert "Nothing to fix" in capsys.readouterr().out


def test_check_plugins_warns_when_a_registered_plugin_is_stale(tmp_project: Path):
    """Installed as a file, dead at runtime: nushell refuses a plugin from an older minor."""
    from core.doctor import _check_plugins

    with (
        patch("core.doctor.is_plugin_installed", return_value=True),
        patch("core.plugins.registered_plugin_versions", return_value={"gstat": "0.111.0"}),
        patch("core.plugins._get_nu_version", return_value="0.114.1"),
    ):
        results = _check_plugins(tmp_project)

    stale = next(r for r in results if "stale" in r.message or "rebuild" in (r.fix or ""))
    assert stale.status == "warn"
    assert "gstat" in stale.message


def test_check_plugins_stays_quiet_when_versions_match(tmp_project: Path):
    from core.doctor import _check_plugins

    with (
        patch("core.doctor.is_plugin_installed", return_value=True),
        patch("core.plugins.registered_plugin_versions", return_value={"gstat": "0.114.1"}),
        patch("core.plugins._get_nu_version", return_value="0.114.1"),
    ):
        results = _check_plugins(tmp_project)

    assert all(r.status == "pass" for r in results)
