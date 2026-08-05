# Editing & DAG Inheritance Guide

When editing existing OrcaSlicer configuration profiles, understand how DAG profile inheritance is constructed and managed.

## 1. Inheritance Hierarchy (`inherits` property)

Profiles in OrcaSlicer often form an inheritance tree:

```
[fdm_process_common]  (Abstract Base Profile - defines defaults)
        │
        └───> [0.20mm Standard @Voron]  (Child Profile - defines deltas)
```

- **Child Profiles**: Do not duplicate all base settings. They specify `"inherits": "<parent_name>"` and provide delta property overrides.
- **Flattened Resolution**: The validator script builds a Directed Acyclic Graph (DAG) of profiles, recursively resolves the parent chain, and overlays child properties onto parent properties before evaluating JSON schemas.

## 2. Inherited Profiles vs Independent (De-linked) Profiles

Relying on direct inheritance from stock vendor profiles carries a risk:
- When OrcaSlicer updates, updated stock parent profiles can overwrite or alter child behaviors.
- Deleting a parent profile breaks the inheritance chain, causing child profiles to "disappear" from the OrcaSlicer UI even though their `.json` files still exist.

**Solution: De-linking Inheritance (`--de-link-inherits`)**:
To create a truly independent custom profile that will never break during software updates or stock profile edits, use `--de-link-inherits` when cloning:
```bash
python scripts/validate_orca.py clone process "0.20mm Standard @Voron" \
  --name "0.20mm Independent Profile" \
  --out ./independent_process.json \
  --de-link-inherits
```
This flattens all parent settings into `independent_process.json` and removes `"inherits"`, ensuring complete independence.

## 3. Account-Specific User Directories & Cloud Sync

OrcaSlicer stores custom profiles in user directories:
- Default directory: `user/default/`
- Logged-in Bambu Lab account directory: `user/user<10digitnumber>/` (e.g. `user/user1234567890/`)

*Cloud Sync Overwrites*:
If "Auto sync user presets" is enabled in OrcaSlicer preferences, logging into a Bambu account may cause cloud presets to overwrite local custom settings.
- **Preventative Recommendation**: Disable "Auto sync user presets" when creating critical local custom profiles.
- **Profile Recovery**: If custom profiles vanish from the UI, inspect `user/default/` or `user/user<10digitnumber>/` for the `.json` files. Verify `"inherits"` links or convert them to standalone profiles with `validate_orca.py`.

## 4. Safe Property Modifications

- **Speeds & Accelerations**: Speed values must be string-encoded integers (`"180"`), not raw numeric types.
- **Extruder Arrays**: Multi-extruder properties like `nozzle_temperature` or `filament_flow_ratio` require arrays of strings (e.g. `["225"]` or `["225", "225"]`).
- **Compatible Printers**: Use `compatible_printers` array (e.g. `["Voron 2.4 350 0.4 nozzle"]`) or `compatible_printers_condition` string to restrict profile availability to specific machines.
- **Setting ID**: Ensure `setting_id` remains a unique 16-character alphanumeric base62 string (`^[a-zA-Z0-9]{16}$`).
