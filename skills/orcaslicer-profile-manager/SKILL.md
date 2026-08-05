---
name: orcaslicer-profile-manager
description: "Locate, search/list vendors & profiles, inspect DAG chains, diff profile deltas, clone, de-link inheritance, backup/migrate, and validate OrcaSlicer 3D printing config profiles (vendor, machine, filament, process, material-db) across macOS, Linux, and Windows."
---

# OrcaSlicer Profile Manager Skill

This skill equips AI coding assistants (Claude Code, Codex, Antigravity, Cursor, etc.) to **locate**, **list vendors/profiles**, **inspect profile DAG chains**, **diff profile deltas**, **clone/copy**, **de-link inheritance**, **edit**, **resolve inheritance for**, **backup/migrate**, and **validate** OrcaSlicer 3D printer configuration JSON files across operating systems (macOS, Linux, Windows).

---

## Locating the Script

> [!IMPORTANT]
> Every command in this skill is written as `python validate_orca.py <subcommand>`.
> `validate_orca.py` means **the copy that sits next to this `SKILL.md` file**, not a
> file in your current working directory. Resolve it relative to the skill directory.
> Do not assume the current working directory is the repository root.

```bash
# Resolve the script from the skill directory, whatever the current directory is.
SKILL_DIR="$(dirname "$(readlink -f ~/.claude/skills/orcaslicer-profile-manager/SKILL.md)")"
python "$SKILL_DIR/validate_orca.py" locate
```

The script needs its sibling `schemas/` directory. It finds that directory through
`Path(__file__).resolve()`, which follows the symlink back to the repository. Run the
script through the symlink. Do not copy the file somewhere else.

**Dependencies:** the script needs `jsonschema` and `referencing` (see
`requirements.txt`). If the system Python does not have them, use the interpreter of
the repository virtual environment.

---

## Core OrcaSlicer Architecture & Serialization Rules

OrcaSlicer configuration properties (originating from C++ `ConfigOption` in `libslic3r`) strictly enforce a **string-encoded** serialization format for scalar values:

| C++ Type | Expected JSON Format | Example Valid Value | Invalid Format |
|---|---|---|---|
| `bool` | Binary or boolean string | `"1"`, `"0"`, `"true"`, `"false"` | `true`, `1` |
| `int` | String integer | `"7500"` | `7500` |
| `float`/`double` | String double | `"0.4"`, `"215.0"` | `0.4` |
| `percent` | String appended with `%` | `"15%"` | `0.15`, `15` |
| `setting_id` | Alphanumeric plus `_` and `-`; observed length 5–16 (system profiles only — see §5) | `"GFSG96_00"`, `"CUSTPLAPRINTER01"` | `"has space"`, `"has@sign"` |
| `filament_id` | String. OrcaSlicer enforces no length. Bambu AMS ids use the 5-char `GF` + 3 form | `"GFL01"`, `"GFG96"` | — |
| `extruder_array` | Any scalar field may be a single value or an array of one value per extruder | `"0.98"` or `["0.98", "0.98"]` | mixing types within the array |

---

## Undocumented Serialization Gotchas

The table above covers the documented rules. The five rules below are not
documented upstream. Each one was confirmed against the OrcaSlicer 2.4.2 option
tables and the bundled vendor profiles.

### 1. The `"nil"` sentinel

Filament-level printer overrides use the literal string `"nil"` to mean "use the
printer default". The sentinel is set per extruder variant. Affected keys
include:

- `filament_retraction_length`
- `filament_retraction_speed`
- `filament_wipe`
- `filament_wipe_distance`
- `filament_z_hop`
- `filament_retract_before_wipe`

A mixed array such as `["1","nil","1","nil"]` is normal. It overrides variants 1
and 3 and leaves variants 2 and 4 at the printer default.

### 2. Per-extruder-variant array arity is not uniform

Many options serialize as an array with one element per entry in
`print_extruder_variant` or `filament_extruder_variant`. The X1C has 2 variants.
The H2D and X2D have 4.

