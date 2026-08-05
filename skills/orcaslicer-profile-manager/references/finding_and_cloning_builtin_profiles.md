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
> **User-preset format:** `clone` always produces OrcaSlicer's stricter user-preset shape automatically (no `type`/`setting_id`/`compatible_printers`, `inherits` always set to a system preset, `from: "User"`). See SKILL.md § "User Presets vs System Presets" for why this matters — getting it wrong is the #1 cause of a cloned profile silently not appearing in the UI.
> **Cloning a user preset:** a user preset cannot be a parent. When the source is a user preset, `clone` flattens automatically: it points `inherits` at the nearest **system** ancestor and inlines the values that the user parent supplied.

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
Flattens every resolved parent value into the child body so its values can't drift on a stock update. `"inherits"` is kept and still points at a system preset — a system parent is the tested form. This option locks the values, not the link itself:
```bash
python validate_orca.py clone process "0.20mm Standard @Voron" \
  --name "0.20mm HighSpeed Value-Locked" \
  --de-link-inherits \
  --set outer_wall_speed='"180"' \
  --set inner_wall_speed='"220"'
```

---

## 5. Troubleshooting Disappearing Profiles

> [!WARNING]
> **Read the OrcaSlicer log first. The log is the only authority.**
> Static validation cannot detect a runtime rejection. A preset can pass every
> check in this tool, and OrcaSlicer can still discard it at load time. The UI
> shows no error. The only record of the rejection is the log file.

### 5.1 Step 1: run `doctor`

The tool automates every manual step in section 5.2. It reads the newest log
file, reports the presets that OrcaSlicer dropped, cross-checks the number of
JSON files against the number of loaded presets, and exits non-zero when anything
is wrong. It also reports the keys that OrcaSlicer removed, but only when the log
names them; OrcaSlicer 2.4.2 does not log a key removal for a user preset
directory load, so `doctor` reports that check as `NOT CHECKED`. See the warning
in section 5.2.

```bash
python validate_orca.py doctor
python validate_orca.py doctor --json

# Read a specific log file, or point at a different user preset directory.
python validate_orca.py doctor --log <path/to/debug_....log.0>
python validate_orca.py doctor --user-dir <path/to/user/default>
```

**Run `doctor` before any other check.** Use the manual steps in section 5.2 only
when the subcommand is not available.

> [!IMPORTANT]
> **Restart OrcaSlicer before you read the log.** OrcaSlicer writes the load
> result only at startup. A log that was written before you saved the presets
> tells you nothing about them.
>
> 1. Write the preset files.
> 2. Quit OrcaSlicer.
> 3. Start OrcaSlicer.
> 4. Read the log.
>
> **A stale log is a real trap.** It shows a clean load of the previous session
> and looks like a pass. Compare the timestamp of the log file against the
> timestamps of the preset files. The log must be newer than every preset file.

### 5.2 Manual log inspection

#### Log directory

| OS | Log Directory |
|---|---|
| **macOS** | `~/Library/Application Support/OrcaSlicer/log/` |
| **Linux** | `~/.config/OrcaSlicer/log/`<br>`~/.var/app/io.github.softfever.OrcaSlicer/config/OrcaSlicer/log/` |
| **Windows** | `%APPDATA%\OrcaSlicer\log\` |

Log files are named like `debug_Wed_Aug_05_03_21_41_14247.log.0`. **Use the newest
file.** OrcaSlicer keeps the older files, and they describe earlier sessions.

```bash
# macOS: find the newest log file.
ls -t ~/Library/Application\ Support/OrcaSlicer/log/ | head -1
```

#### Diagnostic strings

Search the newest log file for these strings:

| String | Meaning |
|---|---|
| `can not find parent` | OrcaSlicer could not resolve `inherits`. It dropped the preset. |
| `can not find inherit preset for user preset` | Same failure, reported for a user preset. |
| `incorrect keys` | OrcaSlicer removed one or more keys because it does not know them. The preset loaded, but those settings do nothing. **Read the warning below: this line does not occur for a user preset directory load.** |

```bash
LOG_DIR=~/Library/Application\ Support/OrcaSlicer/log
NEWEST="$LOG_DIR/$(ls -t "$LOG_DIR" | head -1)"
grep -E "can not find parent|can not find inherit preset|incorrect keys" "$NEWEST"
```

> [!WARNING]
> **The absence of an `incorrect keys` line proves nothing about a user preset.**
> A controlled test on OrcaSlicer 2.4.2 wrote a user filament preset with a
> pure-junk key (`zz_not_a_real_setting`) and a wrong-domain key (`layer_height`,
> a process key). After a restart, OrcaSlicer loaded the preset, the loaded count
> matched the file count, and the log held no `incorrect keys` line. The format
> string is in the binary, and the same log file holds `[warning]` lines, so this
> is not a log-verbosity effect: `Preset::remove_invalid_keys` does not run, or
> does not log, on the user preset directory load path. It applies on other paths,
> such as a project (3mf) load or `import_json_presets`.
>
> Use static known-key validation against a typo or a wrong-domain key: `clone`
> refuses an unknown or wrong-domain `--set` key, and `auto` reports an
> `[unknown key]` warning. `doctor` reports this check as `NOT CHECKED`.

A `can not find parent` line on a user preset whose parent is another **user**
preset is expected behaviour, not a typo. A user preset cannot inherit from
another user preset. See SKILL.md § "Undocumented Serialization Gotchas", item 6.

#### The `loaded N presets` checksum

The log holds a summary line in this form:

```
loaded 1 presets
```

Compare `N` against the number of JSON files in the user preset directory. A
lower `N` means OrcaSlicer dropped presets.

```bash
grep "loaded .* presets" "$NEWEST"
ls ~/Library/Application\ Support/OrcaSlicer/user/default/filament/*.json | wc -l
```

### 5.3 Remaining checks

Run these checks after the log tells you what OrcaSlicer rejected:

1. **Check the user-preset format**: run `python validate_orca.py auto "<path-to-file>" --json` and read the `warnings` array — it flags `type`/`setting_id`/`compatible_printers` present, or `inherits` missing, tagged `[user-preset format]`. A file can pass schema validation and still be invisible to OrcaSlicer.
2. **Check Disk Storage**: Run `locate` and inspect `user/default/` or `user/user<10digitnumber>/`. JSON files often still exist.
3. **Verify Parent Profiles**: If the profile uses `"inherits"`, ensure the parent is a **system** profile and is present in the search path (`inspect <file> --json` shows the resolved DAG chain and flags a missing parent). Repair a user-preset parent with `tools/flatten_user_inherits.py`.
4. **Cloud Sync**: Disable "Auto sync user presets" if logging into a Bambu account overwrote local presets.
