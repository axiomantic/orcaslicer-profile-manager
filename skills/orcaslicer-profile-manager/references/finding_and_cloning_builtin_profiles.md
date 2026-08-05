# Finding & Cloning Built-in OrcaSlicer Profiles

This guide describes how to locate, search, clone, de-link, and edit built-in OrcaSlicer configuration profiles across macOS, Linux, and Windows.

## 1. Cross-Platform Built-in & User Profile Locations

OrcaSlicer stores system built-in profiles and user profiles in platform-specific directories:

| OS | Built-in App Profiles Directory | User Profiles Directory |
|---|---|---|
| **macOS** | `/Applications/OrcaSlicer.app/Contents/Resources/profiles` | `~/Library/Application Support/OrcaSlicer/user/default`<br>`~/Library/Application Support/OrcaSlicer/user/user<10digitnumber>` |
| **Linux** | `/usr/share/OrcaSlicer/resources/profiles`<br>`~/.local/share/OrcaSlicer/profiles` | `~/.config/OrcaSlicer/user/default`<br>`~/.var/app/io.github.softfever.OrcaSlicer/config/OrcaSlicer/user` |
| **Windows** | `C:\Program Files\OrcaSlicer\resources\profiles`<br>`%LOCALAPPDATA%\Programs\OrcaSlicer\resources\profiles` | `%APPDATA%\OrcaSlicer\user\default`<br>`%APPDATA%\OrcaSlicer\user\user<10digitnumber>` |

*Environment Variable Overrides*:
- `ORCASLICER_PROFILES_DIR`: Override path to built-in system profiles directory.
- `ORCASLICER_USER_DIR`: Override path to user profile directory.

---

## 2. Locating Installed Directories

Run `locate` to automatically discover existing OrcaSlicer installation paths on the current operating system:
```bash
python scripts/validate_orca.py locate
```

---

## 3. Searching Built-in Profiles

Search available built-in and user profiles by domain (`vendor`, `machine`, `filament`, `process`) and text query:
```bash
# Search for PLA filament profiles
python scripts/validate_orca.py list-profiles --domain filament --query "PLA"

# Search for Voron process profiles
python scripts/validate_orca.py list-profiles --domain process --query "Voron"

# Search in JSON format
python scripts/validate_orca.py list-profiles --query "Bambu" --json
```

---

## 4. Cloning and Customizing Profiles

> [!IMPORTANT]
> **Output Location:** Always output cloned user profiles directly to the cross-platform user default preset location (e.g., `~/Library/Application Support/OrcaSlicer/user/default/<domain>/` on macOS). Use `locate` to find the exact path.
> **Naming Conventions:** If the profile is specific to a nozzle size, the `--name` parameter must include the exact nozzle suffix expected by OrcaSlicer (e.g., `@BBL X1C 0.8 nozzle`) so that it populates correctly in the UI dropdowns.

Use `clone` to find a built-in profile, copy it to a new output path, generate a new unique 16-character `setting_id`, set a new profile `name`, apply parameter overrides, and validate:

### Standard Inherited Clone (Child Profile)
```bash
python scripts/validate_orca.py clone filament "Bambu PLA Basic @BBL X1C" \
  --name "Custom PLA HighTemp" \
  --out ./custom_pla_hightemp.json \
  --set nozzle_temperature='["230"]' \
  --set filament_flow_ratio='["0.96"]'
```

### Standalone Independent Clone (`--de-link-inherits`) (Recommended)
Flattens parent settings and removes `"inherits"` link to protect custom profiles from upstream stock profile updates:
```bash
python scripts/validate_orca.py clone process "0.20mm Standard @Voron" \
  --name "0.20mm HighSpeed Independent" \
  --out ./custom_process_speed.json \
  --de-link-inherits \
  --set outer_wall_speed='"180"' \
  --set inner_wall_speed='"220"'
```

---

## 5. Troubleshooting Disappearing Profiles

If profiles vanish from the OrcaSlicer UI:
1. **Check Disk Storage**: Run `locate` and inspect `user/default/` or `user/user<10digitnumber>/`. JSON files often still exist.
2. **Verify Parent Profiles**: If the profile uses `"inherits"`, ensure the parent profile is present in the search path.
3. **De-link**: Use `validate_orca.py clone <domain> <file> --de-link-inherits --name "<NewName>" -o <fixed_file.json>` to turn a broken child profile into a standalone profile.
4. **Cloud Sync**: Disable "Auto sync user presets" if logging into a Bambu account overwrote local presets.