Arity is **not** uniform across keys inside one preset. In
`PolyTerra PLA @BBL X1C`, `nozzle_temperature` is `["220","220"]` (2 elements)
while `hot_plate_temp` is `["55"]` (1 element).

> [!IMPORTANT]
> **Rule: match the arity that the parent profile uses for that same key.** Read
> the parent value first with `inspect`. Do not assume the variant count.

### 3. Unknown keys are silently ignored

OrcaSlicer discards a key it does not recognize. It reports no error. Real vendor
bundles ship dead keys, so **copying a key from a vendor profile does not prove
the key is real**. Confirmed dead keys:

| Dead key | Why it is dead |
|---|---|
| `bottom_surface_line_width` | Never existed. Bottom solid surfaces use `internal_solid_infill_line_width`. |
| `keep_fan_always_on` | Legacy. Superseded by `reduce_fan_stop_start_freq`. |
| `tree_support_bramch_diameter_angle` | Prusa typo for `branch`. |
| `slow_down_curled_perimeters` | Cubicon typo. The real key is `slowdown_for_curled_perimeters`. |
| `epoxy_resin_plate_temp`, `customized_plate_temp` | Creality-only. Not in the OrcaSlicer option table. |
| `nozzle_temperature_intial_layer` | Typo for `nozzle_temperature_initial_layer`. |

### 4. Real `setting_id` and `filament_id` character sets

- `setting_id` uses alphanumeric characters plus underscore and hyphen. Observed
  length is 5 to 16 characters, for example `GFSG96_00`. The 16-char base62
  description in the table above is the upper bound, not a fixed width.
- `filament_id` has **no enforced length** in OrcaSlicer. The 8-character rule is
  a Bambu AMS RFID convention, not a slicer constraint. Bambu's own ids use the
  5-character `GF` + 3 form.

### 5. OrcaSlicer must not be running while you write presets

OrcaSlicer rewrites its preset state on exit. It discards files that were written
while it was running.

1. Quit OrcaSlicer.
2. Write the preset files.
3. Start OrcaSlicer. New presets appear.

---

## User Presets vs System Presets: Two Different JSON Shapes

