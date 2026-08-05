# OrcaSlicer Profile Manager & Validation Engine

A deterministic JSON Schema (Draft 2020-12) validation engine, profile generator, and cross-platform profile manager for OrcaSlicer configurations. Works as a CLI tool and as an **Agent Plugin & Skill** for AI coding assistants (Claude Code, Codex, Antigravity, Cursor, etc.).

---

## Features

- **Cross-Platform Directory Discovery (`locate`)**: Automatically finds built-in app profiles and user configuration directories across **macOS**, **Linux** (including Flatpak), and **Windows** (including Bambu account directories like `user/user<10digitnumber>`).
- **Vendor Ecosystem Listing (`list-vendors`)**: Summarizes installed manufacturer ecosystems (BBL, Creality, Voron, Prusa, RatRig, etc.) with profile counts.
- **Profile Search & Listing (`list-profiles`)**: Search installed profiles by domain, vendor, or text query with detailed metadata.
- **Deep Profile Inspection (`inspect`)**: Displays a profile's identity, full DAG inheritance chain (`Profile -> Parent -> Base`), key domain parameters (thermals, speeds, flow, AMS IDs), schema health, and child dependents.
- **Parameter Diffing (`diff`)**: Highlights exact parameter value deltas between two profiles.
- **Profile Cloning & De-linking (`clone`)**: Clones built-in profiles, auto-generates a unique 16-character base62 `setting_id`, applies custom `--set` property overrides, and offers `--de-link-inherits` to flatten parent settings into an independent profile immune to stock software update corruption.
- **Template Generation (`template`)**: Outputs valid starter skeleton JSON templates for any domain.
- **DAG Profile Inheritance Resolution**: Reconstructs instantiated profile state by flattening parent `inherits` chains (matching C++ `Preset.cpp` logic).
- **JSON Schema Validation (`auto`, `vendor`, `machine`, `filament`, `process`, `material-db`)**: Validates string-encoded OrcaSlicer profiles against Draft 2020-12 JSON Schemas (enforces 8-char AMS `filament_id` limits, 16-char `setting_id` bounds, string booleans `"0"`/`"1"`, string numbers, and percentages).

---

## Installation Instructions

### Prerequisites
- Python 3.9+ installed on your system.

### Option 1: Fast Setup with `uv` (Recommended)
`uv` is an extremely fast Rust-based Python package manager.

```bash
# 1. Clone or navigate to the repository directory
cd orcaslicer_validator

# 2. Create virtual environment
uv venv

# 3. Activate the virtual environment
# On macOS / Linux:
source .venv/bin/activate

# On Windows:
# .venv\Scripts\activate

# 4. Install dependencies
uv pip install -r requirements.txt
```

### Option 2: Standard Python Setup (`venv` + `pip`)

```bash
# 1. Navigate to the repository directory
cd orcaslicer_validator

# 2. Create virtual environment
python3 -m venv venv

# 3. Activate virtual environment
# On macOS / Linux:
source venv/bin/activate

# On Windows:
# venv\Scripts\activate

# 4. Install dependencies
pip install -r requirements.txt
```

---

## Installing as an Agent Plugin / Skill

This package is structured to work natively as a skill across major AI coding assistants:

### For Claude Code
Copy or symlink the skill directory to your Claude skills folder:
```bash
mkdir -p ~/.claude/skills
cp -r skills/orcaslicer-profile-manager ~/.claude/skills/
```

### For Antigravity (AGY)
Copy or symlink the skill directory to your Antigravity global configuration:
```bash
mkdir -p ~/.gemini/config/skills
cp -r skills/orcaslicer-profile-manager ~/.gemini/config/skills/
```

### For Workspace Agents (Codex, Cursor, etc.)
The repository includes `.agents/skills/orcaslicer-profile-manager` and `plugin.json` for automatic workspace detection.

---

## Quick Start CLI Examples

All commands can be run via `uv run python validate_orca.py <subcommand>` or `python validate_orca.py <subcommand>`:

### 1. Discover OrcaSlicer Directories on Host OS
```bash
uv run python validate_orca.py locate
```

### 2. List Installed Vendor Ecosystems
```bash
uv run python validate_orca.py list-vendors
uv run python validate_orca.py list-vendors --json
```

### 3. Search and List Profiles
```bash
uv run python validate_orca.py list-profiles --domain filament --query "PLA"
uv run python validate_orca.py list-profiles --vendor Voron --detail
```

### 4. Deep Profile Inspection
```bash
uv run python validate_orca.py inspect "Bambu PLA Basic @BBL X1C"
uv run python validate_orca.py inspect ./custom_process.json --json
```

### 5. Compare / Diff Two Profiles
```bash
uv run python validate_orca.py diff "0.20mm Standard @Voron" "0.20mm HighSpeed Voron"
uv run python validate_orca.py diff ./profileA.json ./profileB.json --json
```

### 6. Clone & Customize a Built-in Profile
```bash
# Standard inherited clone with overrides:
uv run python validate_orca.py clone filament "Bambu PLA Basic @BBL X1C" \
  --name "My Custom PLA" \
  --out custom_pla.json \
  --set nozzle_temperature='["225"]'

# Standalone clone (de-linked inheritance to prevent stock update corruption):
uv run python validate_orca.py clone process "0.20mm Standard @Voron" \
  --name "0.20mm Standalone Speed" \
  --out custom_process.json \
  --de-link-inherits \
  --set outer_wall_speed='"180"'
```

### 7. Generate Starter Skeleton JSON Template
```bash
uv run python validate_orca.py template filament --out filament_custom.json
uv run python validate_orca.py template machine --out machine_custom.json
```

### 8. Auto-Detect and Validate Profiles against JSON Schemas
```bash
uv run python validate_orca.py auto ./my_profiles/
uv run python validate_orca.py auto ./my_profiles/ --json
```

---

## Running Unit Tests

Run the included test suite to verify schema resolution and CLI subcommands:

```bash
uv run python -m unittest discover -s tests
```

---

## License & Credits

Built for the OrcaSlicer community and compatible with Draft 2020-12 JSON Schema specifications.
