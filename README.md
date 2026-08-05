# OrcaSlicer Profile Manager

Build OrcaSlicer presets that actually load.

OrcaSlicer discards a malformed user preset **without an error** — it just never appears in the UI. This tool validates presets against OrcaSlicer's real option tables, enforces the undocumented user-preset format rules, and reads OrcaSlicer's own log to confirm what actually loaded.

Works as a CLI and as an agent skill for Claude Code, Codex, Antigravity, and Cursor.

## Install

```bash
uv venv && uv pip install -r requirements.txt
```

Python 3.9+. Only two dependencies: `jsonschema`, `referencing`.

## Quick start

Find out what OrcaSlicer actually did with your presets:

```console
$ python validate_orca.py doctor

Dropped presets (1)
  [DROPPED] (process) 0.40mm Mutual Support @BBL X1C 0.8 nozzle
            unresolved parent: 0.40mm Corrected @BBL X1C 0.8 nozzle

Preset counts (files on disk vs presets OrcaSlicer loaded)
  [MISMATCH] process   files=2 loaded=1
```

Clone a built-in profile into your user directory, correctly formatted:

```bash
python validate_orca.py clone filament "Bambu PLA Basic @BBL X1C" \
  --name "My PLA" --set nozzle_temperature='["225"]'
```

Bad keys are rejected before anything is written:

```console
$ python validate_orca.py clone filament "PolyTerra PLA @BBL X1C" \
    --name "My PLA" --set layer_height='"0.4"'

Clone aborted: --set used keys OrcaSlicer will silently ignore:
  ERROR: 'layer_height' is a process setting and has no effect in a filament
         preset. OrcaSlicer silently ignores it; move it to the process preset.
```

## Recipes

Worked, copy-pasteable setups in [references/recipes.md](skills/orcaslicer-profile-manager/references/recipes.md). Each names the exact keys, the values, why, and what **cannot** be expressed in a preset at all.

| Recipe | Covers |
|---|---|
| **Large nozzle** (0.6 / 0.8 / 1.0 mm) | Layer height and line width ratios, wall loops and speeds, volumetric ceilings, temperature, pressure advance, tree support tip sizing |
| **PLA / PETG mutual support** | Zero-gap interface, solid roof, interface speed and pattern, bed temperature compromise, `filament_is_support`, the three-preset-per-material structure |
| **Warping mitigation** | Brim, first layer width, elephant foot, bed temperature, wall count, cooling |

## What it does

- **`doctor`** — reads OrcaSlicer's log and reports dropped presets, stripped keys, and a files-on-disk vs presets-loaded checksum. Exits non-zero, so it works as a gate.
- **Known-key validation** — every setting checked against option tables extracted from the OrcaSlicer binary. Catches typos and wrong-domain keys, which OrcaSlicer ignores in silence.
- **User-preset format enforcement** — the undocumented rules that decide whether a preset appears at all: no `type`/`setting_id`, a system parent, `from: "User"`, a `version`.
- **Preflight checks** — aborts when OrcaSlicer is running, when a parent does not exist, or when the parent does not support the printer you are binding to.
- **`clone`** — copies a built-in profile, writes the `.info` sidecar, and flattens automatically when the source is a user preset.
- **`inspect` / `diff` / `list-profiles`** — resolve inheritance chains, compare deltas, search installed vendors.

## Use as an agent skill

```bash
mkdir -p ~/.claude/skills   # or ~/.gemini/config/skills for Antigravity
ln -s "$PWD/skills/orcaslicer-profile-manager" ~/.claude/skills/
```

Symlink rather than copy — the script resolves its `schemas/` directory through the link. Codex and Cursor pick up `.agents/` and `plugin.json` automatically.

## Documentation

[SKILL.md](skills/orcaslicer-profile-manager/SKILL.md) is the reference: the user-preset format, the serialization gotchas that are documented nowhere upstream, and the pre-generation interview.

Tests: `python -m unittest discover -s tests`
