"""Tests for core.fonts -- Nerd Font detection and installation."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from core.fonts import (
    NERD_FONTS,
    _font_dirs,
    ensure_nerd_font,
    install_nerd_font,
    is_nerd_font_installed,
)


class TestIsNerdFontInstalled:
    """Font detection via filesystem glob."""

    def test_returns_false_for_unknown_font(self):
        assert is_nerd_font_installed("unknown_font") is False

    def test_detects_font_in_directory(self, tmp_path: Path):
        """Should detect a font when matching .ttf files exist."""
        font_dir = tmp_path / "fonts"
        font_dir.mkdir()
        (font_dir / "MesloLGSNerdFont-Regular.ttf").write_bytes(b"fake")

        with patch("core.fonts._font_dirs", return_value=[font_dir]):
            assert is_nerd_font_installed("meslo") is True

    def test_detects_font_otf(self, tmp_path: Path):
        """Should detect .otf files too."""
        font_dir = tmp_path / "fonts"
        font_dir.mkdir()
        (font_dir / "FiraCodeNerdFont-Bold.otf").write_bytes(b"fake")

        with patch("core.fonts._font_dirs", return_value=[font_dir]):
            assert is_nerd_font_installed("firacode") is True

    def test_detects_font_in_subdirectory(self, tmp_path: Path):
        """Should find fonts in subdirectories (Linux /usr/share/fonts/truetype/...)."""
        sub = tmp_path / "fonts" / "truetype" / "meslo"
        sub.mkdir(parents=True)
        (sub / "MesloLGSNerdFont-Regular.ttf").write_bytes(b"fake")

        with patch("core.fonts._font_dirs", return_value=[tmp_path / "fonts"]):
            assert is_nerd_font_installed("meslo") is True

    def test_returns_false_when_no_match(self, tmp_path: Path):
        """Should return False when directory exists but no matching fonts."""
        font_dir = tmp_path / "fonts"
        font_dir.mkdir()
        (font_dir / "Arial.ttf").write_bytes(b"fake")

        with patch("core.fonts._font_dirs", return_value=[font_dir]):
            assert is_nerd_font_installed("meslo") is False

    def test_returns_false_when_dir_missing(self, tmp_path: Path):
        """Should handle non-existent directories gracefully."""
        missing = tmp_path / "nonexistent"
        with patch("core.fonts._font_dirs", return_value=[missing]):
            assert is_nerd_font_installed("meslo") is False

    @patch("core.fonts.get_os", return_value="linux")
    @patch("core.fonts.is_available", return_value=True)
    def test_linux_fc_list_fallback(self, mock_avail, mock_os, tmp_path: Path):
        """Should fall back to fc-list on Linux."""
        with (
            patch("core.fonts._font_dirs", return_value=[tmp_path / "empty"]),
            patch("core.fonts.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                stdout="Some Font\nMeslo LG Nerd Font\nOther Font",
                returncode=0,
            )
            assert is_nerd_font_installed("meslo") is True

    @patch("core.fonts.get_os", return_value="windows")
    def test_no_fc_list_on_windows(self, mock_os, tmp_path: Path):
        """Should NOT use fc-list on Windows."""
        with (
            patch("core.fonts._font_dirs", return_value=[tmp_path / "empty"]),
            patch("core.fonts.subprocess.run") as mock_run,
        ):
            result = is_nerd_font_installed("meslo")
            assert result is False
            mock_run.assert_not_called()


class TestInstallNerdFont:
    """Font installation with fallback chain."""

    def test_unknown_font_returns_false(self):
        assert install_nerd_font("nonexistent") is False

    @patch.dict("os.environ", {"DISPLAY": ":0"})
    @patch("core.fonts.is_nerd_font_installed", return_value=True)
    def test_already_installed_returns_true(self, mock_check):
        assert install_nerd_font("meslo") is True

    @patch.dict("os.environ", {"DISPLAY": "", "WAYLAND_DISPLAY": ""}, clear=False)
    @patch("core.fonts.get_os", return_value="linux")
    def test_headless_linux_skips_install(self, mock_os):
        """Should skip install on headless Linux and return True."""
        assert install_nerd_font("meslo") is True

    @patch.dict("os.environ", {"DISPLAY": ":0"})
    @patch("core.fonts.is_nerd_font_installed", return_value=False)
    @patch("core.fonts._install_via_omp", return_value=True)
    def test_prefers_omp(self, mock_omp, mock_check):
        """Should try oh-my-posh first."""
        assert install_nerd_font("meslo") is True
        mock_omp.assert_called_once_with("Meslo")

    @patch("core.fonts.is_nerd_font_installed", return_value=False)
    @patch("core.fonts._install_via_omp", return_value=False)
    @patch("core.fonts.get_os", return_value="macos")
    @patch("core.fonts._install_via_brew", return_value=True)
    def test_macos_brew_fallback(self, mock_brew, mock_os, mock_omp, mock_check):
        """Should fall back to brew on macOS."""
        assert install_nerd_font("meslo") is True
        mock_brew.assert_called_once_with("font-meslo-lg-nerd-font")

    @patch("core.fonts.is_nerd_font_installed", return_value=False)
    @patch("core.fonts._install_via_omp", return_value=False)
    @patch("core.fonts.get_os", return_value="windows")
    @patch("core.fonts._install_via_github", return_value=True)
    def test_github_fallback(self, mock_gh, mock_os, mock_omp, mock_check):
        """Should fall back to GitHub download."""
        assert install_nerd_font("meslo") is True
        mock_gh.assert_called_once_with("meslo")

    @patch("core.fonts.is_nerd_font_installed", return_value=False)
    @patch("core.fonts._install_via_omp", return_value=False)
    @patch("core.fonts.get_os", return_value="linux")
    @patch("core.fonts._install_via_github", return_value=False)
    @patch.dict("os.environ", {"DISPLAY": ":0"})
    def test_all_methods_fail(self, mock_gh, mock_os, mock_omp, mock_check):
        """Should return False when all install methods fail."""
        assert install_nerd_font("meslo") is False


class TestInstallViaOmp:
    """oh-my-posh font install strategy."""

    def test_not_available(self):
        from core.fonts import _install_via_omp

        with patch("core.fonts.is_available", return_value=False):
            assert _install_via_omp("Meslo") is False

    def test_success(self):
        from core.fonts import _install_via_omp

        with (
            patch("core.fonts.is_available", return_value=True),
            patch("core.fonts.subprocess.run") as mock_run,
        ):
            assert _install_via_omp("Meslo") is True
            mock_run.assert_called_once()

    def test_subprocess_error(self):
        import subprocess

        from core.fonts import _install_via_omp

        with (
            patch("core.fonts.is_available", return_value=True),
            patch(
                "core.fonts.subprocess.run",
                side_effect=subprocess.CalledProcessError(1, "omp"),
            ),
        ):
            assert _install_via_omp("Meslo") is False


class TestInstallViaBrew:
    """Homebrew cask strategy."""

    def test_not_macos(self):
        from core.fonts import _install_via_brew

        with patch("core.fonts.get_os", return_value="linux"):
            assert _install_via_brew("font-meslo-lg-nerd-font") is False

    def test_brew_not_available(self):
        from core.fonts import _install_via_brew

        with (
            patch("core.fonts.get_os", return_value="macos"),
            patch("core.fonts.is_available", return_value=False),
        ):
            assert _install_via_brew("font-meslo-lg-nerd-font") is False

    def test_brew_success(self):
        from core.fonts import _install_via_brew

        with (
            patch("core.fonts.get_os", return_value="macos"),
            patch("core.fonts.is_available", return_value=True),
            patch("core.fonts.subprocess.run") as mock_run,
        ):
            assert _install_via_brew("font-meslo-lg-nerd-font") is True
            mock_run.assert_called_once()

    def test_brew_subprocess_error(self):
        import subprocess

        from core.fonts import _install_via_brew

        with (
            patch("core.fonts.get_os", return_value="macos"),
            patch("core.fonts.is_available", return_value=True),
            patch(
                "core.fonts.subprocess.run",
                side_effect=subprocess.CalledProcessError(1, "brew"),
            ),
        ):
            assert _install_via_brew("font-meslo-lg-nerd-font") is False


class TestInstallViaGithub:
    """GitHub release download strategy."""

    def test_unknown_font(self):
        from core.fonts import _install_via_github

        assert _install_via_github("nonexistent") is False

    def test_download_failure(self, tmp_path: Path):
        from core.fonts import _install_via_github

        with (
            patch("core.fonts.get_os", return_value="windows"),
            patch("core.fonts.get_home_dir", return_value=tmp_path),
            patch("urllib.request.urlopen", side_effect=OSError("fail")),
        ):
            assert _install_via_github("meslo") is False

    def test_download_truncated(self, tmp_path: Path):
        """A truncated download (IncompleteRead, not an OSError) degrades to False."""
        import http.client

        from core.fonts import _install_via_github

        resp = MagicMock()
        resp.__enter__.return_value = resp
        resp.read.side_effect = http.client.IncompleteRead(partial=b"abc")

        with (
            patch("core.fonts.get_os", return_value="windows"),
            patch("core.fonts.get_home_dir", return_value=tmp_path),
            patch("urllib.request.urlopen", return_value=resp),
        ):
            assert _install_via_github("meslo") is False

    def test_success_windows(self, tmp_path: Path):
        import io as _io
        import zipfile as _zf

        from core.fonts import _install_via_github

        # Create a valid zip with a font file
        buf = _io.BytesIO()
        with _zf.ZipFile(buf, "w") as zf:
            zf.writestr("MesloLGSNerdFont-Regular.ttf", b"fakefont")
        zip_data = buf.getvalue()

        mock_resp = MagicMock()
        mock_resp.read.return_value = zip_data
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        dest = tmp_path / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts"

        with (
            patch("core.fonts.get_os", return_value="windows"),
            patch("core.fonts.get_home_dir", return_value=tmp_path),
            patch("urllib.request.urlopen", return_value=mock_resp),
        ):
            assert _install_via_github("meslo") is True
        assert (dest / "MesloLGSNerdFont-Regular.ttf").exists()

    def test_success_linux_with_fc_cache(self, tmp_path: Path):
        import io as _io
        import zipfile as _zf

        from core.fonts import _install_via_github

        buf = _io.BytesIO()
        with _zf.ZipFile(buf, "w") as zf:
            zf.writestr("FiraCodeNerdFont-Regular.ttf", b"fakefont")
        zip_data = buf.getvalue()

        mock_resp = MagicMock()
        mock_resp.read.return_value = zip_data
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with (
            patch("core.fonts.get_os", return_value="linux"),
            patch("core.fonts.get_home_dir", return_value=tmp_path),
            patch("urllib.request.urlopen", return_value=mock_resp),
            patch("core.fonts.is_available", return_value=True),
            patch("core.fonts.subprocess.run") as mock_run,
        ):
            assert _install_via_github("firacode") is True
            mock_run.assert_called_once()

    def test_bad_zip(self, tmp_path: Path):
        from core.fonts import _install_via_github

        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not a zip file"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with (
            patch("core.fonts.get_os", return_value="windows"),
            patch("core.fonts.get_home_dir", return_value=tmp_path),
            patch("urllib.request.urlopen", return_value=mock_resp),
        ):
            assert _install_via_github("meslo") is False

    def test_zip_no_fonts(self, tmp_path: Path):
        import io as _io
        import zipfile as _zf

        from core.fonts import _install_via_github

        buf = _io.BytesIO()
        with _zf.ZipFile(buf, "w") as zf:
            zf.writestr("README.md", b"no fonts here")
        zip_data = buf.getvalue()

        mock_resp = MagicMock()
        mock_resp.read.return_value = zip_data
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with (
            patch("core.fonts.get_os", return_value="windows"),
            patch("core.fonts.get_home_dir", return_value=tmp_path),
            patch("urllib.request.urlopen", return_value=mock_resp),
        ):
            assert _install_via_github("meslo") is False

    def test_macos_dest_dir(self, tmp_path: Path):
        import io as _io
        import zipfile as _zf

        from core.fonts import _install_via_github

        buf = _io.BytesIO()
        with _zf.ZipFile(buf, "w") as zf:
            zf.writestr("MesloLGSNerdFont-Regular.ttf", b"fakefont")
        zip_data = buf.getvalue()

        mock_resp = MagicMock()
        mock_resp.read.return_value = zip_data
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with (
            patch("core.fonts.get_os", return_value="macos"),
            patch("core.fonts.get_home_dir", return_value=tmp_path),
            patch("urllib.request.urlopen", return_value=mock_resp),
        ):
            assert _install_via_github("meslo") is True
        assert (tmp_path / "Library" / "Fonts" / "MesloLGSNerdFont-Regular.ttf").exists()


class TestFontDirs:
    """Platform-specific font directory resolution."""

    @patch("core.fonts.get_os", return_value="macos")
    @patch("core.fonts.get_home_dir", return_value=Path("/Users/test"))
    def test_macos_dirs(self, mock_home, mock_os):
        dirs = _font_dirs()
        assert Path("/Users/test/Library/Fonts") in dirs
        assert Path("/Library/Fonts") in dirs

    @patch("core.fonts.get_os", return_value="linux")
    @patch("core.fonts.get_home_dir", return_value=Path("/home/test"))
    def test_linux_dirs(self, mock_home, mock_os):
        dirs = _font_dirs()
        assert Path("/home/test/.local/share/fonts") in dirs
        assert Path("/usr/share/fonts") in dirs


class TestFcListFallback:
    """fc-list edge cases in is_nerd_font_installed."""

    @patch("core.fonts.get_os", return_value="linux")
    @patch("core.fonts.is_available", return_value=True)
    def test_fc_list_timeout(self, mock_avail, mock_os, tmp_path: Path):
        import subprocess

        with (
            patch("core.fonts._font_dirs", return_value=[tmp_path / "empty"]),
            patch(
                "core.fonts.subprocess.run",
                side_effect=subprocess.TimeoutExpired("fc-list", 10),
            ),
        ):
            assert is_nerd_font_installed("meslo") is False


class TestEnsureNerdFont:
    """Non-fatal wrapper for deploy integration."""

    @patch.dict("os.environ", {"DISPLAY": ":0"})
    def test_never_raises(self, tmp_path: Path):
        """ensure_nerd_font must never raise, even on unexpected errors."""
        with patch("core.fonts.load_settings", side_effect=RuntimeError("boom")):
            ensure_nerd_font(tmp_path)  # should not raise

    @patch.dict("os.environ", {"DISPLAY": ":0"})
    def test_skips_when_disabled(self, tmp_path: Path):
        settings = {"fonts": {"nerd_font": "meslo", "auto_install": False}}
        with patch("core.fonts.load_settings", return_value=settings):
            ensure_nerd_font(tmp_path)  # should return early

    @patch.dict("os.environ", {"DISPLAY": ":0"})
    def test_skips_when_already_installed(self, tmp_path: Path):
        settings = {"fonts": {"nerd_font": "meslo", "auto_install": True}}
        with (
            patch("core.fonts.load_settings", return_value=settings),
            patch("core.fonts.is_nerd_font_installed", return_value=True),
            patch("core.fonts.install_nerd_font") as mock_install,
        ):
            ensure_nerd_font(tmp_path)
            mock_install.assert_not_called()

    @patch.dict("os.environ", {"DISPLAY": ":0"})
    def test_calls_install_when_missing(self, tmp_path: Path):
        settings = {"fonts": {"nerd_font": "firacode", "auto_install": True}}
        with (
            patch("core.fonts.load_settings", return_value=settings),
            patch("core.fonts.is_nerd_font_installed", return_value=False),
            patch("core.fonts.install_nerd_font") as mock_install,
        ):
            ensure_nerd_font(tmp_path)
            mock_install.assert_called_once_with("firacode")

    @patch.dict("os.environ", {"DISPLAY": ":0"})
    def test_warns_on_unknown_font(self, tmp_path: Path):
        settings = {"fonts": {"nerd_font": "comic_sans", "auto_install": True}}
        with patch("core.fonts.load_settings", return_value=settings):
            ensure_nerd_font(tmp_path)  # should not raise

    @patch.dict("os.environ", {"DISPLAY": "", "WAYLAND_DISPLAY": ""}, clear=False)
    @patch("core.fonts.get_os", return_value="linux")
    def test_skips_on_headless_linux(self, mock_os, tmp_path: Path):
        """Should skip font install on headless Linux without calling load_settings."""
        with patch("core.fonts.install_nerd_font") as mock_install:
            ensure_nerd_font(tmp_path)
            mock_install.assert_not_called()

    def test_skips_when_env_var_set(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("MY_SHELL_SKIP_FONTS", "1")
        with patch("core.fonts.load_settings") as mock_settings:
            ensure_nerd_font(tmp_path)
            mock_settings.assert_not_called()


class TestDoctorNerdFontCheck:
    """Doctor health check for Nerd Font."""

    def test_pass_when_installed(self, tmp_path: Path):
        from core.doctor import _check_nerd_font

        settings = {"fonts": {"nerd_font": "meslo"}}
        with (
            patch("core.doctor.load_settings", return_value=settings),
            patch("core.fonts.is_nerd_font_installed", return_value=True),
        ):
            result = _check_nerd_font(tmp_path)
            assert result.status == "pass"

    def test_warn_when_missing(self, tmp_path: Path):
        from core.doctor import _check_nerd_font

        settings = {"fonts": {"nerd_font": "meslo"}}
        with (
            patch("core.doctor.load_settings", return_value=settings),
            patch("core.fonts.is_nerd_font_installed", return_value=False),
            patch("core.fonts.is_headless_linux", return_value=False),
        ):
            result = _check_nerd_font(tmp_path)
            assert result.status == "warn"
            assert "install-fonts" in result.fix

    def test_info_when_missing_on_headless_linux(self, tmp_path: Path):
        from core.doctor import _check_nerd_font

        settings = {"fonts": {"nerd_font": "meslo"}}
        with (
            patch("core.doctor.load_settings", return_value=settings),
            patch("core.fonts.is_nerd_font_installed", return_value=False),
            patch("core.fonts.is_headless_linux", return_value=True),
        ):
            result = _check_nerd_font(tmp_path)
            assert result.status == "info"
            assert "no graphical environment" in result.message

    def test_warn_unknown_font(self, tmp_path: Path):
        from core.doctor import _check_nerd_font

        settings = {"fonts": {"nerd_font": "nonexistent"}}
        with patch("core.doctor.load_settings", return_value=settings):
            result = _check_nerd_font(tmp_path)
            assert result.status == "warn"


class TestInstallFontsCli:
    """CLI install-fonts command wiring."""

    def test_command_exists(self):
        from typer.testing import CliRunner

        from core.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["install-fonts", "--help"])
        assert result.exit_code == 0
        assert "Nerd Font" in result.output

    def test_default_font_from_settings(self):
        from typer.testing import CliRunner

        from core.cli import app

        runner = CliRunner()
        with (
            patch("core.fonts.install_nerd_font", return_value=True) as mock_install,
            patch(
                "core.config.load_settings",
                return_value={"fonts": {"nerd_font": "firacode", "auto_install": True}},
            ),
        ):
            result = runner.invoke(app, ["install-fonts"])
            assert result.exit_code == 0
            mock_install.assert_called_once_with("firacode", force=False)

    def test_explicit_font_argument(self):
        from typer.testing import CliRunner

        from core.cli import app

        runner = CliRunner()
        with patch("core.fonts.install_nerd_font", return_value=True) as mock_install:
            result = runner.invoke(app, ["install-fonts", "firacode"])
            assert result.exit_code == 0
            mock_install.assert_called_once_with("firacode", force=False)

    def test_unknown_font_exits_with_error(self):
        from typer.testing import CliRunner

        from core.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["install-fonts", "comic_sans"])
        assert result.exit_code == 1


class TestFontRegistry:
    """Basic font registry sanity checks."""

    def test_all_fonts_have_required_keys(self):
        required = {"name", "display", "glob", "brew_cask", "github_asset"}
        for key, meta in NERD_FONTS.items():
            assert required <= set(meta.keys()), f"Font '{key}' missing keys"

    def test_meslo_is_default(self):
        assert "meslo" in NERD_FONTS

    def test_firacode_is_available(self):
        assert "firacode" in NERD_FONTS
