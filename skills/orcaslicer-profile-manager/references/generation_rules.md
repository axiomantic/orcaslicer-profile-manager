# Profile Generation Rules & Field Requirements

When generating new OrcaSlicer profiles, adhere strictly to domain-specific required properties and value types.

> [!NOTE]
> The rules below (including `type` as required) describe **system-preset** shape —
> use `template` for building a vendor bundle or a from-scratch reference file. If
> the output is meant to be dropped directly into a `user/default/<domain>/`
> folder as a *user* preset, it must NOT carry `type`/`setting_id`/
> `compatible_printers` and MUST carry a non-empty `inherits`. Prefer `clone`
> (which does this automatically) over hand-writing a user preset from a
> template. See SKILL.md § "User Presets vs System Presets".

## 1. Vendor Manifest Profiles (`vendor.json`)
Located in `resources/profiles/<VendorName>.json`.
- **Required Fields**: `name`, `version` (4-part format `XX.XX.XX.XX`), `machine_model_list`, `machine_list`, `process_list`.
- **Sub-paths**: Must be relative paths to downstream machine, process, and filament JSON files.
- **Example**:
```json
{
  "name": "BBL",
  "version": "01.09.00.00",
  "force_update": "0",
  "description": "Bambu Lab official vendor ecosystem profile",
  "machine_model_list": [{"name": "Bambu Lab X1 Carbon", "sub_path": "machine/Bambu Lab X1 Carbon.json"}],
  "machine_list": [{"name": "Bambu Lab X1 Carbon 0.4 nozzle", "sub_path": "machine/Bambu Lab X1 Carbon 0.4 nozzle.json"}],
  "process_list": [{"name": "0.20mm Standard @BBL X1C", "sub_path": "process/0.20mm Standard @BBL X1C.json"}],
  "filament_list": [{"name": "Bambu PLA Basic", "sub_path": "filament/Bambu PLA Basic.json"}]
}
```

## 2. Machine Profiles (`machine.json`)
- **Required Fields**: `type` (`"machine"` or `"machine_model"`), `name`, `printer_model`, `printable_area`.
- **Kinematics**: `printable_height`, `machine_max_speed_x/y/z`, `machine_max_acceleration_x/y/z`.
- **Cost Modeling**: `printer_power_consumption`, `electricity_rate`, `fixed_cost_per_print`, `estimated_failure_rate`.
- **IMEX**: `is_imex`, `imex_gantry_count`, `imex_firmware_managed_zones`.

## 3. Filament Profiles (`filament.json`)
- **Required Fields**: `type` (`"filament"`), `name`, `filament_type`, `filament_flow_ratio`.
- **AMS Limit**: `filament_id` MUST NOT exceed **8 characters** (hardware micro-controller RAM limit).
- **Thermals & Flow**: `nozzle_temperature`, `hot_plate_temp`, `filament_density`, `filament_cost`.

## 4. Process Execution Profiles (`process.json`)
- **Required Fields**: `type` (`"process"`), `name`, `layer_height`, `wall_generator`, `sparse_infill_density`.
- **Wall Generators**: `"arachne"` or `"classic"`. Arachne parameters (`wall_transition_angle`, `min_bead_width`) only execute when `wall_generator` is `"arachne"`.
- **Speeds & Accelerations**: `initial_layer_speed`, `outer_wall_speed`, `inner_wall_speed`, `default_acceleration`.

## 5. Material Database Mappings (`material_database.json`)
- **Format**: Dictionary keyed by material key (e.g. `"06002"`), containing `"base"` object with `id` and `meterialType`.
