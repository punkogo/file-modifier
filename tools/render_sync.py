"""
render_sync.py - Declarative upstream customization tool for Helm charts and templates.

Supports 4 operations: copy_file, insert_after, replace_block, delete_block.
Content sources: source_file, yaml_file, yaml_value (exactly one required per transform).

Architecture note: The transform engine uses a 'text' backend by default.
The engine is designed so a future 'git-apply' or 'patch-ng' backend can be plugged in
without changing the YAML schema or developer UX.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Literal, Optional, Union

import typer
import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

try:
    import pathspec
except ImportError:
    pathspec = None  # type: ignore[assignment]

app = typer.Typer(
    name="render-sync",
    help="Declarative upstream customization tool for Helm charts.",
    add_completion=False,
)

# ---------------------------------------------------------------------------
# Pydantic schema
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
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class MatchConfig(BaseModel):
    text: Optional[str] = None
    regex: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    occurrence: int = 1

    @model_validator(mode="after")
    def validate_match(self) -> "MatchConfig":
        if self.text is not None and self.regex is not None:
            raise ValueError("'match' cannot define both 'text' and 'regex'.")
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
    def validate_source(self) -> "TransformConfig":
        sources = [
            s
            for s in [self.source_file, self.yaml_file, self.yaml_value]
            if s is not None
        ]
        # copy_file requires exactly one source
        if self.type == "copy_file":
            if len(sources) != 1:
                raise ValueError(
                    f"Transform '{self.id}' (copy_file) requires exactly one of: "
                    "source_file, yaml_file, yaml_value."
                )
        # insert_after and replace_block require exactly one source
        elif self.type in ("insert_after", "replace_block"):
            if len(sources) != 1:
                raise ValueError(
                    f"Transform '{self.id}' ({self.type}) requires exactly one of: "
                    "source_file, yaml_file, yaml_value."
                )
        # delete_block: source is optional (can delete by match instead)
        return self


class ModificationsConfig(BaseModel):
    markers: MarkersConfig = Field(default_factory=MarkersConfig)
    transforms: list[TransformConfig] = Field(default_factory=list)


class Config(BaseModel):
    version: int = 1
    upstream: Optional[UpstreamConfig] = None
    render: RenderConfig
    modifications: ModificationsConfig = Field(default_factory=ModificationsConfig)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

CONFIG_FILE = Path("render.config.yaml")


def load_config(config_path: Path = CONFIG_FILE) -> Config:
    """Load and validate render.config.yaml."""
    if not config_path.exists():
        typer.echo(f"[ERROR] Config file not found: {config_path}", err=True)
        raise typer.Exit(1)
    with open(config_path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    try:
        return Config.model_validate(raw)
    except Exception as exc:
        typer.echo(f"[ERROR] Invalid config: {exc}", err=True)
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Content resolution helpers
# ---------------------------------------------------------------------------


def resolve_content(transform: TransformConfig, base_dir: Path) -> str:
    """Return the text content for a transform, from exactly one source."""
    if transform.yaml_value is not None:
        return transform.yaml_value
    if transform.source_file is not None:
        path = base_dir / transform.source_file
        if not path.exists():
            raise FileNotFoundError(f"source_file not found: {path}")
        return path.read_text(encoding="utf-8")
    if transform.yaml_file is not None:
        path = base_dir / transform.yaml_file
        if not path.exists():
            raise FileNotFoundError(f"yaml_file not found: {path}")
        return path.read_text(encoding="utf-8")
    raise ValueError(f"Transform '{transform.id}' has no content source.")


# ---------------------------------------------------------------------------
# Text-backend transform engine
# ---------------------------------------------------------------------------


class TextTransformEngine:
    """
    Default transform engine using plain text operations.

    Design note: Additional backends (e.g. 'git-apply', 'patch-ng') can be
    implemented by subclassing or replacing this class without changing the
    YAML schema or CLI interface.
    """

    def __init__(self, markers: MarkersConfig):
        self.markers = markers

    def _marker_start(self, block_id: str) -> str:
        return self.markers.start.format(id=block_id)

    def _marker_end(self, block_id: str) -> str:
        return self.markers.end.format(id=block_id)

    # -----------------------------------------------------------------------
    # copy_file
    # -----------------------------------------------------------------------

    def copy_file(
        self, target_path: Path, content: str, transform: TransformConfig
    ) -> None:
        """Copy content to target_file, creating parent dirs as needed."""
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(content, encoding="utf-8")
        typer.echo(f"  [copy_file] Written: {target_path}")

    # -----------------------------------------------------------------------
    # insert_after
    # -----------------------------------------------------------------------

    def insert_after(
        self, target_path: Path, content: str, transform: TransformConfig
    ) -> None:
        """
        Insert content after the Nth occurrence of match.text/regex.
        Wraps content in markers. Idempotent: replaces existing marker block.
        """
        if not target_path.exists():
            raise FileNotFoundError(f"Target file not found: {target_path}")

        match_cfg = transform.match
        if match_cfg is None:
            raise ValueError(f"Transform '{transform.id}': 'match' is required for insert_after.")

        file_text = target_path.read_text(encoding="utf-8")
        marker_s = self._marker_start(transform.id)
        marker_e = self._marker_end(transform.id)

        # Idempotence: if markers already exist, replace the block content
        pattern = re.compile(
            re.escape(marker_s) + r".*?" + re.escape(marker_e),
            re.DOTALL,
        )
        new_block = marker_s + "\n" + content.rstrip("\n") + "\n" + marker_e
        if pattern.search(file_text):
            new_text = pattern.sub(new_block, file_text)
            target_path.write_text(new_text, encoding="utf-8")
            typer.echo(f"  [insert_after] Updated existing block '{transform.id}' in {target_path}")
            return

        # Find match position
        lines = file_text.splitlines(keepends=True)
        occurrence = match_cfg.occurrence
        found = 0
        insert_after_idx = -1

        for idx, line in enumerate(lines):
            if match_cfg.text is not None:
                hit = match_cfg.text in line
            elif match_cfg.regex is not None:
                hit = bool(re.search(match_cfg.regex, line))
            else:
                raise ValueError(
                    f"Transform '{transform.id}': match requires 'text' or 'regex'."
                )
            if hit:
                found += 1
                if found == occurrence:
                    insert_after_idx = idx
                    break

        if insert_after_idx == -1:
            raise ValueError(
                f"Transform '{transform.id}': match not found "
                f"(text={match_cfg.text!r}, regex={match_cfg.regex!r}, "
                f"occurrence={occurrence}) in {target_path}"
            )

        block_lines = (new_block + "\n").splitlines(keepends=True)
        lines[insert_after_idx + 1 : insert_after_idx + 1] = block_lines
        target_path.write_text("".join(lines), encoding="utf-8")
        typer.echo(f"  [insert_after] Inserted block '{transform.id}' in {target_path}")

    # -----------------------------------------------------------------------
    # replace_block
    # -----------------------------------------------------------------------

    def replace_block(
        self, target_path: Path, content: str, transform: TransformConfig
    ) -> None:
        """
        Replace the text block between match.start and match.end (inclusive).
        The replacement content replaces the entire matched region (both anchors + interior).
        """
        if not target_path.exists():
            raise FileNotFoundError(f"Target file not found: {target_path}")

        match_cfg = transform.match
        if match_cfg is None or match_cfg.start is None or match_cfg.end is None:
            raise ValueError(
                f"Transform '{transform.id}': replace_block requires match.start and match.end."
            )

        file_text = target_path.read_text(encoding="utf-8")
        lines = file_text.splitlines(keepends=True)

        occurrence = match_cfg.occurrence
        found = 0
        start_idx = -1
        end_idx = -1

        for idx, line in enumerate(lines):
            if start_idx == -1 and match_cfg.start in line:
                found += 1
                if found == occurrence:
                    start_idx = idx
                    continue
            if start_idx != -1 and end_idx == -1 and match_cfg.end in line:
                end_idx = idx
                break

        if start_idx == -1:
            raise ValueError(
                f"Transform '{transform.id}': match.start not found "
                f"(text={match_cfg.start!r}, occurrence={occurrence}) in {target_path}"
            )
        if end_idx == -1:
            raise ValueError(
                f"Transform '{transform.id}': match.end not found "
                f"(text={match_cfg.end!r}) after start in {target_path}"
            )

        replacement_lines = content.splitlines(keepends=True)
        if replacement_lines and not replacement_lines[-1].endswith("\n"):
            replacement_lines[-1] += "\n"
        lines[start_idx : end_idx + 1] = replacement_lines
        target_path.write_text("".join(lines), encoding="utf-8")
        typer.echo(f"  [replace_block] Replaced block '{transform.id}' in {target_path}")

    # -----------------------------------------------------------------------
    # delete_block
    # -----------------------------------------------------------------------

    def delete_block(
        self, target_path: Path, content: Optional[str], transform: TransformConfig
    ) -> None:
        """
        Delete an exact block from target_file.
        If content is provided, removes that exact text.
        If match.start + match.end are defined, removes lines between them (inclusive).
        """
        if not target_path.exists():
            typer.echo(
                f"  [delete_block] WARNING: Target file not found, skipping: {target_path}",
                err=True,
            )
            return

        file_text = target_path.read_text(encoding="utf-8")

        # Method A: exact content match
        if content is not None:
            if content not in file_text:
                typer.echo(
                    f"  [delete_block] WARNING: Block not found in {target_path}, skipping.",
                    err=True,
                )
                return
            new_text = file_text.replace(content, "", 1)
            target_path.write_text(new_text, encoding="utf-8")
            typer.echo(f"  [delete_block] Deleted block '{transform.id}' from {target_path}")
            return

        # Method B: match.start + match.end
        match_cfg = transform.match
        if match_cfg and match_cfg.start and match_cfg.end:
            lines = file_text.splitlines(keepends=True)
            occurrence = match_cfg.occurrence
            found = 0
            start_idx = -1
            end_idx = -1
            for idx, line in enumerate(lines):
                if start_idx == -1 and match_cfg.start in line:
                    found += 1
                    if found == occurrence:
                        start_idx = idx
                        continue
                if start_idx != -1 and end_idx == -1 and match_cfg.end in line:
                    end_idx = idx
                    break
            if start_idx == -1 or end_idx == -1:
                typer.echo(
                    f"  [delete_block] WARNING: Block boundaries not found in {target_path}, skipping.",
                    err=True,
                )
                return
            del lines[start_idx : end_idx + 1]
            target_path.write_text("".join(lines), encoding="utf-8")
            typer.echo(f"  [delete_block] Deleted block '{transform.id}' from {target_path}")
            return

        typer.echo(
            f"  [delete_block] WARNING: No content or match defined for '{transform.id}', skipping.",
            err=True,
        )


# ---------------------------------------------------------------------------
# File copy with include/exclude (pathspec)
# ---------------------------------------------------------------------------


def copy_source_to_target(cfg: Config, base_dir: Path) -> None:
    """
    Copy files from render.source_root to render.target_root
    applying include/exclude patterns via pathspec.
    """
    source_root = base_dir / cfg.render.source_root
    target_root = base_dir / cfg.render.target_root

    if not source_root.exists():
        typer.echo(f"[ERROR] source_root not found: {source_root}", err=True)
        raise typer.Exit(1)

    if pathspec is None:
        typer.echo("[ERROR] pathspec library not installed. Run: pip install pathspec", err=True)
        raise typer.Exit(1)

    include_spec = pathspec.PathSpec.from_lines("gitwildmatch", cfg.render.include)
    exclude_spec = pathspec.PathSpec.from_lines("gitwildmatch", cfg.render.exclude)

    copied = 0
    skipped = 0
    for src_file in source_root.rglob("*"):
        if not src_file.is_file():
            continue
        rel = src_file.relative_to(source_root)
        rel_str = rel.as_posix()

        if not include_spec.match_file(rel_str):
            skipped += 1
            continue
        if exclude_spec.match_file(rel_str):
            skipped += 1
            continue

        dst_file = target_root / rel
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dst_file)
        typer.echo(f"  [copy] {rel_str}")
        copied += 1

    typer.echo(f"[render-copy] Done: {copied} copied, {skipped} skipped.")


# ---------------------------------------------------------------------------
# Apply modifications
# ---------------------------------------------------------------------------


def apply_modifications(cfg: Config, base_dir: Path) -> None:
    """Apply all transforms defined in modifications.transforms."""
    engine = TextTransformEngine(cfg.modifications.markers)
    target_root = base_dir / cfg.render.target_root

    for transform in cfg.modifications.transforms:
        typer.echo(f"\n[apply-mods] Processing transform: {transform.id} ({transform.type})")
        target_path = target_root / transform.target_file

        try:
            if transform.type == "copy_file":
                content = resolve_content(transform, base_dir)
                engine.copy_file(target_path, content, transform)

            elif transform.type == "insert_after":
                content = resolve_content(transform, base_dir)
                engine.insert_after(target_path, content, transform)

            elif transform.type == "replace_block":
                content = resolve_content(transform, base_dir)
                engine.replace_block(target_path, content, transform)

            elif transform.type == "delete_block":
                # delete_block: content is optional (may use match instead)
                sources = [
                    s
                    for s in [transform.source_file, transform.yaml_file, transform.yaml_value]
                    if s is not None
                ]
                content = resolve_content(transform, base_dir) if sources else None
                engine.delete_block(target_path, content, transform)

        except (FileNotFoundError, ValueError) as exc:
            typer.echo(f"  [ERROR] Transform '{transform.id}' failed: {exc}", err=True)
            raise typer.Exit(1)

    typer.echo("\n[apply-mods] All transforms applied successfully.")


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

config_option = typer.Option(
    "--config",
    "-c",
    help="Path to render.config.yaml",
)


@app.command("validate-config")
def cmd_validate_config(
    config: Annotated[str, config_option] = "render.config.yaml",
) -> None:
    """Validate render.config.yaml schema."""
    cfg = load_config(Path(config))
    typer.echo(f"[validate-config] Config is valid. Version={cfg.version}")
    typer.echo(f"  source_root : {cfg.render.source_root}")
    typer.echo(f"  target_root : {cfg.render.target_root}")
    typer.echo(f"  transforms  : {len(cfg.modifications.transforms)}")


@app.command("render-copy")
def cmd_render_copy(
    config: Annotated[str, config_option] = "render.config.yaml",
) -> None:
    """Copy source files to target_root applying include/exclude filters."""
    cfg = load_config(Path(config))
    base_dir = Path(config).parent
    typer.echo(
        f"[render-copy] Copying {cfg.render.source_root} -> {cfg.render.target_root}"
    )
    copy_source_to_target(cfg, base_dir)


@app.command("apply-mods")
def cmd_apply_mods(
    config: Annotated[str, config_option] = "render.config.yaml",
) -> None:
    """Apply all declared transforms to the rendered output."""
    cfg = load_config(Path(config))
    base_dir = Path(config).parent
    typer.echo(f"[apply-mods] Applying {len(cfg.modifications.transforms)} transform(s)...")
    apply_modifications(cfg, base_dir)


@app.command("all")
def cmd_all(
    config: Annotated[str, config_option] = "render.config.yaml",
) -> None:
    """Run: validate-config -> render-copy -> apply-mods."""
    cfg_path = Path(config)
    base_dir = cfg_path.parent
    cfg = load_config(cfg_path)
    typer.echo("=== Step 1: validate-config ===")
    typer.echo(f"[validate-config] Config valid. Transforms: {len(cfg.modifications.transforms)}")

    typer.echo("\n=== Step 2: render-copy ===")
    copy_source_to_target(cfg, base_dir)

    typer.echo("\n=== Step 3: apply-mods ===")
    apply_modifications(cfg, base_dir)

    typer.echo("\n=== Done ===")


@app.command("show-plan")
def cmd_show_plan(
    config: Annotated[str, config_option] = "render.config.yaml",
) -> None:
    """Print a human-readable summary of all planned transforms."""
    cfg = load_config(Path(config))
    typer.echo(
        f"[show-plan] {len(cfg.modifications.transforms)} transform(s) in {config}:\n"
    )
    for i, t in enumerate(cfg.modifications.transforms, 1):
        source = (
            t.source_file
            or t.yaml_file
            or ("<inline yaml_value>" if t.yaml_value else "<none>")
        )
        match_info = ""
        if t.match:
            if t.match.text:
                match_info = f" | match.text={t.match.text!r} (occ={t.match.occurrence})"
            elif t.match.regex:
                match_info = f" | match.regex={t.match.regex!r} (occ={t.match.occurrence})"
            if t.match.start:
                match_info += f" | start={t.match.start!r}"
            if t.match.end:
                match_info += f" | end={t.match.end!r}"
        typer.echo(
            f"  {i:2d}. [{t.type:14s}] id={t.id!r}"
            f"\n       target={t.target_file!r}"
            f"\n       source={source!r}{match_info}"
        )


@app.command("init-skeleton")
def cmd_init_skeleton() -> None:
    """Generate base mods/ structure and example render.config.yaml for onboarding."""
    base = Path(".")
    dirs = [
        "mods/templates/snippets",
        "mods/replace-blocks",
        "mods/delete-blocks",
        "vendor-source",
        "rendered",
        "tools",
        "tests",
        "scripts",
    ]
    for d in dirs:
        (base / d).mkdir(parents=True, exist_ok=True)
        typer.echo(f"  [init] Created dir: {d}")

    config_file = base / "render.config.yaml"
    if not config_file.exists():
        config_file.write_text(
            "version: 1\n\nupstream:\n  mode: submodule\n  remote: ''\n"
            "  branch: main\n  path: vendor-source/upstream-chart\n  tracked_ref: ''\n\n"
            "render:\n  source_root: vendor-source/upstream-chart\n"
            "  target_root: rendered/app-chart\n  include:\n    - 'templates/**'\n"
            "    - 'Chart.yaml'\n    - 'values.yaml'\n  exclude: []\n\n"
            "modifications:\n  markers:\n"
            "    start: '# BEGIN custom-block: {id}'\n"
            "    end: '# END custom-block: {id}'\n  transforms: []\n",
            encoding="utf-8",
        )
        typer.echo("  [init] Created: render.config.yaml")
    else:
        typer.echo("  [init] render.config.yaml already exists, skipping.")

    typer.echo("[init-skeleton] Done.")


# ---------------------------------------------------------------------------
# Source management commands
# ---------------------------------------------------------------------------


@app.command("source-init")
def cmd_source_init(
    config: Annotated[str, config_option] = "render.config.yaml",
    skip_git: Annotated[bool, typer.Option("--skip-git", help="Skip git operations (PoC mode)")] = False,
) -> None:
    """Initialize the upstream submodule (or skip in PoC mode)."""
    cfg = load_config(Path(config))
    if cfg.upstream is None:
        typer.echo("[source-init] No upstream config found.", err=True)
        raise typer.Exit(1)

    up = cfg.upstream
    up_path = Path(config).parent / up.path

    if up_path.exists() and any(up_path.iterdir()):
        typer.echo(f"[source-init] Upstream path already exists and is non-empty: {up_path}")
        typer.echo("[source-init] Nothing to do.")
        return

    if skip_git or not up.remote:
        typer.echo(
            f"[source-init] Skipping git operations (skip_git={skip_git}, remote={up.remote!r})."
        )
        typer.echo("[source-init] In PoC mode, populate vendor-source/ manually.")
        return

    typer.echo(f"[source-init] Adding submodule: {up.remote} -> {up.path}")
    _run_git(["git", "submodule", "add", "-b", up.branch, up.remote, up.path])
    _run_git(["git", "submodule", "update", "--init", "--recursive"])
    typer.echo("[source-init] Done.")


@app.command("source-sync")
def cmd_source_sync(
    config: Annotated[str, config_option] = "render.config.yaml",
) -> None:
    """Update the upstream submodule to the latest commit."""
    cfg = load_config(Path(config))
    if cfg.upstream is None:
        typer.echo("[source-sync] No upstream config found.", err=True)
        raise typer.Exit(1)

    typer.echo("[source-sync] Updating submodule...")
    _run_git(["git", "submodule", "update", "--remote", "--merge"])
    typer.echo("[source-sync] Done.")


@app.command("source-status")
def cmd_source_status(
    config: Annotated[str, config_option] = "render.config.yaml",
) -> None:
    """Show upstream submodule status."""
    cfg = load_config(Path(config))
    if cfg.upstream is None:
        typer.echo("[source-status] No upstream config defined.")
        return

    up = cfg.upstream
    up_path = Path(config).parent / up.path
    exists = up_path.exists()
    non_empty = exists and any(up_path.rglob("*"))

    typer.echo("[source-status]")
    typer.echo(f"  mode        : {up.mode}")
    typer.echo(f"  remote      : {up.remote or '(not set)'}")
    typer.echo(f"  branch      : {up.branch}")
    typer.echo(f"  path        : {up.path}")
    typer.echo(f"  tracked_ref : {up.tracked_ref or '(not set)'}")
    typer.echo(f"  dir exists  : {exists}")
    typer.echo(f"  non-empty   : {non_empty}")

    # Try to get current submodule commit
    current_commit = _get_submodule_commit(up_path)
    typer.echo(f"  current_sha : {current_commit or '(unknown)'}")


def _get_submodule_commit(path: Path) -> Optional[str]:
    """Return the current HEAD commit of the submodule, if available."""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _run_git(cmd: list[str]) -> None:
    """Run a git command, raising on failure with a clear message."""
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        typer.echo(f"[ERROR] Git command failed: {' '.join(cmd)}", err=True)
        typer.echo(f"  stdout: {result.stdout}", err=True)
        typer.echo(f"  stderr: {result.stderr}", err=True)
        raise typer.Exit(1)
    if result.stdout.strip():
        typer.echo(result.stdout.strip())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
