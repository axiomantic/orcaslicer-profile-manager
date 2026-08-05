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
python validate_orca.py locate
```

---

## 3. Searching Built-in Profiles

Search available built-in and user profiles by domain (`vendor`, `machine`, `filament`, `process`) and text query:
```bash
# Search for PLA filament profiles
python validate_orca.py list-profiles --domain filament --query "PLA"

# Search for Voron process profiles
python validate_orca.py list-profiles --domain process --query "Voron"

# Search in JSON format
python validate_orca.py list-profiles --query "Bambu" --json
```

---

## 4. Cloning and Customizing Profiles

> [!IMPORTANT]
> **Output Location:** Omit the `--out` parameter entirely! The script will automatically resolve the exact cross-platform user profile path and save the file and `.info` cache there.
> **Interview the Operator:** Use the `ask_question` tool to ask the operator if the profile should be bound strictly to a specific printer/nozzle or if it should be universal.
> - **Printer-bound:** Add the exact printer suffix expected by OrcaSlicer (e.g., `@BBL X1C 0.8 nozzle`) to the `--name`.
> - **Universal:** Do NOT append a printer suffix to `--name`. `clone` never carries `compatible_printers` over from the source by default — only set it if you deliberately restrict compatibility with `--compatible-printers`.
> **User-preset format:** `clone` always produces OrcaSlicer's stricter user-preset shape automatically (no `type`/`setting_id`/`compatible_printers`, `inherits` always set, `from: "User"`). See SKILL.md § "User Presets vs System Presets" for why this matters — getting it wrong is the #1 cause of a cloned profile silently not appearing in the UI.

Use `clone` to find a built-in profile, copy it to a new output path, generate a new unique 16-character `setting_id` (stored in the paired `.info` file, not the JSON body), set a new profile `name`, apply parameter overrides, and validate:

### Standard Inherited Clone (Child Profile) (Recommended default)
Body is only the `--set` overrides; the rest resolves live from the parent, so future stock-profile updates keep flowing through:
```bash
python validate_orca.py clone filament "Bambu PLA Basic @BBL X1C" \
  --name "Custom PLA HighTemp" \
  --set nozzle_temperature='["230"]' \
  --set filament_flow_ratio='["0.96"]'
```

### Value-Locked Clone (`--de-link-inherits`)
Flattens every resolved parent value into the child body so its values can't drift on a stock update. `"inherits"` is kept (pointing at the profile just cloned) — OrcaSlicer requires every user preset to declare a valid, existing parent, so full independence isn't achievable; this only locks values, not the link itself:
```bash
python validate_orca.py clone process "0.20mm Standard @Voron" \
  --name "0.20mm HighSpeed Value-Locked" \
  --de-link-inherits \
  --set outer_wall_speed='"180"' \
  --set inner_wall_speed='"220"'
```

---

## 5. Troubleshooting Disappearing Profiles

If profiles vanish from the OrcaSlicer UI (or a freshly cloned profile never shows up in the first place):
1. **Check the user-preset format first**: run `python validate_orca.py auto "<path-to-file>" --json` and read the `warnings` array — it flags `type`/`setting_id`/`compatible_printers` present, or `inherits` missing, tagged `[user-preset format]`. A file can pass schema validation and still be invisible to OrcaSlicer; this check catches what schema validation can't. This is by far the most common cause and produces no error from OrcaSlicer itself.
2. **Check Disk Storage**: Run `locate` and inspect `user/default/` or `user/user<10digitnumber>/`. JSON files often still exist.
3. **Verify Parent Profiles**: If the profile uses `"inherits"`, ensure the parent profile is present in the search path (`inspect <file> --json` shows the resolved DAG chain and flags a missing parent).
4. **Cloud Sync**: Disable "Auto sync user presets" if logging into a Bambu account overwrote local presets.
