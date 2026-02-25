# file-modifier

A portable, declarative tool for customizing upstream Helm charts (or any template files) without modifying the upstream source directly.

---

## What problem does it solve?

When you depend on an open-source Helm chart (or any upstream template), you often need small customizations — extra env vars, init containers, label changes. The traditional options are:

- **Fork & modify**: Hard to keep in sync with upstream.
- **Kustomize overlays**: Complex, YAML-heavy, limited to Kustomize-compatible resources.
- **Bash scripts**: Non-portable, hard to review, fragile.

**file-modifier** lets you declare customizations in a single YAML file (`render.config.yaml`), store modification blocks as separate files in `mods/`, and re-generate the `rendered/` output at any time — idempotently, portably, without touching the upstream source.

---

## Folder structure

```
repo/
├─ .gitmodules                          # (when using real submodule)
├─ vendor-source/
│  └─ upstream-chart/                   # upstream (submodule or local PoC)
│     ├─ Chart.yaml
│     ├─ values.yaml
│     └─ templates/
│        └─ deployment.yaml
├─ rendered/
│  └─ app-chart/                        # generated output (DO NOT edit manually)
├─ mods/
│  ├─ templates/
│  │  ├─ _customizations.tpl            # custom Helm helpers
│  │  └─ snippets/
│  │     ├─ initcontainers.include.tpl
│  │     ├─ app-extra-env.include.tpl
│  │     ├─ worker-extra-env.include.tpl
│  │     └─ sidecars.include.tpl
│  ├─ replace-blocks/
│  │  └─ app-env.block
│  └─ delete-blocks/
│     └─ deployment-app-original-env.block
├─ tools/
│  └─ render_sync.py                    # CLI tool (Python)
├─ tests/
│  └─ test_render_sync.py
├─ render.config.yaml                   # declarative config
├─ requirements.txt
├─ scripts/
│  ├─ render.sh                         # Unix wrapper
│  └─ render.ps1                        # Windows wrapper
└─ Makefile
```

---

## Recommended workflow

### Install dependencies

```bash
pip install -r requirements.txt
```

### First time (initialize upstream submodule)

```bash
python tools/render_sync.py source-init
```

For the local PoC (no real remote), `vendor-source/upstream-chart/` is already present.
Use `--skip-git` to skip git operations in PoC mode.

### Sync upstream to latest

```bash
python tools/render_sync.py source-sync
```

### Generate rendered output

```bash
python tools/render_sync.py all
```

This runs: `validate-config` → `render-copy` → `apply-mods`.

> **Note:** `source-sync` is NOT called automatically by `all`. You control when to pull upstream updates.

### Other useful commands

```bash
# Preview what transforms will be applied
python tools/render_sync.py show-plan

# Check upstream submodule status
python tools/render_sync.py source-status

# Validate config schema only
python tools/render_sync.py validate-config

# Scaffold mods/ structure for a new project
python tools/render_sync.py init-skeleton
```

### Using wrappers (cross-platform)

```bash
# Unix/macOS
./scripts/render.sh all

# Windows PowerShell
.\scripts\render.ps1 all
```

---

## The 4 operations

### 1. `copy_file`
Copies a file from `mods/` (or inline) directly to `rendered/`, creating parent directories.
Does not use markers. Useful for adding completely new files.

```yaml
- id: "copy-custom-helpers"
  type: "copy_file"
  target_file: "templates/_customizations.tpl"
  source_file: "mods/templates/_customizations.tpl"
```

### 2. `insert_after`
Inserts content after the Nth occurrence of a text match.
Wraps the inserted block with `# BEGIN custom-block: <id>` / `# END custom-block: <id>` markers.
**Idempotent**: if the marker already exists, replaces the block content instead of duplicating.

```yaml
- id: "deployment-initcontainers-include"
  type: "insert_after"
  target_file: "templates/deployment.yaml"
  match:
    text: "spec:"
    occurrence: 2
  source_file: "mods/templates/snippets/initcontainers.include.tpl"
```

### 3. `replace_block`
Replaces the block of lines between `match.start` and `match.end` (inclusive) with new content.
Useful for substituting an entire section (e.g., a whole `env:` block).

```yaml
- id: "replace-app-env-block"
  type: "replace_block"
  target_file: "templates/deployment.yaml"
  match:
    start: "          env:"
    end: "          ports:"
    occurrence: 1
  source_file: "mods/replace-blocks/app-env.block"
```

### 4. `delete_block`
Removes an exact block of text from the target file.
If the block is not found, emits a warning and continues (V1 behavior).

```yaml
- id: "delete-original-app-env"
  type: "delete_block"
  target_file: "templates/deployment.yaml"
  source_file: "mods/delete-blocks/deployment-app-original-env.block"
```

---

## The 3 content sources

Each transform that needs content must use **exactly one** of:

| Source        | Description                                                        |
|---------------|--------------------------------------------------------------------|
| `source_file` | Path to a file in `mods/`                                          |
| `yaml_file`   | Path to a block file (same as `source_file`, different label)      |
| `yaml_value`  | Inline content in the config YAML                                  |

If zero or more than one source is defined, the tool fails with a clear error.

---

## What is NOT supported (V1)

- Patch files (`.patch` / `git apply`)
- Kustomize / post-renderers
- AST-aware YAML/Go-template editing
- No automatic `source-sync` in `all` pipeline (intentional)

---

## Adding a new modification

1. Create or update a file in `mods/` with the content you want to insert/replace.
2. Add a new entry to `modifications.transforms` in `render.config.yaml`:

```yaml
- id: "my-new-mod"
  type: "insert_after"
  target_file: "templates/deployment.yaml"
  match:
    text: "containers:"
    occurrence: 1
  source_file: "mods/templates/snippets/my-snippet.tpl"
```

3. Run:

```bash
python tools/render_sync.py all
```

4. Review `rendered/app-chart/templates/deployment.yaml` to verify.

---

## Important

> `vendor-source/` is **never modified**. All output goes to `rendered/`. Treat `rendered/` as a build artifact — regenerate it any time with `python tools/render_sync.py all`.

---

## Running tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```
