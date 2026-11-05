import subprocess
import sys


class TestDredgeDir:
    def test_from_d_flag(self, tmp_path):
        result = subprocess.run(
            [sys.executable, "-m", "dredge", "-d", str(tmp_path), "import", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_from_env_var(self, tmp_path):
        result = subprocess.run(
            [sys.executable, "-m", "dredge", "import", "--help"],
            capture_output=True,
            text=True,
            env={**{"PATH": "/usr/bin", "HOME": str(tmp_path)}, "DREDGE_DIR": str(tmp_path)},
        )
        assert result.returncode == 0

    def test_fatal_when_missing(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "dredge",
                "import",
                "https://example.com/view/gs/bucket/1234",
            ],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin", "HOME": "/tmp"},
        )
        assert result.returncode != 0
        assert "DREDGE_DIR" in result.stderr


class TestImportCommand:
    def test_import_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "dredge", "import", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "--auto-must-gather" in result.stdout


class TestHistoryFlags:
    def test_failed_default_true(self):
        result = subprocess.run(
            [sys.executable, "-m", "dredge", "history", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "--failed" in result.stdout
        assert "--no-failed" in result.stdout


class TestGithubTransportSelection:
    def test_flag_env_and_default_precedence(self, monkeypatch):
        import pytest

        from dredge.cli import parse_args
        from dredge.discovery import GitHubTransport

        for environment, flag, expected in [
            (None, None, GitHubTransport.HTTP),
            ("gh", None, GitHubTransport.GH),
            ("http", None, GitHubTransport.HTTP),
            ("gh", "http", GitHubTransport.HTTP),
            ("http", "gh", GitHubTransport.GH),
            ("invalid", "gh", GitHubTransport.GH),
        ]:
            monkeypatch.delenv("DREDGE_GITHUB_TRANSPORT", raising=False)
            if environment is not None:
                monkeypatch.setenv("DREDGE_GITHUB_TRANSPORT", environment)
            argv = ["dredge", "pr", "https://github.com/org/repo/pull/1"]
            if flag:
                argv.extend(["--github-transport", flag])
            monkeypatch.setattr(sys, "argv", argv)
            assert parse_args().github_transport == expected

        for environment in ["invalid", "", "GH"]:
            monkeypatch.setenv("DREDGE_GITHUB_TRANSPORT", environment)
            monkeypatch.setattr(sys, "argv", ["dredge", "pr", "https://github.com/org/repo/pull/1"])
            with pytest.raises(SystemExit) as exc:
                parse_args()
            assert exc.value.code == 2

    def test_invalid_flag_cannot_fall_back_to_valid_env(self, monkeypatch):
        import pytest

        from dredge.cli import parse_args

        monkeypatch.setenv("DREDGE_GITHUB_TRANSPORT", "gh")
        monkeypatch.setattr(sys, "argv", ["dredge", "pr", "url", "--github-transport", "invalid"])
        with pytest.raises(SystemExit) as exc:
            parse_args()
        assert exc.value.code == 2

    def test_other_commands_ignore_transport_env(self, monkeypatch):
        from dredge.cli import parse_args

        monkeypatch.setenv("DREDGE_GITHUB_TRANSPORT", "invalid")
        monkeypatch.setattr(sys, "argv", ["dredge", "import", "url"])
        assert parse_args().command == "import"

    def test_pr_help_documents_transport(self):
        result = subprocess.run(
            [sys.executable, "-m", "dredge", "pr", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "--github-transport {http,gh}" in result.stdout
        assert "DREDGE_GITHUB_TRANSPORT" in result.stdout
        assert "Example:" in result.stdout
