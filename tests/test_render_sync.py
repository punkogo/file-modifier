"""
Tests for tools/render_sync.py

Covers:
- copy_file: copies content correctly
- insert_after: inserts with marker
- insert_after: idempotent (second run does not duplicate)
- replace_block: replaces expected block
- delete_block: removes block
- error when match not found
- error when transform defines more than one source (source_file + yaml_value, etc.)
- include/exclude basic behavior in render-copy
- subprocess mocks for source-init and source-sync
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

# Make sure tools/ is importable
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

from render_sync import (  # noqa: E402
    Config,
    MarkersConfig,
    ModificationsConfig,
    RenderConfig,
    TextTransformEngine,
    TransformConfig,
    UpstreamConfig,
    apply_modifications,
    copy_source_to_target,
    load_config,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_base(tmp_path: Path) -> Path:
    """Return a temporary base directory."""
    return tmp_path


def make_transform(**kwargs) -> TransformConfig:
    return TransformConfig.model_validate(kwargs)


def make_engine() -> TextTransformEngine:
    return TextTransformEngine(MarkersConfig())


# ---------------------------------------------------------------------------
# copy_file
# ---------------------------------------------------------------------------


class TestCopyFile:
    def test_copy_file_creates_file(self, tmp_base: Path) -> None:
        target = tmp_base / "out" / "file.tpl"
        transform = make_transform(
            id="t1", type="copy_file", target_file="out/file.tpl", yaml_value="hello world\n"
        )
        engine = make_engine()
        engine.copy_file(target, "hello world\n", transform)
        assert target.read_text() == "hello world\n"

    def test_copy_file_overwrites_existing(self, tmp_base: Path) -> None:
        target = tmp_base / "file.tpl"
        target.write_text("old content")
        transform = make_transform(
            id="t1", type="copy_file", target_file="file.tpl", yaml_value="new content\n"
        )
        engine = make_engine()
        engine.copy_file(target, "new content\n", transform)
        assert target.read_text() == "new content\n"

    def test_copy_file_creates_parent_dirs(self, tmp_base: Path) -> None:
        target = tmp_base / "deep" / "nested" / "file.txt"
        transform = make_transform(
            id="t1", type="copy_file", target_file="deep/nested/file.txt", yaml_value="data"
        )
        engine = make_engine()
        engine.copy_file(target, "data", transform)
        assert target.exists()


# ---------------------------------------------------------------------------
# insert_after
# ---------------------------------------------------------------------------


class TestInsertAfter:
    def _make_target(self, tmp_base: Path, content: str) -> Path:
        p = tmp_base / "target.yaml"
        p.write_text(content, encoding="utf-8")
        return p

    def test_insert_after_basic(self, tmp_base: Path) -> None:
        target = self._make_target(
            tmp_base,
            "line1\nspec:\n  containers:\n",
        )
        transform = make_transform(
            id="my-block",
            type="insert_after",
            target_file="target.yaml",
            match={"text": "spec:", "occurrence": 1},
            yaml_value="  initContainers: []\n",
        )
        engine = make_engine()
        engine.insert_after(target, "  initContainers: []\n", transform)
        result = target.read_text()
        assert "# BEGIN custom-block: my-block" in result
        assert "  initContainers: []" in result
        assert "# END custom-block: my-block" in result
        # marker appears after spec:
        spec_idx = result.index("spec:")
        begin_idx = result.index("# BEGIN custom-block: my-block")
        assert begin_idx > spec_idx

    def test_insert_after_idempotent(self, tmp_base: Path) -> None:
        target = self._make_target(
            tmp_base,
            "line1\nspec:\n  containers:\n",
        )
        transform = make_transform(
            id="my-block",
            type="insert_after",
            target_file="target.yaml",
            match={"text": "spec:", "occurrence": 1},
            yaml_value="  initContainers: []\n",
        )
        engine = make_engine()
        # First run
        engine.insert_after(target, "  initContainers: []\n", transform)
        # Second run (idempotent: should not duplicate)
        engine.insert_after(target, "  initContainers: []\n", transform)
        result = target.read_text()
        assert result.count("# BEGIN custom-block: my-block") == 1

    def test_insert_after_updates_content_on_second_run(self, tmp_base: Path) -> None:
        target = self._make_target(tmp_base, "spec:\n  x: 1\n")
        transform = make_transform(
            id="blk",
            type="insert_after",
            target_file="target.yaml",
            match={"text": "spec:", "occurrence": 1},
            yaml_value="old content\n",
        )
        engine = make_engine()
        engine.insert_after(target, "old content\n", transform)
        # Update content
        engine.insert_after(target, "new content\n", transform)
        result = target.read_text()
        assert "new content" in result
        assert "old content" not in result

    def test_insert_after_occurrence(self, tmp_base: Path) -> None:
        target = self._make_target(tmp_base, "spec:\nfoo: 1\nspec:\nbar: 2\n")
        transform = make_transform(
            id="blk2",
            type="insert_after",
            target_file="target.yaml",
            match={"text": "spec:", "occurrence": 2},
            yaml_value="inserted\n",
        )
        engine = make_engine()
        engine.insert_after(target, "inserted\n", transform)
        result = target.read_text()
        lines = result.splitlines()
        # second "spec:" should come before marker
        spec_positions = [i for i, l in enumerate(lines) if l.strip() == "spec:"]
        begin_pos = next(i for i, l in enumerate(lines) if "BEGIN custom-block" in l)
        assert spec_positions[1] < begin_pos

    def test_insert_after_match_not_found_raises(self, tmp_base: Path) -> None:
        target = self._make_target(tmp_base, "no match here\n")
        transform = make_transform(
            id="blk",
            type="insert_after",
            target_file="target.yaml",
            match={"text": "nonexistent:", "occurrence": 1},
            yaml_value="data\n",
        )
        engine = make_engine()
        with pytest.raises(ValueError, match="match not found"):
            engine.insert_after(target, "data\n", transform)


# ---------------------------------------------------------------------------
# replace_block
# ---------------------------------------------------------------------------


class TestReplaceBlock:
    def test_replace_block_basic(self, tmp_base: Path) -> None:
        content = textwrap.dedent("""\
            containers:
              - name: app
                env:
                  - name: FOO
                    value: foo
                ports:
                  - 80
        """)
        target = tmp_base / "file.yaml"
        target.write_text(content)
        transform = make_transform(
            id="r1",
            type="replace_block",
            target_file="file.yaml",
            match={"start": "  env:", "end": "  ports:", "occurrence": 1},
            yaml_value="  env:\n    - name: REPLACED\n      value: replaced\n  ports:\n",
        )
        engine = make_engine()
        engine.replace_block(
            target,
            "  env:\n    - name: REPLACED\n      value: replaced\n  ports:\n",
            transform,
        )
        result = target.read_text()
        assert "REPLACED" in result
        assert "FOO" not in result

    def test_replace_block_start_not_found_raises(self, tmp_base: Path) -> None:
        target = tmp_base / "file.yaml"
        target.write_text("no env here\n")
        transform = make_transform(
            id="r1",
            type="replace_block",
            target_file="file.yaml",
            match={"start": "  env:", "end": "  ports:", "occurrence": 1},
            yaml_value="replacement\n",
        )
        engine = make_engine()
        with pytest.raises(ValueError, match="match.start not found"):
            engine.replace_block(target, "replacement\n", transform)

    def test_replace_block_end_not_found_raises(self, tmp_base: Path) -> None:
        target = tmp_base / "file.yaml"
        target.write_text("  env:\n    - foo\n")
        transform = make_transform(
            id="r1",
            type="replace_block",
            target_file="file.yaml",
            match={"start": "  env:", "end": "  ports:", "occurrence": 1},
            yaml_value="replacement\n",
        )
        engine = make_engine()
        with pytest.raises(ValueError, match="match.end not found"):
            engine.replace_block(target, "replacement\n", transform)


# ---------------------------------------------------------------------------
# delete_block
# ---------------------------------------------------------------------------


class TestDeleteBlock:
    def test_delete_block_exact(self, tmp_base: Path) -> None:
        content = "line1\n  env:\n    - foo\n  ports:\nline2\n"
        target = tmp_base / "file.yaml"
        target.write_text(content)
        block_to_delete = "  env:\n    - foo\n"
        transform = make_transform(
            id="d1", type="delete_block", target_file="file.yaml", yaml_value=block_to_delete
        )
        engine = make_engine()
        engine.delete_block(target, block_to_delete, transform)
        result = target.read_text()
        assert "env:" not in result
        assert "line1" in result
        assert "line2" in result

    def test_delete_block_not_found_warns(self, tmp_base: Path, capsys) -> None:
        target = tmp_base / "file.yaml"
        target.write_text("no block here\n")
        transform = make_transform(
            id="d1", type="delete_block", target_file="file.yaml", yaml_value="missing block\n"
        )
        engine = make_engine()
        # Should not raise, just warn
        engine.delete_block(target, "missing block\n", transform)
        result = target.read_text()
        assert result == "no block here\n"

    def test_delete_block_file_not_found_warns(self, tmp_base: Path) -> None:
        target = tmp_base / "nonexistent.yaml"
        transform = make_transform(
            id="d1", type="delete_block", target_file="nonexistent.yaml", yaml_value="block\n"
        )
        engine = make_engine()
        # Should not raise, just warn
        engine.delete_block(target, "block\n", transform)


# ---------------------------------------------------------------------------
# Validation: multiple sources error
# ---------------------------------------------------------------------------


class TestTransformValidation:
    def test_multiple_sources_raises(self) -> None:
        with pytest.raises(Exception, match="exactly one"):
            make_transform(
                id="bad",
                type="insert_after",
                target_file="foo.yaml",
                match={"text": "spec:"},
                source_file="mods/file.tpl",
                yaml_value="inline content\n",
            )

    def test_no_source_raises(self) -> None:
        with pytest.raises(Exception, match="exactly one"):
            make_transform(
                id="bad",
                type="insert_after",
                target_file="foo.yaml",
                match={"text": "spec:"},
            )

    def test_yaml_file_and_yaml_value_raises(self) -> None:
        with pytest.raises(Exception, match="exactly one"):
            make_transform(
                id="bad",
                type="replace_block",
                target_file="foo.yaml",
                match={"start": "env:", "end": "ports:"},
                yaml_file="mods/file.tpl",
                yaml_value="inline\n",
            )

    def test_match_text_and_regex_raises(self) -> None:
        with pytest.raises(Exception):
            make_transform(
                id="bad",
                type="insert_after",
                target_file="foo.yaml",
                match={"text": "spec:", "regex": "spec:.*"},
                yaml_value="data\n",
            )

    def test_single_source_ok(self) -> None:
        t = make_transform(
            id="ok",
            type="insert_after",
            target_file="foo.yaml",
            match={"text": "spec:"},
            yaml_value="data\n",
        )
        assert t.yaml_value == "data\n"


# ---------------------------------------------------------------------------
# render-copy include/exclude
# ---------------------------------------------------------------------------


class TestRenderCopy:
    def _make_config(self, source_root: str, target_root: str, include, exclude) -> Config:
        return Config.model_validate({
            "version": 1,
            "render": {
                "source_root": source_root,
                "target_root": target_root,
                "include": include,
                "exclude": exclude,
            },
            "modifications": {"transforms": []},
        })

    def test_include_pattern(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "Chart.yaml").write_text("chart")
        (src / "values.yaml").write_text("values")
        templates = src / "templates"
        templates.mkdir()
        (templates / "deploy.yaml").write_text("deploy")
        (src / "README.md").write_text("readme")

        cfg = self._make_config(
            "src", "dst",
            include=["Chart.yaml", "values.yaml", "templates/**"],
            exclude=[],
        )
        copy_source_to_target(cfg, tmp_path)
        dst = tmp_path / "dst"
        assert (dst / "Chart.yaml").exists()
        assert (dst / "values.yaml").exists()
        assert (dst / "templates" / "deploy.yaml").exists()
        assert not (dst / "README.md").exists()

    def test_exclude_pattern(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        templates = src / "templates"
        tests_dir = templates / "tests"
        tests_dir.mkdir(parents=True)
        (templates / "deploy.yaml").write_text("deploy")
        (tests_dir / "test.yaml").write_text("test")

        cfg = self._make_config(
            "src", "dst",
            include=["templates/**"],
            exclude=["templates/tests/**"],
        )
        copy_source_to_target(cfg, tmp_path)
        dst = tmp_path / "dst"
        assert (dst / "templates" / "deploy.yaml").exists()
        assert not (dst / "templates" / "tests" / "test.yaml").exists()


# ---------------------------------------------------------------------------
# source-init / source-sync subprocess mocks
# ---------------------------------------------------------------------------


class TestSourceCommands:
    def _make_cfg_file(self, tmp_path: Path) -> Path:
        cfg_data = {
            "version": 1,
            "upstream": {
                "mode": "submodule",
                "remote": "https://example.com/chart.git",
                "branch": "main",
                "path": "vendor-source/upstream-chart",
                "tracked_ref": "v1.0.0",
            },
            "render": {
                "source_root": "vendor-source/upstream-chart",
                "target_root": "rendered/app-chart",
                "include": ["**"],
                "exclude": [],
            },
            "modifications": {"transforms": []},
        }
        cfg_file = tmp_path / "render.config.yaml"
        with open(cfg_file, "w") as fh:
            yaml.dump(cfg_data, fh)
        return cfg_file

    def test_source_init_calls_git_submodule_add(self, tmp_path: Path) -> None:
        cfg_file = self._make_cfg_file(tmp_path)
        called_commands = []

        def fake_run(cmd, **kwargs):
            called_commands.append(cmd)
            m = MagicMock()
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""
            return m

        from typer.testing import CliRunner
        from render_sync import app as cli_app

        runner = CliRunner()
        with patch("render_sync.subprocess.run", side_effect=fake_run):
            result = runner.invoke(cli_app, ["source-init", "--config", str(cfg_file)])

        assert any("submodule" in " ".join(c) for c in called_commands), (
            f"Expected submodule command. Got: {called_commands}\nOutput: {result.output}"
        )

    def test_source_sync_calls_git_submodule_update(self, tmp_path: Path) -> None:
        cfg_file = self._make_cfg_file(tmp_path)
        called_commands = []

        def fake_run(cmd, **kwargs):
            called_commands.append(cmd)
            m = MagicMock()
            m.returncode = 0
            m.stdout = ""
            m.stderr = ""
            return m

        from typer.testing import CliRunner
        from render_sync import app as cli_app

        runner = CliRunner()
        with patch("render_sync.subprocess.run", side_effect=fake_run):
            result = runner.invoke(cli_app, ["source-sync", "--config", str(cfg_file)])

        assert any("submodule" in " ".join(c) for c in called_commands), (
            f"Expected submodule command. Got: {called_commands}\nOutput: {result.output}"
        )

    def test_source_init_skip_git_flag(self, tmp_path: Path) -> None:
        cfg_file = self._make_cfg_file(tmp_path)
        # Create non-empty upstream path to trigger "already exists" path
        up_path = tmp_path / "vendor-source" / "upstream-chart"
        up_path.mkdir(parents=True)
        (up_path / "Chart.yaml").write_text("chart")

        from typer.testing import CliRunner
        from render_sync import app as cli_app

        runner = CliRunner()
        result = runner.invoke(cli_app, ["source-init", "--config", str(cfg_file)])
        assert result.exit_code == 0
        assert "already exists" in result.output
