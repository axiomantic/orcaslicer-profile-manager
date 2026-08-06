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

The table above covers the documented rules. The seven rules below are not
documented upstream. Rules 1 to 5 were confirmed against the OrcaSlicer 2.4.2
option tables and the bundled vendor profiles. Rules 6 and 7 were confirmed on a
real machine and in the OrcaSlicer source.

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

### 6. A user preset cannot inherit from another user preset

A user preset must point `inherits` at a **system** preset. If `inherits` names
another user preset, OrcaSlicer drops the child silently. The UI shows no error.
The debug log contains one line for each dropped preset:

```
can not find parent <parent name> for config <child name>!
```

The summary line in the same log reports the true count, for example
`loaded 1 presets` when 2 JSON files are on disk.

**Mechanism** (`src/libslic3r/Preset.cpp`, `PresetCollection::load_presets`):
the loader stages each preset it reads in a local `presets_loaded` deque. It
merges that deque into the searchable `m_presets` collection only **after** the
directory loop ends. The parent lookup searches `m_presets`. A sibling user
preset is therefore never visible as a parent during the pass that reads it.

**Load order is not the cause.** The log shows a parent preset that logs
"load config successful" before its child, and the child still fails.

**This is not an explicit rule in the code.** OrcaSlicer's inherits-resolution
code holds no `is_system` check. User-from-user inheritance is not forbidden by
design. It simply does not resolve on the disk-loading path, and the child is
lost.

> [!WARNING]
> **A shared "base" preset with several "variant" presets that inherit from it is
> not possible in OrcaSlicer.** Each variant must be flat. Each variant inherits
> directly from a system preset and repeats every shared value in its own body.
> This means you cannot edit a shared value in one place. A change to a shared
> value must be applied to every variant file.

**Repair and prevention:**

- `tools/flatten_user_inherits.py` repairs presets that already have a
  user-preset parent. It inlines the parent values and repoints `inherits` at the
  nearest system ancestor. `--apply` first copies each original preset, and its
  paired `.info`, to `<preset-dir>-backup-<YYYYmmdd-HHMMSS>/`. That directory is a
  sibling of the preset directory, because OrcaSlicer owns and rewrites its own
  directory. The default is a dry run, which writes nothing.
- `clone` flattens automatically when the source profile is a user preset. It
  sets `inherits` to the nearest system ancestor and inlines the values that the
  user parent supplied.

A system parent that is not the direct value source is correct. OrcaSlicer uses
the parent config only as a starting point (`preset.config = inherit_preset->config;`)
and then applies the child's own keys on top.

### 7. The G-code is the ground truth for what a setting did

A preset value tells you what you asked for. It does not tell you which key
governed a given extrusion. One setting can change how the slicer classifies a
region, and a different key then controls the speed and the fan for it. Read the
sliced file before you tune:

```bash
# Which features exist in the print.
grep -o "; FEATURE: .*" out.gcode | sort | uniq -c
```

Then read the `F` values and the `M106` values inside the feature you care about.

OrcaSlicer writes `; FEATURE: ` on Bambu Lab printers. It writes `;TYPE:` on
other printers.

A worked example is in [recipes.md](references/recipes.md) § 2.6. There, gap
infill on an overhang got no overhang slowdown, because gap infill is neither a
perimeter nor a bridge.

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
| `inherits` | Present, points further up the built-in chain | **Must name a SYSTEM preset.** A user preset cannot inherit from another user preset — see the warning below and gotcha 6. Point `inherits` at the nearest system ancestor. A preset with no `inherits` may be supported, but this tool does not test that form. Always set `inherits` to a system preset. |
| `from` | Absent | `"User"` |
| `version` | Sometimes absent | **Required.** A user preset with no `version` field is silently skipped by the loader even if everything else is correct (confirmed empirically). |
| `<domain>_settings_id` (`print_settings_id` / `filament_settings_id` / `printer_settings_id`) | — | Should be set, equal to `name`. |
| Body | Every setting — a complete, self-contained definition | Only the keys that differ from `inherits` (a *diff*), unless you deliberately flattened everything with `--de-link-inherits` |

> [!WARNING]
> **`inherits` in a user preset must name a SYSTEM preset.** A user preset that
> inherits from another user preset does not load. OrcaSlicer drops the child
> silently. It writes `can not find parent <parent> for config <child>!` to the
> debug log and shows nothing in the UI. Read gotcha 6 before you design a set of
> presets.

**About a preset with no `inherits`:** an earlier version of this document said
that OrcaSlicer rejects a user preset that has no `inherits` key. That claim came
from a bug report. This tool never tested it. Source inspection shows a
`preset->save(nullptr)` path, a `default_preset_for(config)` fallback for a preset
with no parent, and a code comment that reads "We support custom root preset now".
A root preset with no `inherits` is therefore possible, but it is untested here.
**Use a system parent.** That form is tested and recommended. Do not depend on the
no-`inherits` form.

**`validate_orca.py clone` produces the correct shape automatically** — do not
hand-write user preset JSON. If you ever do write one by hand (e.g. via
`write_to_file`), run `validate_orca.py auto <file> --json` afterward and check
the `warnings` array; it lints exactly these rules (tagged
`[user-preset format]`) even when the file is otherwise schema-valid, because
schema-valid does **not** mean OrcaSlicer's loader will show it.

---

## Before You Generate: Operator Interview

> [!IMPORTANT]
> Complete all seven steps below **before** you generate any profile. Each step
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

