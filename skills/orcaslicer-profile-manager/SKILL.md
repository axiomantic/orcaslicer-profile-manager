---
name: orcaslicer-profile-manager
description: "Locate, search/list vendors & profiles, inspect DAG chains, diff profile deltas, clone, de-link inheritance, backup/migrate, and validate OrcaSlicer 3D printing config profiles (vendor, machine, filament, process, material-db) across macOS, Linux, and Windows."
---

# OrcaSlicer Profile Manager Skill

This skill equips AI coding assistants (Claude Code, Codex, Antigravity, Cursor, etc.) to **locate**, **list vendors/profiles**, **inspect profile DAG chains**, **diff profile deltas**, **clone/copy**, **de-link inheritance**, **edit**, **resolve inheritance for**, **backup/migrate**, and **validate** OrcaSlicer 3D printer configuration JSON files across operating systems (macOS, Linux, Windows).

---

## Core OrcaSlicer Architecture & Serialization Rules

OrcaSlicer configuration properties (originating from C++ `ConfigOption` in `libslic3r`) strictly enforce a **string-encoded** serialization format for scalar values:

| C++ Type | Expected JSON Format | Example Valid Value | Invalid Format |
|---|---|---|---|
| `bool` | Binary or boolean string | `"1"`, `"0"`, `"true"`, `"false"` | `true`, `1` |
| `int` | String integer | `"7500"` | `7500` |
| `float`/`double` | String double | `"0.4"`, `"215.0"` | `0.4` |
| `percent` | String appended with `%` | `"15%"` | `0.15`, `15` |
| `setting_id` | 16-char base62 string (system profiles only — see §5) | `"CUSTPLAPRINTER01"` | 17 characters |
| `filament_id` | Max 8-char string (AMS limit) | `"CPLA0001"` | `"LONG_FILAMENT_ID"` |
| `extruder_array` | Any scalar field may be a single value or an array of one value per extruder | `"0.98"` or `["0.98", "0.98"]` | mixing types within the array |

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
