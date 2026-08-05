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

> [!WARNING]
> **A user preset can only inherit from a SYSTEM preset.** The tree above applies
> to system profiles. A user preset that names another **user** preset in
> `inherits` does not load. OrcaSlicer drops the child silently and writes
> `can not find parent <parent> for config <child>!` to the debug log. The UI
> shows no error.
>
> A shared user "base" preset with several user "variant" presets is therefore not
> possible. Keep every user preset flat:
>
> ```
> [Generic PLA @BBL X1C]   (SYSTEM preset — the only valid parent)
>         │
>         ├───> [My PLA Draft]   (user preset — inherits from the system preset,
>         │                       repeats every shared value in its own body)
>         └───> [My PLA Fine]    (user preset — same rule, same repeated values)
> ```
>
> The shared values must be repeated in each variant. You cannot edit them in one
> place. Point `inherits` at the nearest system ancestor and inline the values you
> would have taken from an intermediate user preset. A system parent that is not
> the direct value source is correct: OrcaSlicer uses the parent config only as a
> starting point and then applies the child's own keys on top.
>
> `tools/flatten_user_inherits.py` repairs presets that already carry a
> user-preset parent. `clone` flattens automatically when the source is a user
> preset. See SKILL.md § "Undocumented Serialization Gotchas", item 6.

## 2. Inherited Profiles vs Independent (De-linked) Profiles

Relying on direct inheritance from stock vendor profiles carries a risk:
- When OrcaSlicer updates, updated stock parent profiles can overwrite or alter child behaviors.
- Deleting a parent profile breaks the inheritance chain, causing child profiles to "disappear" from the OrcaSlicer UI even though their `.json` files still exist.

**Mitigation: Value-Locking Inheritance (`--de-link-inherits`)**:

> [!WARNING]
> `--de-link-inherits` does **not** remove `"inherits"`. It flattens every
> resolved parent value explicitly into the child body and keeps `"inherits"`
> pointed at a **system** preset. A system parent is the tested and recommended
> form. Because every value is now redeclared in the child, later edits to the
> parent chain cannot change this profile's *behavior* — but the profile still
> depends on that parent *existing* (deleting the named parent still breaks the
> chain).
>
> A preset with no `inherits` key may be supported by OrcaSlicer, but this tool
> does not test that form. Do not depend on it. Always name a system parent.

```bash
python validate_orca.py clone process "0.20mm Standard @Voron" \
  --name "0.20mm Value-Locked Profile" \
  --out ./value_locked_process.json \
  --de-link-inherits
```
This flattens all parent settings into `value_locked_process.json` while keeping `"inherits": "0.20mm Standard @Voron"`.

## 3. Account-Specific User Directories & Cloud Sync

OrcaSlicer stores custom profiles in user directories:
- Default directory: `user/default/`
- Logged-in Bambu Lab account directory: `user/user<10digitnumber>/` (e.g. `user/user1234567890/`)

*Cloud Sync Overwrites*:
If "Auto sync user presets" is enabled in OrcaSlicer preferences, logging into a Bambu account may cause cloud presets to overwrite local custom settings.
- **Preventative Recommendation**: Disable "Auto sync user presets" when creating critical local custom profiles.
- **Profile Recovery**: If custom profiles vanish from the UI, run `python validate_orca.py doctor` first. It reads the newest OrcaSlicer log and reports which presets the loader dropped and why. Then inspect `user/default/` or `user/user<10digitnumber>/` for the `.json` files and verify that every `"inherits"` names a system profile.

## 4. Safe Property Modifications

- **Speeds & Accelerations**: Speed values must be string-encoded integers (`"180"`), not raw numeric types.
- **Extruder Arrays**: Multi-extruder properties like `nozzle_temperature` or `filament_flow_ratio` require arrays of strings (e.g. `["225"]` or `["225", "225"]`).
- **Compatible Printers**: Only set `compatible_printers` (array, e.g. `["Voron 2.4 350 0.4 nozzle"]`) on a user preset when deliberately restricting compatibility — otherwise leave it unset. `clone` already does this by default.
- **Setting ID does NOT belong in a user preset's JSON body.** It lives only in the paired `.info` file (`base_id = <16-char id>`), auto-generated by `clone`. A `"setting_id"` key present in the `.json` body is a system-preset-only field — OrcaSlicer's loader silently ignores user presets that carry it. See SKILL.md § "User Presets vs System Presets" for the full list of system-only fields (`type`, `setting_id`, `compatible_printers`, `instantiation`) that must be absent from a hand-edited user preset.
- **Inherits**: `"inherits"` on a user preset must name a **system** preset. Never point it at another user preset.
- **After any hand-edit**, run `python validate_orca.py auto "<file>" --json` and check the `warnings` array for `[user-preset format]` entries — schema validity alone does not guarantee OrcaSlicer will show the profile. Then restart OrcaSlicer and run `python validate_orca.py doctor` to confirm the loader accepted the preset.
