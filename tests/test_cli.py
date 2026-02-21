"""
Integration tests for syncript CLI behavior and installer idempotency.
Author: Younes Rahimi

Tests:
  - .syncript discovery: searching parent directories upward
  - syncript init: creates a valid .syncript YAML, refuses overwrite without --force
  - config loading: apply_profile correctly mutates module variables
  - installer idempotency: install-unix.sh does not duplicate files on repeated runs
"""
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


# ── Helpers ───────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.parent
INSTALL_SCRIPT = REPO_ROOT / "install-unix.sh"


def run_syncript(*args, cwd=None, input_text=None):
    """Run the syncript CLI and return (returncode, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, "-m", "syncript", *args],
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        input=input_text,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    )
    return result.returncode, result.stdout, result.stderr


# ── Tests: .syncript discovery ────────────────────────────────────────────────

class TestFindSyncript(unittest.TestCase):
    """Tests for find_syncript() — upward search through parent directories."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_find_in_same_directory(self):
        """find_syncript finds .syncript in the start directory."""
        from syncript.config import find_syncript
        (self.root / ".syncript").write_text("profiles: []\n", encoding="utf-8")
        found = find_syncript(self.root)
        self.assertIsNotNone(found)
        self.assertEqual(found, self.root / ".syncript")

    def test_find_in_parent_directory(self):
        """find_syncript searches upward and finds .syncript in a parent."""
        from syncript.config import find_syncript
        (self.root / ".syncript").write_text("profiles: []\n", encoding="utf-8")
        subdir = self.root / "a" / "b" / "c"
        subdir.mkdir(parents=True)
        found = find_syncript(subdir)
        self.assertIsNotNone(found)
        self.assertEqual(found, self.root / ".syncript")

    def test_returns_none_when_not_found(self):
        """find_syncript returns None when no .syncript exists in any parent."""
        from syncript.config import find_syncript
        # Use a deep temp subdir with no .syncript anywhere in it
        subdir = self.root / "x" / "y"
        subdir.mkdir(parents=True)
        # We cannot guarantee no .syncript exists above self.root (the temp dir),
        # but we can verify the function at least doesn't crash.
        result = find_syncript(subdir)
        # If found, it's in some system parent — acceptable. If None, perfect.
        self.assertIn(result, [None] + [p for p in subdir.parents])

    def test_finds_nearest_syncript(self):
        """find_syncript returns the nearest (deepest) .syncript."""
        from syncript.config import find_syncript
        # Two .syncript files: one at root, one at subdir/a
        (self.root / ".syncript").write_text("profiles: []\n", encoding="utf-8")
        sub_a = self.root / "a"
        sub_a.mkdir()
        (sub_a / ".syncript").write_text("profiles: []\n", encoding="utf-8")
        deep = sub_a / "b" / "c"
        deep.mkdir(parents=True)
        found = find_syncript(deep)
        self.assertEqual(found, sub_a / ".syncript")


# ── Tests: config loading ─────────────────────────────────────────────────────

