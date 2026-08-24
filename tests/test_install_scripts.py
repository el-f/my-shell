"""Structural and parity tests for install.sh and install.ps1.

Fast, offline tests that parse both scripts as text and verify
structural properties, error handling patterns, and feature parity.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = ROOT / "install.sh"
INSTALL_PS1 = ROOT / "install.ps1"


@pytest.fixture(scope="module")
def sh_content() -> str:
    return INSTALL_SH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ps1_content() -> str:
    return INSTALL_PS1.read_text(encoding="utf-8")


class TestInstallScriptParity:
    def test_both_scripts_exist(self):
        assert INSTALL_SH.exists(), "install.sh missing"
        assert INSTALL_PS1.exists(), "install.ps1 missing"

    def test_both_check_git_prerequisite(self, sh_content, ps1_content):
        assert "command -v git" in sh_content
        assert "Get-Command git" in ps1_content

    def test_both_install_uv_if_missing(self, sh_content, ps1_content):
        assert "command -v uv" in sh_content or "uv not found" in sh_content
        assert "Get-Command uv" in ps1_content or "uv not found" in ps1_content

    def test_both_install_mise_if_missing(self, sh_content, ps1_content):
        assert "command -v mise" in sh_content or "mise not found" in sh_content
        assert "Get-Command mise" in ps1_content or "mise not found" in ps1_content

    def test_both_support_env_var_overrides(self, sh_content, ps1_content):
        for var in ["MY_SHELL_INSTALL_DIR", "MY_SHELL_REPO", "MY_SHELL_BRANCH"]:
            assert var in sh_content, f"install.sh missing {var}"
            assert var in ps1_content, f"install.ps1 missing {var}"

    def test_both_handle_existing_installation(self, sh_content, ps1_content):
        assert "pull --ff-only" in sh_content
        assert "pull --ff-only" in ps1_content

    def test_both_run_mise_install_and_setup(self, sh_content, ps1_content):
        assert "mise install" in sh_content
        assert "mise setup" in sh_content
        assert "mise install" in ps1_content
        assert "mise setup" in ps1_content

    def test_both_verify_deployment_with_doctor(self, sh_content, ps1_content):
        assert "uv run my-shell doctor" in sh_content
        assert "uv run my-shell doctor" in ps1_content
        assert "failed its health check" in sh_content
        assert "failed its health check" in ps1_content

    def test_neither_tells_users_to_export_managed_project_dir(self, sh_content, ps1_content):
        assert "export MY_SHELL_DIR" not in sh_content
        assert "$env:MY_SHELL_DIR =" not in ps1_content

    def test_default_repo_url_matches_between_scripts(self, sh_content, ps1_content):
        sh_match = re.search(r'https://github\.com/[^"\'}\s]+\.git', sh_content)
        ps1_match = re.search(r'https://github\.com/[^"\'}\s]+\.git', ps1_content)
        assert sh_match and ps1_match, "Could not extract repo URLs"
        assert sh_match.group() == ps1_match.group(), (
            f"Repo URLs differ: {sh_match.group()} vs {ps1_match.group()}"
        )

    def test_default_branch_matches_between_scripts(self, sh_content, ps1_content):
        assert "main" in sh_content
        assert "main" in ps1_content


class TestInstallShContent:
    def test_shebang_is_posix_sh(self, sh_content):
        assert sh_content.startswith("#!/bin/sh"), "Shebang must be #!/bin/sh"

    def test_set_eu_present(self, sh_content):
        assert re.search(r"^set -eu$", sh_content, re.MULTILINE), (
            "install.sh must use 'set -eu' for strict mode"
        )

    def test_no_bashisms(self, sh_content):
        # Skip shebang and comment lines
        code_lines = [
            line
            for line in sh_content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        code = "\n".join(code_lines)
        # No [[ ]] (bash-only test syntax)
        assert "[[" not in code, "Bashism detected: [[ ]]"
        # No (( )) arithmetic (bash-only)
        assert not re.search(r"\(\(.*\)\)", code), "Bashism detected: (( ))"

    def test_curl_wget_fallback_for_uv(self, sh_content):
        # Both curl and wget paths exist for uv installation
        uv_section = sh_content[sh_content.index("uv not found") :]
        uv_section = uv_section[: uv_section.index("ok")]
        assert "curl" in uv_section
        assert "wget" in uv_section

    def test_curl_wget_fallback_for_mise(self, sh_content):
        mise_section = sh_content[sh_content.index("mise not found") :]
        mise_section = mise_section[: mise_section.index("ok")]
        assert "curl" in mise_section
        assert "wget" in mise_section

    def test_fail_function_exits_nonzero(self, sh_content):
        assert re.search(r"fail\(\).*exit 1", sh_content), "fail() must exit with nonzero code"

    def test_home_validation_present(self, sh_content):
        assert "HOME" in sh_content
        assert re.search(r"-z.*HOME|HOME.*-z|HOME is not set", sh_content), (
            "install.sh must validate HOME is set"
        )

    def test_mise_yes_or_trust_present(self, sh_content):
        assert "MISE_YES" in sh_content or "mise trust" in sh_content, (
            "install.sh must handle mise trust for non-interactive use"
        )

    def test_error_handling_on_git_clone(self, sh_content):
        # git clone should have error handling
        assert re.search(r"git clone.*\|\| fail", sh_content), (
            "git clone must have || fail error handling"
        )

    def test_error_handling_on_mise_commands(self, sh_content):
        # mise install is best-effort (optional tools); mise setup is the deploy, so it must abort
        assert re.search(r"if ! mise install --locked", sh_content), (
            "mise install must have non-fatal error handling (if ! mise install)"
        )
        assert re.search(r"mise setup \|\| fail", sh_content), (
            "a failed mise setup must abort the install, not warn"
        )

    def test_trap_handler_present(self, sh_content):
        assert "trap" in sh_content, "install.sh must have a trap handler for cleanup"

    def test_existing_non_git_dir_handled(self, sh_content):
        assert "is not a git repository" in sh_content, (
            "install.sh must handle existing non-git directory"
        )

    def test_tput_guarded(self, sh_content):
        # tput calls should be guarded with 2>/dev/null
        tput_lines = [
            line for line in sh_content.splitlines() if "tput" in line and "command -v" not in line
        ]
        for line in tput_lines:
            assert "2>/dev/null" in line, f"Unguarded tput call: {line.strip()}"


class TestInstallPs1Content:
    def test_tls12_enabled(self, ps1_content):
        assert "Tls12" in ps1_content, "install.ps1 must enable TLS 1.2"

    def test_write_fail_throws_instead_of_exiting(self, ps1_content):
        # `irm ... | iex` shares the caller's runspace, where `exit` closes the session.
        assert re.search(r"function Write-Fail.*throw", ps1_content), (
            "Write-Fail must throw, not exit"
        )
        assert not re.search(r"function Write-Fail.*exit 1", ps1_content), (
            "Write-Fail must not call exit"
        )

    def test_lastexitcode_checked_after_native_commands(self, ps1_content):
        # After git clone and mise commands, LASTEXITCODE must be checked
        assert ps1_content.count("LASTEXITCODE") >= 3, (
            "install.ps1 must check LASTEXITCODE after native commands"
        )

    def test_localappdata_validation_present(self, ps1_content):
        assert re.search(r"LOCALAPPDATA.*fail|LOCALAPPDATA.*not", ps1_content, re.IGNORECASE), (
            "install.ps1 must validate LOCALAPPDATA is set"
        )

    def test_mise_yes_present(self, ps1_content):
        assert "MISE_YES" in ps1_content, "install.ps1 must set MISE_YES for non-interactive use"

    def test_winget_fallback_present(self, ps1_content):
        assert "Get-Command winget" in ps1_content, "install.ps1 must check if winget is available"
        assert "mise.run" in ps1_content or "mise installer" in ps1_content.lower(), (
            "install.ps1 must have fallback when winget is unavailable"
        )

    def test_iex_scope_isolation(self, ps1_content):
        assert "scriptblock" in ps1_content.lower() or "ScriptBlock" in ps1_content, (
            "install.ps1 should run downloaded scripts in isolated scope"
        )
        assert "| Invoke-Expression" not in ps1_content, (
            "install.ps1 should not pipe directly to Invoke-Expression (scope leak)"
        )

    def test_existing_non_git_dir_handled(self, ps1_content):
        assert "is not a git repository" in ps1_content, (
            "install.ps1 must handle existing non-git directory"
        )

    def test_no_non_ascii_in_executable_lines(self, ps1_content):
        """Windows PowerShell 5.1 misreads non-ASCII in BOM-less UTF-8 files."""
        non_ascii = [
            (i + 1, line)
            for i, line in enumerate(ps1_content.splitlines())
            if not line.isascii() and not line.lstrip().startswith("#")
        ]
        assert not non_ascii, (
            f"Non-ASCII on executable lines {[n for n, _ in non_ascii]}. "
            f"Windows PowerShell 5.1 reads BOM-less UTF-8 as Windows-1252, "
            f"breaking parsing. Use ASCII alternatives."
        )

    def test_ps_version_runtime_check(self, ps1_content):
        assert "PSVersionTable" in ps1_content, (
            "install.ps1 must have runtime PowerShell version check"
        )

    def test_conditional_success_message(self, ps1_content):
        # Success is claimed only when every optional step also worked
        assert re.search(r"if \(\$toolsOk -and \$updateOk\)", ps1_content), (
            "install.ps1 must track optional-step success state"
        )

    def test_mise_setup_failure_aborts(self, ps1_content):
        assert re.search(
            r"mise setup\s*\n\s*if \(\$LASTEXITCODE -ne 0\) \{\s*\n\s*Write-Fail", ps1_content
        ), "a failed mise setup must abort the install, not warn"
