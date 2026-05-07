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