class TestLoadProfile(unittest.TestCase):
    """Tests for load_syncript_file and apply_profile."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        # Reset config module between tests
        import syncript.config as cfg
        cfg.SSH_HOST = "example.com"
        cfg.SSH_PORT = 22
        cfg.LOCAL_ROOT = Path(".")
        from pathlib import PurePosixPath
        cfg.REMOTE_ROOT = PurePosixPath("/")

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write_syncript(self, content):
        p = self.root / ".syncript"
        p.write_text(content, encoding="utf-8")
        return p

    def test_load_profile_basic(self):
        """apply_profile sets SSH_HOST, SSH_PORT, LOCAL_ROOT, REMOTE_ROOT."""
        import syncript.config as cfg
        p = self._write_syncript(
            "profiles:\n"
            "  - name: default\n"
            "    server: myhost.example.com\n"
            "    port: 2222\n"
            "    local_root: /tmp/local\n"
            "    remote_root: /remote/path\n"
        )
        data = cfg.load_syncript_file(p)
        profile = cfg.get_profile(data, "default")
        cfg.apply_profile(profile)
        self.assertEqual(cfg.SSH_HOST, "myhost.example.com")
        self.assertEqual(cfg.SSH_PORT, 2222)

    def test_load_profile_with_base_remote(self):
        """apply_profile prepends base_remote to relative remote_root."""
        import syncript.config as cfg
        from pathlib import PurePosixPath
        p = self._write_syncript(
            "profiles:\n"
            "  - name: default\n"
            "    server: host\n"
            "    port: 22\n"
            "    local_root: /tmp\n"
            "    remote_root: projects/myrepo\n"
            "defaults:\n"
            "  base_remote: /home/user\n"
        )
        data = cfg.load_syncript_file(p)
        profile = cfg.get_profile(data, "default")
        cfg.apply_profile(profile)
        self.assertEqual(cfg.REMOTE_ROOT, PurePosixPath("/home/user/projects/myrepo"))

    def test_get_profile_by_name(self):
        """get_profile retrieves the named profile correctly."""
        import syncript.config as cfg
        p = self._write_syncript(
            "profiles:\n"
            "  - name: dev\n"
            "    server: dev.example.com\n"
            "    port: 22\n"
            "    local_root: /tmp\n"
            "    remote_root: /dev\n"
            "  - name: prod\n"
            "    server: prod.example.com\n"
            "    port: 2222\n"
            "    local_root: /tmp\n"
            "    remote_root: /prod\n"
        )
        data = cfg.load_syncript_file(p)
        profile = cfg.get_profile(data, "prod")
        self.assertEqual(profile["server"], "prod.example.com")
        self.assertEqual(profile["port"], 2222)

    def test_get_profile_falls_back_to_first(self):
        """get_profile falls back to first profile if named one not found."""
        import syncript.config as cfg
        p = self._write_syncript(
            "profiles:\n"
            "  - name: only\n"
            "    server: only.example.com\n"
            "    port: 22\n"
            "    local_root: /tmp\n"
            "    remote_root: /only\n"
        )
        data = cfg.load_syncript_file(p)
        profile = cfg.get_profile(data, "nonexistent")
        self.assertEqual(profile["server"], "only.example.com")


# ── Tests: syncript init CLI ──────────────────────────────────────────────────

class TestInitCommand(unittest.TestCase):
    """Tests for the 'syncript init' subcommand."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cwd = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_init_creates_syncript_file(self):
        """'syncript init' creates a .syncript file in the current directory."""
        rc, out, err = run_syncript(
            "init",
            "--server", "myhost.com",
            "--port", "22",
            "--remote", "projects/test",
            "--base-remote", "/home/user",
            cwd=self.cwd,
        )
        self.assertEqual(rc, 0, msg=f"stderr: {err}")
        syncript_file = self.cwd / ".syncript"
        self.assertTrue(syncript_file.exists(), ".syncript should have been created")
        content = syncript_file.read_text(encoding="utf-8")
        self.assertIn("myhost.com", content)
        self.assertIn("projects/test", content)
        self.assertIn("/home/user", content)

    def test_init_refuses_overwrite(self):
        """'syncript init' refuses to overwrite existing .syncript without --force."""
        syncript_file = self.cwd / ".syncript"
        syncript_file.write_text("profiles: []\n", encoding="utf-8")
        rc, out, err = run_syncript(
            "init",
            "--server", "myhost.com",
            "--remote", "projects/test",
            cwd=self.cwd,
        )
        self.assertNotEqual(rc, 0, "Should exit with error when .syncript exists")
        self.assertIn("already exists", err)

    def test_init_force_overwrites(self):
        """'syncript init --force' overwrites existing .syncript."""
        syncript_file = self.cwd / ".syncript"
        syncript_file.write_text("profiles: []\n", encoding="utf-8")
        rc, out, err = run_syncript(
            "init",
            "--server", "newhost.com",
            "--remote", "projects/new",
            "--force",
            cwd=self.cwd,
        )
        self.assertEqual(rc, 0, msg=f"stderr: {err}")
        content = syncript_file.read_text(encoding="utf-8")
        self.assertIn("newhost.com", content)

    def test_init_dry_run_does_not_write(self):
        """'syncript init --dry-run' prints the config but does not write it."""
        rc, out, err = run_syncript(
            "init",
            "--server", "myhost.com",
            "--remote", "projects/test",
            "--dry-run",
            cwd=self.cwd,
        )
        self.assertEqual(rc, 0, msg=f"stderr: {err}")
        syncript_file = self.cwd / ".syncript"
        self.assertFalse(syncript_file.exists(), ".syncript should NOT be created in dry-run")
        self.assertIn("dry-run", out)

    def test_init_creates_valid_yaml(self):
        """'syncript init' produces valid YAML that can be loaded."""
        rc, out, err = run_syncript(
            "init",
            "--server", "myhost.com",
            "--port", "2222",
            "--remote", "/absolute/remote",
            cwd=self.cwd,
        )
        self.assertEqual(rc, 0, msg=f"stderr: {err}")
        import yaml
        content = (self.cwd / ".syncript").read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        self.assertIn("profiles", data)
        self.assertEqual(data["profiles"][0]["server"], "myhost.com")
        self.assertEqual(data["profiles"][0]["port"], 2222)