### 2. Does the request imply a shared base with variants?

Look at the whole set of presets that the operator asked for. Ask yourself if the
set implies one shared "base" preset and two or more "variant" presets that
inherit from it.

OrcaSlicer cannot express that structure. A user preset cannot inherit from
another user preset. See § "Undocumented Serialization Gotchas", item 6.

If the set implies a shared base, tell the operator these three facts **before you
generate anything**:

1. OrcaSlicer cannot express a shared base with variants.
2. Each variant will be an independent preset that inherits from a system preset.
3. Each variant will repeat the shared values, so a later change to a shared value
   must be applied to every variant file.

Get the operator's acknowledgement. Do not generate the presets before the
operator answers.

**Never name a preset `Base`.** The word is ambiguous. "Support base" means the
bulk support structure. "Base profile" reads as the plain single-material
profile. The word can no longer mean "parent preset" either, because every user
preset is flat. Use `Single-Material` for a plain solo-printing profile. Use
`Model` and `Support Interface` for the multi-material roles. See
[Recipes](references/recipes.md) § "Naming: do not use the word Base".

### 3. Printer-bound or universal?

See § 5 "Cloning Profiles: Inherited vs Independent" for the exact question and
the naming rule. Ask it now, not after you generate.

### 4. Which build plate?

The answer selects which of the twelve `*_plate_temp` keys to set. OrcaSlicer
reads only the pair for the plate that the operator selects, so a correct value
on the wrong plate does nothing. **Do not guess the plate.** See
[Recipes](references/recipes.md) § 2.4 for the full twelve-key table.

### 5. Single-material or multi-material?

A multi-material job needs AMS slot indices. `support_interface_filament` and
`support_filament` hold an extruder/filament index that depends on the operator's
own slot layout. Only the operator knows it. Ask for the slot numbers, or leave
the keys at `"0"` (auto) and tell the operator to set them in the UI.

> [!CAUTION]
> **After you generate a multi-material or support-interface preset set, report the
> required manual steps to the operator.** State plainly that **the presets alone are
> not a complete working setup**. Give this checklist:
>
> 1. **Flushing volumes (mandatory).** Set 600 to 800 mm³ for a PLA/PETG pair in the
>    **Flushing volumes** dialog. The keys are not preset options; they live in
>    `OrcaSlicer.conf` per printer preset. Too little purge gives
>    cross-contamination and a clogged nozzle.
> 2. **Support interface filament (mandatory).** Set **Support interface filament**
>    in the **Support** tab to the AMS slot that holds the interface material. Leave
>    **Support filament** at `0`.
> 3. **Verify the physical AMS slots** against the indices from step 2.
> 4. **Optional:** `flush_into_infill`, `flush_into_objects`, and
>    `flush_into_support` reuse the purged filament.
>
> Report the checklist even when the operator did not ask for it, and even when
> every preset validates clean. See [Recipes](references/recipes.md) § "Required
> manual steps after you create these presets" for the full text.
> `validate_orca.py clone` prints the same checklist at creation time.

### 6. Confirm that the parent profile exists and is a system preset

A hallucinated parent name is a real observed failure. `Generic PETG @BBL X1C`
does **not** exist. The real profiles are `Generic PETG HF @BBL X1C` and
`Generic PETG @base`.

Verify every name before you put it in `inherits`. Confirm also that the named
parent is a **system** preset. A user preset as parent breaks the child silently:
```bash
python validate_orca.py list-profiles --domain filament --query "PETG"
```

### 7. Confirm the parent's `compatible_printers`

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

Both options below always strip `type`/`setting_id`/`compatible_printers` — see
the format table above for why. They differ only in how much of the parent's
resolved values get redeclared in the child body:

> [!IMPORTANT]
> **Source is a system preset:** `clone` sets `"inherits"` to that exact system
> profile, never a grandparent.
> **Source is a user preset:** `clone` flattens automatically. It sets
> `"inherits"` to the nearest **system** ancestor and inlines the values that the
> user parent supplied. A user preset cannot be a parent — see § "Undocumented
> Serialization Gotchas", item 6.

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
`"inherits"` is still kept and still points at a system preset. A system parent is
the tested form, so this option locks the values but keeps the link. It does not
achieve full independence from the parent's existence and name.
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

Static validation cannot see a runtime rejection. After OrcaSlicer restarts, read
the log to find out what OrcaSlicer actually loaded:
```bash
python validate_orca.py doctor
```
`doctor` sees a dropped preset and a files-vs-loaded count mismatch. It does not
see a bad key in a user preset: OrcaSlicer 2.4.2 does not log key removal on that
load path, so `doctor` reports the key check as NOT CHECKED. Use the known-key
validation in `clone` and `auto` against a typo or a wrong-domain key.

See [Finding & Cloning Built-in Profiles](references/finding_and_cloning_builtin_profiles.md) § 5.

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
| `doctor` | Read the newest OrcaSlicer log; report dropped presets and file-count mismatches (removed keys only when the log names them; 2.4.2 does not log them for user presets) | `--json` |

---

## 8. Reference Manuals

- [Inspection & Diffing Guide](references/inspection_and_diffing_profiles.md)
- [Finding & Cloning Built-in Profiles](references/finding_and_cloning_builtin_profiles.md)
- [Generation Guide](references/generation_rules.md)
- [Editing & DAG Inheritance Guide](references/editing_rules.md)
- [Worked Profile Recipes](references/recipes.md)
