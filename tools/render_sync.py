#!/usr/bin/env python3
"""
render_sync.py — Declarative upstream customization tool.

Applies transforms defined in render.config.yaml to files copied from
vendor-source/ into rendered/, without modifying the upstream directly.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Literal, Optional

import pathspec
import typer
import yaml
from pydantic import BaseModel, model_validator

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class UpstreamConfig(BaseModel):
    mode: str = "submodule"
    remote: str = ""
    branch: str = "main"
    path: str
    tracked_ref: str = ""


class RenderConfig(BaseModel):
    source_root: str
    target_root: str
    include: list[str] = ["**"]
    exclude: list[str] = []


class MatchConfig(BaseModel):
    text: Optional[str] = None
    regex: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    occurrence: int = 1

    @model_validator(mode="after")
    def validate_match(self) -> "MatchConfig":
        if self.text is not None and self.regex is not None:
            raise ValueError("match cannot define both 'text' and 'regex'")
        return self


class MarkersConfig(BaseModel):
    start: str = "# BEGIN custom-block: {id}"
    end: str = "# END custom-block: {id}"


class TransformConfig(BaseModel):
    id: str
    type: Literal["copy_file", "insert_after", "replace_block", "delete_block"]
    target_file: str
    match: Optional[MatchConfig] = None
    source_file: Optional[str] = None
    yaml_file: Optional[str] = None
    yaml_value: Optional[str] = None

    @model_validator(mode="after")
    def validate_sources(self) -> "TransformConfig":
        sources = [s for s in [self.source_file, self.yaml_file, self.yaml_value] if s is not None]
        if self.type in ("insert_after", "replace_block"):
            if len(sources) == 0:
                raise ValueError(
                    f"Transform '{self.id}' ({self.type}) requires exactly one content source "
                    "(source_file, yaml_file, or yaml_value)"
                )
            if len(sources) > 1:
                raise ValueError(
                    f"Transform '{self.id}' ({self.type}) defines multiple content sources; "
                    "use exactly one of source_file, yaml_file, or yaml_value"
                )
        if self.type == "copy_file":
            if len(sources) == 0:
                raise ValueError(
                    f"Transform '{self.id}' (copy_file) requires exactly one content source "
                    "(source_file, yaml_file, or yaml_value)"
                )
            if len(sources) > 1:
                raise ValueError(
                    f"Transform '{self.id}' (copy_file) defines multiple content sources; "
                    "use exactly one of source_file, yaml_file, or yaml_value"
                )
        if self.type == "insert_after":
            if self.match is None:
                raise ValueError(f"Transform '{self.id}' (insert_after) requires a 'match' configuration")
            if self.match.text is None and self.match.regex is None:
                raise ValueError(f"Transform '{self.id}' (insert_after) requires match.text or match.regex")
        if self.type == "replace_block":
            if self.match is None:
                raise ValueError(f"Transform '{self.id}' (replace_block) requires a 'match' configuration")
            if self.match.start is None or self.match.end is None:
                raise ValueError(f"Transform '{self.id}' (replace_block) requires match.start and match.end")
        if self.type == "delete_block":
            if len(sources) > 1:
                raise ValueError(
                    f"Transform '{self.id}' (delete_block) defines multiple content sources; "
                    "use exactly one of source_file, yaml_file, or yaml_value"
                )
            # delete_block can work with source_file/yaml_file/yaml_value OR match.start+end
            has_source = len(sources) > 0
            has_match_range = self.match is not None and self.match.start is not None and self.match.end is not None
            if not has_source and not has_match_range:
                raise ValueError(
                    f"Transform '{self.id}' (delete_block) requires either a content source or match.start + match.end"
                )
        return self


class ModificationsConfig(BaseModel):
    markers: MarkersConfig = MarkersConfig()
    transforms: list[TransformConfig] = []


class Config(BaseModel):
    version: int = 1
    upstream: Optional[UpstreamConfig] = None
    render: RenderConfig
    modifications: ModificationsConfig = ModificationsConfig()


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

CONFIG_FILE = Path("render.config.yaml")


def load_config(config_path: Path = CONFIG_FILE) -> Config:
    if not config_path.exists():
        typer.echo(f"[ERROR] Config file not found: {config_path}", err=True)
        raise SystemExit(1)
    with config_path.open() as f:
        raw = yaml.safe_load(f)
    try:
        return Config(**raw)
    except Exception as exc:
        typer.echo(f"[ERROR] Invalid config: {exc}", err=True)
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Content resolver
# ---------------------------------------------------------------------------


def resolve_content(transform: TransformConfig, base_dir: Path) -> str:
    """Return the text content for a transform, from whichever source is defined."""
    if transform.yaml_value is not None:
        return transform.yaml_value
    path_str = transform.source_file or transform.yaml_file
    assert path_str is not None
    p = base_dir / path_str
    if not p.exists():
        typer.echo(f"[ERROR] Source file not found: {p}", err=True)
        raise SystemExit(1)
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Marker helpers
# ---------------------------------------------------------------------------


def marker_start(markers: MarkersConfig, id_: str) -> str:
    return markers.start.replace("{id}", id_)


def marker_end(markers: MarkersConfig, id_: str) -> str:
    return markers.end.replace("{id}", id_)


def wrap_with_markers(content: str, markers: MarkersConfig, id_: str) -> str:
    content = content.rstrip("\n")
    return f"{marker_start(markers, id_)}\n{content}\n{marker_end(markers, id_)}\n"


# ---------------------------------------------------------------------------
# Transform engine — text backend
# ---------------------------------------------------------------------------


def _find_occurrence(lines: list[str], pattern: str, is_regex: bool, occurrence: int) -> int:
    """Return index of the `occurrence`-th line matching `pattern`. Raises ValueError if not found."""
    found = 0
    for i, line in enumerate(lines):
        matched = bool(re.search(pattern, line)) if is_regex else pattern in line
        if matched:
            found += 1
            if found == occurrence:
                return i
    raise ValueError(f"Pattern {pattern!r} not found (occurrence {occurrence})")


def apply_copy_file(transform: TransformConfig, target_root: Path, base_dir: Path) -> None:
    content = resolve_content(transform, base_dir)
    target = target_root / transform.target_file
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    typer.echo(f"  [copy_file] {transform.target_file}")


def apply_insert_after(
    transform: TransformConfig,
    target_root: Path,
    base_dir: Path,
    markers: MarkersConfig,
) -> None:
    target = target_root / transform.target_file
    if not target.exists():
        typer.echo(f"[ERROR] Target file not found: {target}", err=True)
        raise SystemExit(1)

    content = resolve_content(transform, base_dir)
    lines = target.read_text(encoding="utf-8").splitlines(keepends=True)

    m_start = marker_start(markers, transform.id)
    m_end = marker_end(markers, transform.id)

    # Check idempotence: if markers already exist, replace block content
    start_idx = None
    end_idx = None
    for i, line in enumerate(lines):
        if m_start in line:
            start_idx = i
        if m_end in line and start_idx is not None:
            end_idx = i
            break

    wrapped = wrap_with_markers(content, markers, transform.id)
    wrapped_lines = wrapped.splitlines(keepends=True)

    if start_idx is not None and end_idx is not None:
        # Replace existing block
        lines[start_idx : end_idx + 1] = wrapped_lines
        typer.echo(f"  [insert_after] {transform.target_file} (updated existing block '{transform.id}')")
    else:
        # Insert after match
        match = transform.match
        assert match is not None
        pattern = match.text if match.text is not None else match.regex
        assert pattern is not None
        is_regex = match.regex is not None

        try:
            idx = _find_occurrence(lines, pattern, is_regex, match.occurrence)
        except ValueError as exc:
            typer.echo(f"[ERROR] {exc} in {transform.target_file}", err=True)
            raise SystemExit(1)

        lines[idx + 1 : idx + 1] = wrapped_lines
        typer.echo(f"  [insert_after] {transform.target_file} (inserted block '{transform.id}')")

    target.write_text("".join(lines), encoding="utf-8")


def apply_replace_block(
    transform: TransformConfig,
    target_root: Path,
    base_dir: Path,
    markers: MarkersConfig,
) -> None:
    target = target_root / transform.target_file
    if not target.exists():
        typer.echo(f"[ERROR] Target file not found: {target}", err=True)
        raise SystemExit(1)

    content = resolve_content(transform, base_dir)
    match = transform.match
    assert match is not None
    assert match.start is not None and match.end is not None

    lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    occurrence = match.occurrence
    count = 0
    start_idx = None

    for i, line in enumerate(lines):
        if match.start in line:
            count += 1
            if count == occurrence:
                start_idx = i
                break

    if start_idx is None:
        typer.echo(
            f"[ERROR] replace_block: start pattern {match.start!r} not found "
            f"(occurrence {occurrence}) in {transform.target_file}",
            err=True,
        )
        raise SystemExit(1)

    end_idx = None
    for i in range(start_idx + 1, len(lines)):
        if match.end in lines[i]:
            end_idx = i
            break

    if end_idx is None:
        typer.echo(
            f"[ERROR] replace_block: end pattern {match.end!r} not found after line {start_idx} "
            f"in {transform.target_file}",
            err=True,
        )
        raise SystemExit(1)

    # Replace lines from start_idx to end_idx (inclusive of start, exclusive of end anchor)
    # The replacement includes the start anchor line (match.start) but NOT the end anchor line (match.end)
    replacement_lines = content.splitlines(keepends=True)
    # Keep the end anchor line (match.end)
    lines[start_idx:end_idx] = replacement_lines
    target.write_text("".join(lines), encoding="utf-8")
    typer.echo(f"  [replace_block] {transform.target_file} (block '{transform.id}')")


def apply_delete_block(
    transform: TransformConfig,
    target_root: Path,
    base_dir: Path,
) -> None:
    target = target_root / transform.target_file
    if not target.exists():
        typer.echo(f"[ERROR] Target file not found: {target}", err=True)
        raise SystemExit(1)

    file_text = target.read_text(encoding="utf-8")

    if transform.source_file is not None or transform.yaml_file is not None or transform.yaml_value is not None:
        # Delete by exact block match
        block = resolve_content(transform, base_dir)
        if block not in file_text:
            typer.echo(
                f"[WARNING] delete_block: block not found in {transform.target_file}, skipping.",
                err=True,
            )
            return
        file_text = file_text.replace(block, "", 1)
        target.write_text(file_text, encoding="utf-8")
        typer.echo(f"  [delete_block] {transform.target_file} (block '{transform.id}')")
    else:
        # Delete by match.start + match.end
        match = transform.match
        assert match is not None
        lines = file_text.splitlines(keepends=True)
        occurrence = match.occurrence if match.occurrence else 1
        count = 0
        start_idx = None
        for i, line in enumerate(lines):
            if match.start in line:
                count += 1
                if count == occurrence:
                    start_idx = i
                    break

        if start_idx is None:
            typer.echo(
                f"[WARNING] delete_block: start {match.start!r} not found in {transform.target_file}, skipping.",
                err=True,
            )
            return

        end_idx = None
        for i in range(start_idx + 1, len(lines)):
            if match.end in lines[i]:
                end_idx = i
                break

        if end_idx is None:
            typer.echo(
                f"[WARNING] delete_block: end {match.end!r} not found in {transform.target_file}, skipping.",
                err=True,
            )
            return

        del lines[start_idx : end_idx + 1]
        target.write_text("".join(lines), encoding="utf-8")
        typer.echo(f"  [delete_block] {transform.target_file} (block '{transform.id}')")


# ---------------------------------------------------------------------------
# File copy with pathspec include/exclude
# ---------------------------------------------------------------------------


def collect_files(source_root: Path, include_patterns: list[str], exclude_patterns: list[str]) -> list[Path]:
    """Return relative paths of files to copy."""
    include_spec = pathspec.PathSpec.from_lines("gitwildmatch", include_patterns)
    exclude_spec = pathspec.PathSpec.from_lines("gitwildmatch", exclude_patterns) if exclude_patterns else None

    result = []
    for p in source_root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(source_root)
        rel_str = rel.as_posix()
        if not include_spec.match_file(rel_str):
            continue
        if exclude_spec and exclude_spec.match_file(rel_str):
            continue
        result.append(rel)
    return sorted(result)


def do_render_copy(cfg: Config, base_dir: Path) -> None:
    source_root = base_dir / cfg.render.source_root
    target_root = base_dir / cfg.render.target_root

    if not source_root.exists():
        typer.echo(f"[ERROR] Source root not found: {source_root}", err=True)
        raise SystemExit(1)

    files = collect_files(source_root, cfg.render.include, cfg.render.exclude)
    if not files:
        typer.echo("[WARNING] No files matched include/exclude patterns.")
        return

    # Clear and recreate target
    if target_root.exists():
        shutil.rmtree(target_root)
    target_root.mkdir(parents=True, exist_ok=True)

    for rel in files:
        src = source_root / rel
        dst = target_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        typer.echo(f"  [copy] {rel}")

    typer.echo(f"[render-copy] Copied {len(files)} file(s) to {target_root}")


def do_apply_mods(cfg: Config, base_dir: Path) -> None:
    target_root = base_dir / cfg.render.target_root
    markers = cfg.modifications.markers

    for transform in cfg.modifications.transforms:
        typer.echo(f"[apply-mods] Applying '{transform.id}' ({transform.type})")
        if transform.type == "copy_file":
            apply_copy_file(transform, target_root, base_dir)
        elif transform.type == "insert_after":
            apply_insert_after(transform, target_root, base_dir, markers)
        elif transform.type == "replace_block":
            apply_replace_block(transform, target_root, base_dir, markers)
        elif transform.type == "delete_block":
            apply_delete_block(transform, target_root, base_dir)


# ---------------------------------------------------------------------------
# Typer CLI
# ---------------------------------------------------------------------------

app = typer.Typer(help="render_sync — Declarative upstream customization tool")


@app.command("validate-config")
def validate_config(
    config: Path = typer.Option(CONFIG_FILE, "--config", "-c", help="Path to render.config.yaml"),
) -> None:
    """Validate render.config.yaml schema."""
    cfg = load_config(config)
    typer.echo(f"[validate-config] Config is valid (version={cfg.version})")


@app.command("render-copy")
def render_copy(
    config: Path = typer.Option(CONFIG_FILE, "--config", "-c"),
) -> None:
    """Copy files from source_root to target_root using include/exclude rules."""
    cfg = load_config(config)
    base_dir = config.parent
    typer.echo("[render-copy] Starting copy...")
    do_render_copy(cfg, base_dir)


@app.command("apply-mods")
def apply_mods(
    config: Path = typer.Option(CONFIG_FILE, "--config", "-c"),
) -> None:
    """Apply all transforms defined in modifications.transforms."""
    cfg = load_config(config)
    base_dir = config.parent
    typer.echo("[apply-mods] Applying modifications...")
    do_apply_mods(cfg, base_dir)
    typer.echo("[apply-mods] Done.")


@app.command("all")
def run_all(
    config: Path = typer.Option(CONFIG_FILE, "--config", "-c"),
) -> None:
    """Run validate-config, render-copy, and apply-mods in sequence."""
    cfg = load_config(config)
    base_dir = config.parent
    typer.echo("[all] Step 1/3: validate-config")
    typer.echo(f"[all] Config valid (version={cfg.version})")

    typer.echo("[all] Step 2/3: render-copy")
    do_render_copy(cfg, base_dir)

    typer.echo("[all] Step 3/3: apply-mods")
    do_apply_mods(cfg, base_dir)

    typer.echo("[all] All steps completed successfully.")


@app.command("show-plan")
def show_plan(
    config: Path = typer.Option(CONFIG_FILE, "--config", "-c"),
) -> None:
    """Print a human-readable summary of all transforms."""
    cfg = load_config(config)
    typer.echo(f"[show-plan] render.config.yaml v{cfg.version}")
    typer.echo(f"  source_root : {cfg.render.source_root}")
    typer.echo(f"  target_root : {cfg.render.target_root}")
    typer.echo(f"  transforms  : {len(cfg.modifications.transforms)}")
    typer.echo("")
    for t in cfg.modifications.transforms:
        source = t.source_file or t.yaml_file or ("<inline yaml_value>" if t.yaml_value else "<none>")
        typer.echo(f"  [{t.id}]")
        typer.echo(f"    type        : {t.type}")
        typer.echo(f"    target_file : {t.target_file}")
        typer.echo(f"    source      : {source}")
        if t.match:
            typer.echo(f"    match       : {t.match.model_dump(exclude_none=True)}")
        typer.echo("")


@app.command("source-init")
def source_init(
    config: Path = typer.Option(CONFIG_FILE, "--config", "-c"),
) -> None:
    """Initialize the upstream submodule (or report if already exists)."""
    cfg = load_config(config)
    if cfg.upstream is None:
        typer.echo("[source-init] No upstream config defined, skipping.")
        return

    upstream = cfg.upstream
    base_dir = config.parent
    upstream_path = base_dir / upstream.path

    if upstream_path.exists() and any(upstream_path.iterdir()):
        typer.echo(f"[source-init] Upstream path already exists: {upstream_path}")
        typer.echo("[source-init] Nothing to do.")
        return

    if upstream.mode == "submodule":
        if not upstream.remote:
            typer.echo(
                "[source-init] No remote configured. For a local PoC, populate vendor-source/ manually.",
                err=True,
            )
            raise SystemExit(1)
        typer.echo(f"[source-init] Adding submodule: {upstream.remote} -> {upstream.path}")
        cmd = [
            "git",
            "submodule",
            "add",
            "-b",
            upstream.branch,
            upstream.remote,
            upstream.path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(base_dir))
        if result.returncode != 0:
            typer.echo(f"[ERROR] git submodule add failed:\n{result.stderr}", err=True)
            raise SystemExit(result.returncode)
        typer.echo(result.stdout)

        result2 = subprocess.run(
            ["git", "submodule", "update", "--init", "--recursive"],
            capture_output=True,
            text=True,
            cwd=str(base_dir),
        )
        if result2.returncode != 0:
            typer.echo(f"[ERROR] git submodule update failed:\n{result2.stderr}", err=True)
            raise SystemExit(result2.returncode)
        typer.echo(result2.stdout)
        typer.echo("[source-init] Done.")
    else:
        typer.echo(f"[source-init] Unsupported mode: {upstream.mode}", err=True)
        raise SystemExit(1)


@app.command("source-sync")
def source_sync(
    config: Path = typer.Option(CONFIG_FILE, "--config", "-c"),
) -> None:
    """Update the upstream submodule to latest."""
    cfg = load_config(config)
    if cfg.upstream is None:
        typer.echo("[source-sync] No upstream config defined, skipping.")
        return

    base_dir = config.parent
    typer.echo("[source-sync] Updating submodule...")
    result = subprocess.run(
        ["git", "submodule", "update", "--remote", "--merge"],
        capture_output=True,
        text=True,
        cwd=str(base_dir),
    )
    if result.returncode != 0:
        typer.echo(f"[ERROR] git submodule update failed:\n{result.stderr}", err=True)
        raise SystemExit(result.returncode)
    typer.echo(result.stdout or "[source-sync] Submodule is up to date.")
    typer.echo("[source-sync] Done.")


@app.command("source-status")
def source_status(
    config: Path = typer.Option(CONFIG_FILE, "--config", "-c"),
) -> None:
    """Show upstream submodule status."""
    cfg = load_config(config)
    if cfg.upstream is None:
        typer.echo("[source-status] No upstream config defined.")
        return

    upstream = cfg.upstream
    base_dir = config.parent
    upstream_path = base_dir / upstream.path

    typer.echo(f"  mode        : {upstream.mode}")
    typer.echo(f"  remote      : {upstream.remote or '<not set>'}")
    typer.echo(f"  branch      : {upstream.branch}")
    typer.echo(f"  path        : {upstream.path}")
    typer.echo(f"  tracked_ref : {upstream.tracked_ref or '<not set>'}")
    typer.echo(f"  path exists : {upstream_path.exists()}")

    # Try to get current commit
    if upstream_path.exists():
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(upstream_path),
        )
        if result.returncode == 0:
            typer.echo(f"  current commit: {result.stdout.strip()}")
        else:
            typer.echo("  current commit: <unable to determine>")


@app.command("init-skeleton")
def init_skeleton(
    base: Path = typer.Option(Path("."), "--base", "-b", help="Base directory"),
) -> None:
    """Generate base mods/ structure and example render.config.yaml."""
    dirs = [
        base / "mods" / "templates" / "snippets",
        base / "mods" / "replace-blocks",
        base / "mods" / "delete-blocks",
        base / "vendor-source",
        base / "rendered",
        base / "tools",
        base / "scripts",
        base / "tests",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
        typer.echo(f"  [mkdir] {d}")

    config_path = base / "render.config.yaml"
    if not config_path.exists():
        example_config = """version: 1

