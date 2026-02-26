# Copilot Instructions for punkogo/file-modifier (render_sync)

## Project overview

`render_sync` is a Python 3 CLI tool (`tools/render_sync.py`) that:

1. Copies a filtered subset of files from `vendor-source/` → `rendered/` (`render-copy`)
2. Applies a declarative, ordered set of text transforms (`apply-mods`) defined in `render.config.yaml`

Transform types: `copy_file`, `insert_after`, `replace_block`, `delete_block`.
Config schema is validated by Pydantic models inside `render_sync.py`.
The CLI is built with Typer; all configuration comes from `render.config.yaml`.

## Repository structure

```
tools/render_sync.py          # The entire CLI + config schema + transform logic
tests/test_render_sync.py     # pytest unit tests
render.config.yaml            # Example config (demonstrates all transform types)
mods/                         # Local customisation snippets (source content for transforms)
vendor-source/                # Upstream files — NEVER modified
rendered/                     # Output of render-copy + apply-mods — treated as a build artifact
Makefile                      # Convenience targets (see below)
requirements.txt              # Runtime + test dependencies
.pre-commit-config.yaml       # ruff (lint + format) and standard pre-commit hooks
```

## How to run tests and lint

```bash
# Install dependencies
pip install -r requirements.txt

# Run the full test suite (must pass on Python 3.9, 3.11, 3.12)
python -m pytest tests/ -v

# Lint
ruff check .
ruff format --check .

# Or via Make
make test
make lint
```

CI runs both `pytest` and `pre-commit` (ruff) on every push and pull request.

## Definition of "done"

A task is complete when **all** of the following are true:

1. `python -m pytest tests/ -v` exits 0 — no failures, no errors.
2. `ruff check .` exits 0 — no lint issues.
3. `ruff format --check .` exits 0 — no formatting issues.
4. All unresolved, non-outdated review threads on the PR are addressed.

**If the above four conditions are met, stop.** Do not add more commits.

## Rules to prevent infinite review loops

These rules exist because Copilot PR reviewer and Copilot coding agent can create
an infinite cycle if not constrained:

### 1. Verify before acting

Before claiming a review comment needs a code fix, run `ruff check .` and
`python -m pytest tests/`. If both pass, the code already meets quality standards.
Do not make a change simply because a reviewer comment exists — check whether the
issue is real in the *current* file content.

### 2. Respect resolution status

- If a review thread has `is_resolved: true`, it is done. Do not touch the
  related code.
- If a review thread has `is_outdated: true`, it refers to code that no longer
  exists. Do not address it.
- Only act on threads that are **both unresolved and not outdated**.

### 3. Do not add meta-content to README

Never add "PR Review Resolution Tables", "Changelog" sections, or other
process-tracking prose to `README.md`. README.md is product documentation.
Process tracking belongs in PR descriptions or commit messages, not in files
committed to the repo.

### 4. No duplicate safety guards

If a safety check already exists at the top of a function (e.g. a
`try/except relative_to()` guard before `shutil.rmtree`), do not add a second
equivalent guard lower in the same function. One check in the right place is
correct; a second check adds confusion without value.

### 5. Minimal changes only

When fixing a review comment, change only the lines required to address it.
Do not refactor surrounding code, rename variables, add helper functions, or
expand test coverage beyond what the comment asks for.

### 6. Do not open new issues or PRs to address review comments

Address review comments by committing directly to the current branch.
Do not create a new issue or open a new PR to fix something in an existing PR.

## Code conventions

- **Python version**: target 3.9+ syntax (no walrus operators or 3.10+ match statements in production paths; 3.9 is the minimum tested version in CI).
- **Imports**: only `from __future__ import annotations` at the top; no unused imports. Run `ruff check` to verify.
- **Pydantic**: use `model_validator(mode="after")` for cross-field validation.
- **Typer**: commands are snake_case in Python, kebab-case on the CLI (Typer does this automatically).
- **Error handling**: use `typer.echo(..., err=True)` then `raise SystemExit(1)` for fatal errors — never `sys.exit()`.
- **Tests**: use `pytest` with `tmp_path` fixtures. Keep one assertion class per transform type. Mock `subprocess.run` for submodule commands. Always assert on `result.exit_code` when using `CliRunner.invoke`.

## What this project does NOT do

- No YAML-aware merging — all transforms are text-based.
- No conditional transforms.
- No multi-file glob targets (each transform targets exactly one `target_file`).
- No secret management.