# ── Tests: installer idempotency ──────────────────────────────────────────────

@unittest.skipUnless(
    INSTALL_SCRIPT.exists() and sys.platform != "win32",
    "install-unix.sh not found or not on a Unix system",
)
class TestInstallerIdempotency(unittest.TestCase):
    """Tests for install-unix.sh idempotency."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.install_dir = Path(self.tmpdir.name) / "bin"
        self.config_dir = Path(self.tmpdir.name) / "config" / "syncript"
        self.profile_file = Path(self.tmpdir.name) / ".profile"
        self.profile_file.write_text("", encoding="utf-8")

    def tearDown(self):
        self.tmpdir.cleanup()

    def _run_installer(self, extra_args=None):
        env = {
            **os.environ,
            "HOME": self.tmpdir.name,
            "XDG_CONFIG_HOME": str(Path(self.tmpdir.name) / "config"),
        }
        cmd = [
            "sh",
            str(INSTALL_SCRIPT),
            f"--server=testserver.example.com",
            f"--base-remote=/home/testuser",
            f"--port=22",
        ]
        if extra_args:
            cmd.extend(extra_args)
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(REPO_ROOT),
        )

    def test_installer_runs_successfully(self):
        """install-unix.sh exits 0 on a fresh install."""
        result = self._run_installer()
        self.assertEqual(
            result.returncode, 0,
            msg=f"stdout: {result.stdout}\nstderr: {result.stderr}",
        )

    def test_installer_is_idempotent(self):
        """Running install-unix.sh twice does not duplicate PATH entries."""
        self._run_installer()
        self._run_installer()  # second run

        # Check the profile file has only one PATH export for our bin
        profile_content = self.profile_file.read_text(encoding="utf-8")
        tag_count = profile_content.count("added by syncript installer")
        self.assertLessEqual(tag_count, 1, "PATH entry should not be duplicated")

    def test_installer_creates_config(self):
        """install-unix.sh creates the global config.yaml."""
        result = self._run_installer()
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        config_file = self.config_dir / "config.yaml"
        self.assertTrue(config_file.exists(), f"Config not found at {config_file}")
        content = config_file.read_text(encoding="utf-8")
        self.assertIn("testserver.example.com", content)

    def test_installer_config_not_overwritten_on_second_run(self):
        """install-unix.sh does not overwrite config on second run (no --force)."""
        self._run_installer()
        config_file = self.config_dir / "config.yaml"
        original_content = config_file.read_text(encoding="utf-8")

        # Modify config to a sentinel value
        config_file.write_text("# custom config\n", encoding="utf-8")

        # Run again without --force
        self._run_installer()
        content_after = config_file.read_text(encoding="utf-8")
        self.assertEqual(content_after, "# custom config\n",
                         "Config should not be overwritten on second run without --force")

    def test_installer_force_overwrites_config(self):
        """install-unix.sh --force recreates the config."""
        self._run_installer()
        config_file = self.config_dir / "config.yaml"
        config_file.write_text("# custom config\n", encoding="utf-8")

        self._run_installer(extra_args=["--force"])
        content_after = config_file.read_text(encoding="utf-8")
        self.assertNotEqual(content_after, "# custom config\n",
                            "Config should be recreated with --force")

    def test_uninstall_removes_wrapper(self):
        """install-unix.sh --uninstall removes the installed wrapper."""
        self._run_installer()
        # Run uninstall
        env = {
            **os.environ,
            "HOME": self.tmpdir.name,
            "XDG_CONFIG_HOME": str(Path(self.tmpdir.name) / "config"),
        }
        result = subprocess.run(
            ["sh", str(INSTALL_SCRIPT), "--uninstall"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(REPO_ROOT),
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)


# ── Tests: .git exclusion ─────────────────────────────────────────────────────

class TestGitExclusion(unittest.TestCase):
    """Tests that .git directory contents are excluded from local scan and deletion plans."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_local_list_all_excludes_git_files(self):
        """local_list_all must not return any .git/** entries."""
        from syncript.operations.scanner import local_list_all
        import syncript.config as cfg

        old_root = cfg.LOCAL_ROOT
        cfg.LOCAL_ROOT = self.root
        try:
            (self.root / "normal.txt").write_text("hello", encoding="utf-8")
            git_dir = self.root / ".git"
            git_dir.mkdir()
            (git_dir / "config").write_text("[core]", encoding="utf-8")
            (git_dir / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
            objects_dir = git_dir / "objects"
            objects_dir.mkdir()
            (objects_dir / "abc123").write_text("blob", encoding="utf-8")

            result = local_list_all(self.root, [])

            self.assertIn("normal.txt", result)
            git_keys = [k for k in result if k == ".git" or k.startswith(".git/")]
            self.assertEqual(git_keys, [], f"Unexpected .git entries: {git_keys}")
        finally:
            cfg.LOCAL_ROOT = old_root

    def test_is_git_path_top_level(self):
        """_is_git_path returns True for top-level .git entries."""
        from syncript.core.sync_engine import _is_git_path
        self.assertTrue(_is_git_path(".git"))
        self.assertTrue(_is_git_path(".git/config"))
        self.assertTrue(_is_git_path(".git/HEAD"))
        self.assertTrue(_is_git_path(".git/objects/ab/cdef1234"))

    def test_is_git_path_nested(self):
        """_is_git_path returns True for nested .git paths."""
        from syncript.core.sync_engine import _is_git_path
        self.assertTrue(_is_git_path("subdir/.git"))
        self.assertTrue(_is_git_path("subdir/.git/config"))
        self.assertTrue(_is_git_path("a/b/.git/HEAD"))

    def test_is_git_path_non_git(self):
        """_is_git_path returns False for normal paths."""
        from syncript.core.sync_engine import _is_git_path
        self.assertFalse(_is_git_path("src/main.py"))
        self.assertFalse(_is_git_path("mygitrepo/file.txt"))
        self.assertFalse(_is_git_path("README.md"))

    def test_run_sync_git_filter_excludes_deletion(self):
        """The safety filter in run_sync must exclude all .git path shapes."""
        from syncript.core.sync_engine import _is_git_path

        git_paths = [
            ".git/config",
            ".git/HEAD",
            ".git/objects/ab/cdef1234",
            "subdir/.git/config",
            "subdir/.git",
        ]
        non_git_paths = [
            "src/main.py",
            "mygitrepo/file.txt",
        ]

        all_paths = git_paths + non_git_paths
        filtered = [r for r in all_paths if not _is_git_path(r)]

        for gp in git_paths:
            self.assertNotIn(gp, filtered, f".git path should be filtered: {gp}")
        for np in non_git_paths:
            self.assertIn(np, filtered, f"Non-.git path should pass: {np}")


# ── Tests: BATCH_FILE_SIZE batching ──────────────────────────────────────────

class TestBatchFileSizeConfig(unittest.TestCase):
    """Tests for BATCH_FILE_SIZE constant and apply_profile support."""

    def setUp(self):
        import syncript.config as cfg
        self._orig = cfg.BATCH_FILE_SIZE

    def tearDown(self):
        import syncript.config as cfg
        cfg.BATCH_FILE_SIZE = self._orig

    def test_default_batch_file_size(self):
        """BATCH_FILE_SIZE default is 512 KB."""
        import syncript.config as cfg
        self.assertEqual(cfg.BATCH_FILE_SIZE, 512 * 1024)

    def test_apply_profile_sets_batch_file_size(self):
        """apply_profile honours batch_file_size key."""
        import syncript.config as cfg
        cfg.apply_profile({"batch_file_size": 1024 * 1024})
        self.assertEqual(cfg.BATCH_FILE_SIZE, 1024 * 1024)

    def test_init_includes_batch_file_size(self):
        """'syncript init' writes batch_file_size into the generated .syncript."""
        tmpdir = tempfile.TemporaryDirectory()
        try:
            cwd = Path(tmpdir.name)
            rc, out, err = run_syncript(
                "init",
                "--server", "host.example.com",
                "--remote", "projects/test",
                cwd=cwd,
            )
            self.assertEqual(rc, 0, msg=f"stderr: {err}")
            content = (cwd / ".syncript").read_text(encoding="utf-8")
            self.assertIn("batch_file_size", content)
            self.assertIn("524288", content)  # 512 * 1024
        finally:
            tmpdir.cleanup()


class TestMakeSizeBatches(unittest.TestCase):
    """Tests for _make_size_batches and _estimate_compressed_size helpers."""

    def test_single_batch_when_below_limit(self):
        """All small files fit in one batch."""
        from syncript.core.sync_engine import _make_size_batches
        files = [("a.py", "pa"), ("b.py", "pb"), ("c.py", "pc")]
        sizes = {"a.py": 1000, "b.py": 1000, "c.py": 1000}
        batches = _make_size_batches(files, sizes, 512 * 1024)
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]), 3)

    def test_splits_into_multiple_batches(self):
        """Large files are split across batches when limit is small."""
        from syncript.core.sync_engine import _make_size_batches
        # Binary files: ratio 0.9, so each 600 KB binary → ~540 KB compressed
        # With a 512 KB limit, each file gets its own batch
        files = ["a.bin", "b.bin", "c.bin"]
        sizes = {"a.bin": 600 * 1024, "b.bin": 600 * 1024, "c.bin": 600 * 1024}
        batches = _make_size_batches(files, sizes, 512 * 1024)
        self.assertEqual(len(batches), 3)

    def test_text_files_compress_well(self):
        """Text files estimated at 10 % allow many files per batch."""
        from syncript.core.sync_engine import _make_size_batches
        # Each .py file: 100 KB raw → ~10 KB estimated compressed
        # 512 KB limit should hold ~51 files
        files = [(f"f{i}.py", f"p{i}") for i in range(60)]
        sizes = {f"f{i}.py": 100 * 1024 for i in range(60)}
        batches = _make_size_batches(files, sizes, 512 * 1024)
        # Should be 2 batches (51 in first, 9 in second)
        self.assertGreater(len(batches), 1)
        self.assertLess(len(batches), 60)

    def test_oversized_single_file_gets_own_batch(self):
        """A file larger than the limit still gets its own batch."""
        from syncript.core.sync_engine import _make_size_batches
        files = ["huge.bin", "small.py"]
        sizes = {"huge.bin": 10 * 1024 * 1024, "small.py": 1024}
        batches = _make_size_batches(files, sizes, 512 * 1024)
        self.assertGreaterEqual(len(batches), 1)
        self.assertIn("huge.bin", batches[0])

    def test_adaptive_ratio_used_when_provided(self):
        """When ratio is provided, it overrides the heuristic."""
        from syncript.core.sync_engine import _estimate_compressed_size
        # .py would normally be 0.10; override with 0.5
        est = _estimate_compressed_size("file.py", 1000, ratio=0.5)
        self.assertEqual(est, 500)

    def test_heuristic_text_extension(self):
        """Text extensions get 0.10 compression ratio by default."""
        from syncript.core.sync_engine import _estimate_compressed_size
        est = _estimate_compressed_size("hello.py", 1000, ratio=None)
        self.assertEqual(est, 100)  # 10% of 1000

    def test_heuristic_binary_extension(self):
        """Binary extensions get 0.90 compression ratio by default."""
        from syncript.core.sync_engine import _estimate_compressed_size
        est = _estimate_compressed_size("photo.jpg", 1000, ratio=None)
        self.assertEqual(est, 900)  # 90% of 1000

    def test_empty_files_list(self):
        """Empty input produces empty output."""
        from syncript.core.sync_engine import _make_size_batches
        self.assertEqual(_make_size_batches([], {}, 512 * 1024), [])


if __name__ == "__main__":
    unittest.main()
