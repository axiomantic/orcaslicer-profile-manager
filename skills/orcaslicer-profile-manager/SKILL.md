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
| `setting_id` | 16-char base62 string | `"CUSTPLAPRINTER01"` | 17 characters |
| `filament_id` | Max 8-char string (AMS limit) | `"CPLA0001"` | `"LONG_FILAMENT_ID"` |
| `extruder_array` | String or array of strings | `"0.98"` or `["0.98"]` | `[0.98]` |

---

## 1. Locating Built-in & User Directories (`locate`)

Discover installed OrcaSlicer application and user profile directories on the host system:
```bash
python scripts/validate_orca.py locate
```

---

## 2. Listing Vendor Ecosystems & Profiles (`list-vendors`, `list-profiles`)

### List Installed Vendors (`list-vendors`)
Lists all installed 3D printer hardware vendor ecosystems (BBL, Creality, Voron, Prusa, RatRig, etc.) along with model, printer, filament, and process profile counts:
```bash
python scripts/validate_orca.py list-vendors
python scripts/validate_orca.py list-vendors --json
```

### Search & List Profiles (`list-profiles`)
Search installed built-in & user profiles by domain (`machine`, `filament`, `process`, `vendor`), vendor, or text query:
```bash
python scripts/validate_orca.py list-profiles --domain filament --query "PLA"
python scripts/validate_orca.py list-profiles --vendor Voron --detail
```

---

## 3. Deep Profile Inspection (`inspect`)

Examine a profile's identity, DAG inheritance chain (`Profile -> Parent -> Base`), key domain parameters (thermals, speeds, flow, AMS IDs), schema health, and child dependents:
```bash
python scripts/validate_orca.py inspect "Bambu PLA Basic @BBL X1C"
python scripts/validate_orca.py inspect ./custom_process.json --json
```

---

## 4. Comparing / Diffing Profiles (`diff`)

Compare two profiles to pinpoint parameter value deltas (temperature, speed, infill, flow rate differences):
```bash
python scripts/validate_orca.py diff "0.20mm Standard @Voron" "0.20mm HighSpeed Voron"
python scripts/validate_orca.py diff ./profileA.json ./profileB.json --json
```

---

## 5. Cloning Profiles: Inherited vs Independent (`clone`)

> [!IMPORTANT]
> **Output Location:** Omit the `--out` parameter entirely! The script will automatically resolve the exact cross-platform user profile path and save the file and `.info` cache there.
> **Interview the Operator:** Use the `ask_question` tool to ask the operator if the profile should be bound strictly to a specific printer/nozzle or if it should be universal.
> - **Printer-bound:** Add the exact printer suffix expected by OrcaSlicer (e.g., `@BBL X1C 0.8 nozzle`) to the `--name`.
> - **Universal:** Do NOT append a printer suffix to `--name`. Pass `--set compatible_printers='[]'` to clear the filter.

### Option A: Standard Inherited Clone (Child Profile)
Creates a profile inheriting from a parent preset (e.g. `"inherits": "fdm_process_common"`).
```bash
python scripts/validate_orca.py clone filament "Bambu PLA Basic @BBL X1C" \
  --name "Custom Bambu PLA Basic" \
  --set nozzle_temperature='["225"]'
```

### Option B: Standalone Independent Clone (`--de-link-inherits`) (Recommended)
Flattens parent inheritance settings and removes the `"inherits"` link to protect custom profiles from stock software update corruption:
```bash
python scripts/validate_orca.py clone process "0.20mm Standard @Voron" \
  --name "0.20mm Standalone Voron" \
  --de-link-inherits \
  --set outer_wall_speed='"180"'
```

---

## 6. Generating Templates & Validating Profiles

Generate starter skeleton JSON:
```bash
python scripts/validate_orca.py template <domain> --out <path/to/profile.json>
```

Validate files or directories against Draft 2020-12 schemas:
```bash
python scripts/validate_orca.py auto <path/to/profile.json> --json
```

---

## 7. CLI Tool Subcommand Quick Reference

`scripts/validate_orca.py` provides a complete programmatic interface:

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