> [!WARNING]
> This is the single most common way a generated profile silently fails to appear
> in OrcaSlicer. It produces **no error** — the file and its `.info` companion
> just don't show up in the UI. This was reverse-engineered from
> [OrcaSlicer#12223](https://github.com/OrcaSlicer/OrcaSlicer/issues/12223) and
> confirmed against real installed profiles; it is undocumented upstream.

OrcaSlicer's preset loader (`PresetCollection::load_user_presets`) enforces a
**stricter, different shape for user presets than for system presets**, even
though both are `.json` files with mostly the same key names:

| Field | System preset (built-in bundles) | User preset (`user/default/<domain>/*.json`) |
|---|---|---|
| `type` | Present (`"process"`, `"filament"`, `"machine"`) | **Must be absent.** Its presence alone gets the whole file silently ignored. |
| `setting_id` | Present, 16-char (or short legacy code) | **Must be absent** from the JSON body. Lives only in the paired `.info` file. |
| `compatible_printers` | Present (full compatibility list) | Absent unless you are deliberately restricting compatibility. |
| `instantiation` | Sometimes present | Not used. |
| `inherits` | Present, points further up the built-in chain | **Required, non-empty**, must name an existing profile (system or user). A standalone preset with no `inherits` is rejected — full independence from a parent is not a concept OrcaSlicer's preset system supports. |
| `from` | Absent | `"User"` |
| `version` | Sometimes absent | **Required.** A user preset with no `version` field is silently skipped by the loader even if everything else is correct (confirmed empirically). |
| `<domain>_settings_id` (`print_settings_id` / `filament_settings_id` / `printer_settings_id`) | — | Should be set, equal to `name`. |
| Body | Every setting — a complete, self-contained definition | Only the keys that differ from `inherits` (a *diff*), unless you deliberately flattened everything with `--de-link-inherits` |

**`validate_orca.py clone` produces the correct shape automatically** — do not
hand-write user preset JSON. If you ever do write one by hand (e.g. via
`write_to_file`), run `validate_orca.py auto <file> --json` afterward and check
the `warnings` array; it lints exactly these rules (tagged
`[user-preset format]`) even when the file is otherwise schema-valid, because
schema-valid does **not** mean OrcaSlicer's loader will show it.

---

## Before You Generate: Operator Interview

> [!IMPORTANT]
> Complete all six steps below **before** you generate any profile. Each step
> comes from an observed failure. Use the `ask_question` tool for every question.
> Do not guess an answer.

### 1. Classify every requested change by domain first

Sort each requested change into `process`, `filament`, or `machine` **before you
write anything**. OrcaSlicer silently ignores a process key that is placed in a
filament preset. It reports no error, and the setting does nothing.

About 60% of a typical "tune my filament" request is actually process domain.
Layer height, line width, wall count, speeds, supports, and brims are all
process. Temperatures, flow, cooling fans, and volumetric limits are filament.

Split the work into a process preset and a filament preset. **Tell the operator
the split before you build.** See [Recipes](references/recipes.md), which marks
the domain of every key.

### 2. Printer-bound or universal?

See § 5 "Cloning Profiles: Inherited vs Independent" for the exact question and
the naming rule. Ask it now, not after you generate.

### 3. Which build plate?

The answer selects which of the twelve `*_plate_temp` keys to set. OrcaSlicer
reads only the pair for the plate that the operator selects, so a correct value
on the wrong plate does nothing. **Do not guess the plate.** See
[Recipes](references/recipes.md) § 2.4 for the full twelve-key table.

### 4. Single-material or multi-material?

A multi-material job needs AMS slot indices. `support_interface_filament` and
`support_filament` hold an extruder/filament index that depends on the operator's
own slot layout. Only the operator knows it. Ask for the slot numbers, or leave
the keys at `"0"` (auto) and tell the operator to set them in the UI.

### 5. Confirm that the parent profile exists

A hallucinated parent name is a real observed failure. `Generic PETG @BBL X1C`
does **not** exist. The real profiles are `Generic PETG HF @BBL X1C` and
`Generic PETG @base`.

Verify every name before you put it in `inherits`:
```bash
python validate_orca.py list-profiles --domain filament --query "PETG"
```

### 6. Confirm the parent's `compatible_printers`

The parent's `compatible_printers` must include the exact machine preset name you
bind to, for example `Bambu Lab X1 Carbon 0.8 nozzle`. If it does not, the preset
will not appear for that printer.
```bash
python validate_orca.py inspect "<parent name>" --json
```

---

## 1. Locating Built-in & User Directories (`locate`)

Discover installed OrcaSlicer application and user profile directories on the host system:
```bash
python validate_orca.py locate
```

---

## 2. Listing Vendor Ecosystems & Profiles (`list-vendors`, `list-profiles`)

### List Installed Vendors (`list-vendors`)
Lists all installed 3D printer hardware vendor ecosystems (BBL, Creality, Voron, Prusa, RatRig, etc.) along with model, printer, filament, and process profile counts:
```bash
python validate_orca.py list-vendors
python validate_orca.py list-vendors --json
```

### Search & List Profiles (`list-profiles`)
Search installed built-in & user profiles by domain (`machine`, `filament`, `process`, `vendor`), vendor, or text query:
```bash
python validate_orca.py list-profiles --domain filament --query "PLA"
python validate_orca.py list-profiles --vendor Voron --detail
```

---

## 3. Deep Profile Inspection (`inspect`)

Examine a profile's identity, DAG inheritance chain (`Profile -> Parent -> Base`), key domain parameters (thermals, speeds, flow, AMS IDs), schema health, and child dependents:
```bash
python validate_orca.py inspect "Bambu PLA Basic @BBL X1C"
python validate_orca.py inspect ./custom_process.json --json
```

---

## 4. Comparing / Diffing Profiles (`diff`)

Compare two profiles to pinpoint parameter value deltas (temperature, speed, infill, flow rate differences):
```bash
python validate_orca.py diff "0.20mm Standard @Voron" "0.20mm HighSpeed Voron"
python validate_orca.py diff ./profileA.json ./profileB.json --json
```

---

## 5. Cloning Profiles: Inherited vs Independent (`clone`)

> [!IMPORTANT]
> **Output Location:** Omit the `--out` parameter entirely! The script will automatically resolve the exact cross-platform user profile path and save the file and `.info` cache there.
> **Interview the Operator:** Use the `ask_question` tool to ask the operator if the profile should be bound strictly to a specific printer/nozzle or if it should be universal.
> - **Printer-bound:** Add the exact printer suffix expected by OrcaSlicer (e.g., `@BBL X1C 0.8 nozzle`) to the `--name`.
> - **Universal:** Do NOT append a printer suffix to `--name`. This is the default now — `clone` never carries `compatible_printers` over from the source unless you pass `--compatible-printers` yourself.

Both options below always set `"inherits"` to the exact profile being cloned
(never a grandparent) and always strip `type`/`setting_id`/`compatible_printers`
— see the format table above for why. They differ only in how much of the
parent's resolved values get redeclared in the child body:

### Option A: Standard Inherited Clone (Child Profile) (Recommended default)
Body is just your `--set` overrides; everything else is inherited live from the
named parent, so future edits to the parent chain (e.g. a stock profile update)
continue to flow through — same as a GUI "Save As" of a modified preset.
```bash
python validate_orca.py clone filament "Bambu PLA Basic @BBL X1C" \
  --name "Custom Bambu PLA Basic" \
  --set nozzle_temperature='["225"]'
```

### Option B: Value-Locked Clone (`--de-link-inherits`)
Flattens every resolved parent value into the child body explicitly, so the
child's *values* can never drift when the parent chain is updated. Note:
`"inherits"` is still kept (pointing at the cloned profile) because OrcaSlicer
rejects a preset with no inherits at all — this option locks values, it cannot
achieve full independence from the parent's existence/name.
```bash
python validate_orca.py clone process "0.20mm Standard @Voron" \
  --name "0.20mm Value-Locked Voron" \
  --de-link-inherits \
  --set outer_wall_speed='"180"'
```

---

## 6. Generating Templates & Validating Profiles

Generate starter skeleton JSON:
```bash
python validate_orca.py template <domain> --out <path/to/profile.json>
```

Validate files or directories against Draft 2020-12 schemas:
```bash
python validate_orca.py auto <path/to/profile.json> --json
```

---

## 7. CLI Tool Subcommand Quick Reference

`validate_orca.py` provides a complete programmatic interface:

| Subcommand | Description | LLM JSON Flag |
|---|---|---|
| `locate` | Discover installed built-in app & user profile paths | N/A |
| `list-vendors` | Summarize installed vendor ecosystems & counts | `--json` |
| `list-profiles` | Search & list profiles by domain/vendor/query | `--json` |
| `inspect` | Detailed report on DAG chain, key parameters, schema health | `--json` |
| `diff` | Highlight parameter value deltas between two profiles | `--json` |
| `clone` | Copy profile, assign new 16-char `setting_id`, de-link, & validate | N/A |
| `template` | Output starter skeleton JSON | stdout / `--out` |
| `auto` | Auto-detect domain & validate against JSON schema | `--json` |

---

## 8. Reference Manuals

- [Inspection & Diffing Guide](references/inspection_and_diffing_profiles.md)
- [Finding & Cloning Built-in Profiles](references/finding_and_cloning_builtin_profiles.md)
- [Generation Guide](references/generation_rules.md)
- [Editing & DAG Inheritance Guide](references/editing_rules.md)
- [Worked Profile Recipes](references/recipes.md)