upstream:
  mode: submodule
  remote: ""
  branch: "main"
  path: "vendor-source/upstream-chart"
  tracked_ref: ""

render:
  source_root: "vendor-source/upstream-chart"
  target_root: "rendered/app-chart"
  include:
    - "Chart.yaml"
    - "values.yaml"
    - "templates/**"
  exclude:
    - "templates/tests/**"

modifications:
  markers:
    start: "# BEGIN custom-block: {id}"
    end: "# END custom-block: {id}"
  transforms: []
"""
        config_path.write_text(example_config)
        typer.echo(f"  [created] {config_path}")
    else:
        typer.echo(f"  [skip] {config_path} already exists")

    typer.echo("[init-skeleton] Done.")


@app.command("help")
def help_cmd(
    ctx: typer.Context,
    command: Optional[str] = typer.Argument(None, help="Command to show help for."),
) -> None:
    """Show help for a command.

    Without arguments prints overall help (same as --help).
    With a command name prints that command's help, e.g.:

        render_sync.py help validate-config
    """
    import click  # transitive dep of typer; imported here to keep module-level imports minimal

    parent_ctx = ctx.parent
    if command is None:
        typer.echo(parent_ctx.get_help())
        return

    # Look up the named sub-command in the Click group
    group = parent_ctx.command
    sub_cmd = group.commands.get(command)  # type: ignore[attr-defined]
    if sub_cmd is None:
        typer.echo(
            f"[ERROR] Unknown command: {command!r}\nAvailable commands: {', '.join(sorted(group.commands))}",  # type: ignore[attr-defined]
            err=True,
        )
        raise typer.Exit(1)

    sub_ctx = click.Context(sub_cmd, parent=parent_ctx, info_name=command)
    typer.echo(sub_ctx.get_help())


if __name__ == "__main__":
    app()
