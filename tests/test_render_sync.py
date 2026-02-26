"""Tests for render_sync.py"""
from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

# Ensure tools/ is importable
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from render_sync import (
    Config,
    MarkersConfig,
    ModificationsConfig,
    RenderConfig,
    TransformConfig,
    apply_copy_file,
    apply_delete_block,
    apply_insert_after,
    apply_replace_block,
    collect_files,
    load_config,
    marker_end,
    marker_start,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_env(tmp_path: Path):
    """Create a minimal file structure for tests."""
    source = tmp_path / "vendor-source" / "chart"
    source.mkdir(parents=True)
    target = tmp_path / "rendered" / "chart"
    target.mkdir(parents=True)
    mods = tmp_path / "mods"
    mods.mkdir()
    return tmp_path, source, target, mods


@pytest.fixture
def default_markers():
    return MarkersConfig()


def make_transform(**kwargs) -> TransformConfig:
    defaults = dict(id="test-id", type="copy_file", target_file="templates/test.yaml")
    defaults.update(kwargs)
    return TransformConfig(**defaults)


# ---------------------------------------------------------------------------
# collect_files
# ---------------------------------------------------------------------------

class TestCollectFiles:
    def test_include_all(self, tmp_path):
        (tmp_path / "a.yaml").write_text("a")
        (tmp_path / "b.yaml").write_text("b")
        files = collect_files(tmp_path, ["**"], [])
        assert len(files) == 2

    def test_exclude_pattern(self, tmp_path):
        (tmp_path / "keep.yaml").write_text("k")
        tests = tmp_path / "templates" / "tests"
        tests.mkdir(parents=True)
        (tests / "ignore.yaml").write_text("i")
        files = collect_files(tmp_path, ["**"], ["templates/tests/**"])
        names = [f.name for f in files]
        assert "keep.yaml" in names
        assert "ignore.yaml" not in names

    def test_include_pattern(self, tmp_path):
        (tmp_path / "Chart.yaml").write_text("c")
        (tmp_path / "values.yaml").write_text("v")
        (tmp_path / "README.md").write_text("r")
        files = collect_files(tmp_path, ["*.yaml"], [])
        names = [f.name for f in files]
        assert "Chart.yaml" in names
        assert "values.yaml" in names
        assert "README.md" not in names


# ---------------------------------------------------------------------------
# copy_file
# ---------------------------------------------------------------------------

class TestCopyFile:
    def test_copies_from_source_file(self, tmp_path):
        target_root = tmp_path / "rendered"
        target_root.mkdir()
        source_dir = tmp_path / "mods"
        source_dir.mkdir()
        src = source_dir / "myfile.tpl"
        src.write_text("hello world\n")

        t = make_transform(
            type="copy_file",
            target_file="templates/myfile.tpl",
            source_file="mods/myfile.tpl",
        )
        apply_copy_file(t, target_root, tmp_path)
        result = (target_root / "templates" / "myfile.tpl").read_text()
        assert result == "hello world\n"

    def test_creates_intermediate_dirs(self, tmp_path):
        target_root = tmp_path / "rendered"
        target_root.mkdir()
        source_dir = tmp_path / "mods"
        source_dir.mkdir()
        (source_dir / "x.tpl").write_text("x")

        t = make_transform(
            type="copy_file",
            target_file="a/b/c/x.tpl",
            source_file="mods/x.tpl",
        )
        apply_copy_file(t, target_root, tmp_path)
        assert (target_root / "a" / "b" / "c" / "x.tpl").exists()

    def test_overwrites_existing(self, tmp_path):
        target_root = tmp_path / "rendered"
        target_root.mkdir()
        (target_root / "f.tpl").write_text("old")
        src = tmp_path / "new.tpl"
        src.write_text("new content")

        t = make_transform(type="copy_file", target_file="f.tpl", source_file="new.tpl")
        apply_copy_file(t, target_root, tmp_path)
        assert (target_root / "f.tpl").read_text() == "new content"


# ---------------------------------------------------------------------------
# insert_after
# ---------------------------------------------------------------------------

class TestInsertAfter:
    def test_inserts_block_with_markers(self, tmp_path, default_markers):
        target_root = tmp_path / "rendered"
        target_root.mkdir()
        f = target_root / "dep.yaml"
        f.write_text("line1\nspec:\n  containers:\n")

        t = make_transform(
            id="my-block",
            type="insert_after",
            target_file="dep.yaml",
            match={"text": "spec:", "occurrence": 1},
            yaml_value="  initContainers: []\n",
        )
        apply_insert_after(t, target_root, tmp_path, default_markers)
        content = f.read_text()
        assert "# BEGIN custom-block: my-block" in content
        assert "# END custom-block: my-block" in content
        assert "initContainers" in content

    def test_idempotent_does_not_duplicate(self, tmp_path, default_markers):
        target_root = tmp_path / "rendered"
        target_root.mkdir()
        f = target_root / "dep.yaml"
        f.write_text("line1\nspec:\n  containers:\n")

        t = make_transform(
            id="my-block",
            type="insert_after",
            target_file="dep.yaml",
            match={"text": "spec:", "occurrence": 1},
            yaml_value="  initContainers: []\n",
        )
        # Run twice
        apply_insert_after(t, target_root, tmp_path, default_markers)
        apply_insert_after(t, target_root, tmp_path, default_markers)
        content = f.read_text()
        assert content.count("# BEGIN custom-block: my-block") == 1

    def test_match_not_found_raises(self, tmp_path, default_markers):
        target_root = tmp_path / "rendered"
        target_root.mkdir()
        (target_root / "dep.yaml").write_text("line1\nline2\n")

        t = make_transform(
            id="block",
            type="insert_after",
            target_file="dep.yaml",
            match={"text": "nonexistent", "occurrence": 1},
            yaml_value="extra: line\n",
        )
        with pytest.raises(SystemExit):
            apply_insert_after(t, target_root, tmp_path, default_markers)


# ---------------------------------------------------------------------------
# replace_block
# ---------------------------------------------------------------------------

class TestReplaceBlock:
    def test_replaces_block(self, tmp_path, default_markers):
        target_root = tmp_path / "rendered"
        target_root.mkdir()
        f = target_root / "dep.yaml"
        f.write_text(
            "spec:\n"
            "  containers:\n"
            "          env:\n"
            "            - name: FOO\n"
            "              value: old\n"
            "          ports:\n"
            "            - containerPort: 80\n"
        )
        mods = tmp_path / "mods"
        mods.mkdir()
        block_file = mods / "env.block"
        block_file.write_text(
            "          env:\n"
            "            - name: FOO\n"
            "              value: new\n"
        )

        t = make_transform(
            id="replace-env",
            type="replace_block",
            target_file="dep.yaml",
            match={"start": "          env:", "end": "          ports:", "occurrence": 1},
            source_file="mods/env.block",
        )
        apply_replace_block(t, target_root, tmp_path, default_markers)
        content = f.read_text()
        assert "value: new" in content
        assert "value: old" not in content
        # end anchor still present
        assert "ports:" in content

    def test_start_not_found_raises(self, tmp_path, default_markers):
        target_root = tmp_path / "rendered"
        target_root.mkdir()
        (target_root / "dep.yaml").write_text("line1\nline2\n")
        mods = tmp_path / "mods"
        mods.mkdir()
        (mods / "b.block").write_text("replacement\n")

        t = make_transform(
            id="r",
            type="replace_block",
            target_file="dep.yaml",
            match={"start": "MISSING_START", "end": "MISSING_END", "occurrence": 1},
            source_file="mods/b.block",
        )
        with pytest.raises(SystemExit):
            apply_replace_block(t, target_root, tmp_path, default_markers)


# ---------------------------------------------------------------------------
# delete_block
# ---------------------------------------------------------------------------

class TestDeleteBlock:
    def test_deletes_exact_block(self, tmp_path):
        target_root = tmp_path / "rendered"
        target_root.mkdir()
        f = target_root / "dep.yaml"
        f.write_text(
            "line1\n"
            "          env:\n"
            "            - name: WORKER\n"
            "              value: bar\n"
            "line2\n"
        )
        mods = tmp_path / "mods"
        mods.mkdir()
        block = mods / "del.block"
        block.write_text(
            "          env:\n"
            "            - name: WORKER\n"
            "              value: bar\n"
        )

        t = make_transform(
            id="del-env",
            type="delete_block",
            target_file="dep.yaml",
            source_file="mods/del.block",
        )
        apply_delete_block(t, target_root, tmp_path)
        content = f.read_text()
        assert "WORKER" not in content
        assert "line1" in content
        assert "line2" in content

    def test_block_not_found_warns(self, tmp_path, capsys):
        target_root = tmp_path / "rendered"
        target_root.mkdir()
        f = target_root / "dep.yaml"
        f.write_text("nothing here\n")
        mods = tmp_path / "mods"
        mods.mkdir()
        (mods / "del.block").write_text("missing block\n")

        t = make_transform(
            id="del",
            type="delete_block",
            target_file="dep.yaml",
            source_file="mods/del.block",
        )
        # Should NOT raise, just warn
        apply_delete_block(t, target_root, tmp_path)


# ---------------------------------------------------------------------------
# Validation: multiple sources
# ---------------------------------------------------------------------------

class TestValidation:
    def test_multiple_sources_raises(self):
        with pytest.raises(Exception):
            TransformConfig(
                id="bad",
                type="insert_after",
                target_file="f.yaml",
                match={"text": "x"},
                source_file="mods/a.tpl",
                yaml_value="inline",
            )

    def test_no_source_raises(self):
        with pytest.raises(Exception):
            TransformConfig(
                id="bad",
                type="insert_after",
                target_file="f.yaml",
                match={"text": "x"},
            )

    def test_match_text_and_regex_raises(self):
        with pytest.raises(Exception):
            TransformConfig(
                id="bad",
                type="insert_after",
                target_file="f.yaml",
                match={"text": "x", "regex": "x.*"},
                yaml_value="content",
            )

    def test_insert_after_without_match_raises(self):
        with pytest.raises(Exception):
            TransformConfig(
                id="bad",
                type="insert_after",
                target_file="f.yaml",
                yaml_value="content",
            )

    def test_insert_after_match_without_text_or_regex_raises(self):
        with pytest.raises(Exception):
            TransformConfig(
                id="bad",
                type="insert_after",
                target_file="f.yaml",
                match={"start": "begin", "end": "end"},
                yaml_value="content",
            )

    def test_replace_block_without_match_raises(self):
        with pytest.raises(Exception):
            TransformConfig(
                id="bad",
                type="replace_block",
                target_file="f.yaml",
                source_file="mods/a.block",
            )

    def test_replace_block_match_missing_end_raises(self):
        with pytest.raises(Exception):
            TransformConfig(
                id="bad",
                type="replace_block",
                target_file="f.yaml",
                match={"start": "begin"},
                source_file="mods/a.block",
            )

    def test_delete_block_multiple_sources_raises(self):
        with pytest.raises(Exception):
            TransformConfig(
                id="bad",
                type="delete_block",
                target_file="f.yaml",
                source_file="mods/a.block",
                yaml_value="inline",
            )

    def test_copy_file_yaml_value_is_valid_source(self):
        """yaml_value is a valid source for copy_file (same as source_file/yaml_file)."""
        t = TransformConfig(
            id="ok",
            type="copy_file",
            target_file="f.yaml",
            yaml_value="content: here\n",
        )
        assert t.yaml_value == "content: here\n"


# ---------------------------------------------------------------------------
# source-init / source-sync subprocess mocks
# ---------------------------------------------------------------------------

class TestSourceCommands:
    def test_source_init_calls_git_submodule_add(self, tmp_path):
        config_path = tmp_path / "render.config.yaml"
        config_data = {
            "version": 1,
            "upstream": {
                "mode": "submodule",
                "remote": "https://github.com/example/chart.git",
                "branch": "main",
                "path": "vendor-source/chart",
                "tracked_ref": "v1.0.0",
            },
            "render": {
                "source_root": "vendor-source/chart",
                "target_root": "rendered/chart",
            },
        }
        config_path.write_text(yaml.dump(config_data))

        with patch("render_sync.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            from typer.testing import CliRunner
            from render_sync import app
            runner = CliRunner()
            result = runner.invoke(app, ["source-init", "--config", str(config_path)])
            # Two calls: git submodule add + git submodule update
            assert mock_run.call_count == 2
            first_call_args = mock_run.call_args_list[0][0][0]
            assert "submodule" in first_call_args
            assert "add" in first_call_args

    def test_source_sync_calls_git_submodule_update(self, tmp_path):
        config_path = tmp_path / "render.config.yaml"
        config_data = {
            "version": 1,
            "upstream": {
                "mode": "submodule",
                "remote": "https://github.com/example/chart.git",
                "branch": "main",
                "path": "vendor-source/chart",
            },
            "render": {
                "source_root": "vendor-source/chart",
                "target_root": "rendered/chart",
            },
        }
        config_path.write_text(yaml.dump(config_data))

        with patch("render_sync.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            from typer.testing import CliRunner
            from render_sync import app
            runner = CliRunner()
            result = runner.invoke(app, ["source-sync", "--config", str(config_path)])
            assert mock_run.call_count == 1
            call_args = mock_run.call_args_list[0][0][0]
            assert "submodule" in call_args
            assert "update" in call_args
