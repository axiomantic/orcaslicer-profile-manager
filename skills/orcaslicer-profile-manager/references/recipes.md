# Worked Profile Recipes

Copy-pasteable recipes for common OrcaSlicer tuning tasks. Each recipe gives the
exact key names, the value, and the reason for the value. Each recipe also lists
what a preset cannot do.

> [!IMPORTANT]
> Classify every key by domain before you write it. A process key in a filament
> preset is silently ignored. A filament key in a process preset is silently
> ignored. Each recipe below marks the domain of every key group.

> [!NOTE]
> All values below are verified against the OrcaSlicer 2.4.2 option tables and the
> bundled vendor profiles. Values are starting points, not calibrated results.
> Print a test part after you apply a recipe.

---

## Recipe 1: Large Nozzle Correction (0.6 / 0.8 / 1.0 mm)

A stock profile is tuned for a 0.4 mm nozzle. A larger nozzle needs a taller
layer, a wider line, and a slower wall.

### 1.1 Process domain: layer height

| Key | Type | Value | Reason |
|---|---|---|---|
| `layer_height` | scalar float string | max 60% of nozzle diameter (recommended), max 80% (absolute) | A taller layer does not bond to the layer below. |
| `initial_layer_print_height` | scalar float string | same rule as `layer_height` | The first layer must bond to the plate. |

Example for a 0.8 mm nozzle: `"0.48"` at 60%, `"0.64"` at the absolute maximum.

### 1.2 Process domain: line width

Set every line width to at least 105% of the nozzle diameter. A line narrower
than the nozzle causes under-extrusion and gaps. All of these keys are scalar
float strings:

- `line_width`
- `initial_layer_line_width`
- `outer_wall_line_width`
- `inner_wall_line_width`
- `top_surface_line_width`
- `internal_solid_infill_line_width`
- `sparse_infill_line_width`
- `support_line_width`

> [!WARNING]
> `bottom_surface_line_width` **does not exist**. Bottom solid surfaces use
> `internal_solid_infill_line_width`. Some vendor bundles ship the dead key. Do
> not copy it. See SKILL.md § "Undocumented Serialization Gotchas".

Example for a 0.8 mm nozzle at 105%: `"0.84"`.

### 1.3 Process domain: walls and speed

| Key | Type | Value | Reason |
|---|---|---|---|
| `wall_loops` | int string | `"2"` | A wide line already gives a thick wall. Fewer loops lower the shrinkage tension that causes warping. |
| `wall_generator` | enum string | `"classic"` | Arachne varies the line width to fill thin walls. A large nozzle has a narrow usable width band, so a fixed width is more predictable. The only valid values are `"classic"` and `"arachne"`. |
| `outer_wall_speed` | array of float strings | `["50"]` to `["60"]` | A wide line moves more material. A slow outer wall keeps the surface clean. |
| `inner_wall_speed` | array of float strings | about `["100"]` | The inner wall is hidden, so it can run faster than the outer wall. |

> [!NOTE]
> Speed keys are arrays with one element per extruder variant. Match the arity
> that the parent profile uses for that same key. See SKILL.md § "Undocumented
> Serialization Gotchas", item 2.

### 1.4 Process domain: small features

| Key | Type | Value | Reason |
|---|---|---|---|
| `small_perimeter_speed` | percent or float string | lower than `outer_wall_speed` | A small loop does not let the material cool before the nozzle returns. |
| `small_perimeter_threshold` | float string | `"0"` for auto | Sets the perimeter length below which the small-perimeter speed applies. |

There is no boolean key to enable small-perimeter slowdown. The two keys above
are the whole feature.

### 1.5 Process domain: tree supports

| Key | Type | Value | Reason |
|---|---|---|---|
| `tree_support_tip_diameter` | float string | at least the line width; `"0.9"` for a 0.8 mm nozzle | The slicer rejects an extrusion narrower than the line width. A tip smaller than the line width fails to slice. |

### 1.6 Process domain: warping

| Key | Type | Value | Reason |
|---|---|---|---|
| `brim_type` | enum string | `"outer_only"` | A brim holds the part edges to the plate. The outer brim is enough for most parts and is easy to remove. |
| `brim_width` | float string | wider for a larger part | More brim area gives more adhesion. |

See Recipe 3 for full warping mitigation.

### 1.7 Filament domain: temperature

| Key | Type | Value | Reason |
|---|---|---|---|
| `nozzle_temperature` | array of int strings | stock value plus 10 to 20 C | A large nozzle pushes more material per second. The melt zone needs more heat. |
| `nozzle_temperature_initial_layer` | array of int strings | stock value plus 10 to 20 C | The first layer needs the same correction. |

Do not exceed `nozzle_temperature_range_high`. Read that key from the parent
profile before you set a temperature.

### 1.8 Filament domain: pressure advance

| Key | Type | Value | Reason |
|---|---|---|---|
| `enable_pressure_advance` | array of bool strings | `["1"]` on Klipper or Marlin | Pressure advance corrects the extra material at corners. |
| `pressure_advance` | array of float strings | machine-specific, calibrate it | The value depends on the extruder and the filament. |

> [!WARNING]
> On Bambu Lab printers these two keys are largely inert. The printer runs its own
> Flow Dynamics Calibration in firmware. Every stock Bambu Lab profile leaves both
> keys unset. The real control is the Flow Dynamics option in the print dialog,
> not the slicer preset. Set these keys only for Klipper or Marlin machines.

### 1.9 Filament domain: wipe and cooling

| Key | Type | Value | Reason |
|---|---|---|---|
| `filament_wipe_distance` | array of float strings | lower than the printer default (2 mm on the X1C) | A large nozzle holds more material. A long wipe pulls too much material out and leaves a gap at the start of the next extrusion. |
| `additional_cooling_fan_speed` | array of int strings | raise it for PLA | This key **is** the aux/side fan. A wide, hot line needs more air to solidify. |

The machine-domain key `auxiliary_fan` declares whether the printer has an aux
fan. It is a machine preset key. Do not put it in a filament preset.

### 1.10 Filament domain: the throughput ceiling

| Key | Type | Value | Reason |
|---|---|---|---|
| `filament_max_volumetric_speed` | array of float strings | leave at the stock value until you run a flow test | This key is the real throughput limit. It throttles every speed in section 1.3. |

> [!WARNING]
> Do not raise `filament_max_volumetric_speed` to reach a target speed. The
> hotend cannot melt more material than its physical limit. A raised value gives
> under-extrusion, not more speed. Run a volumetric flow test first, then set the
> measured value.

### What you cannot do in a preset (Recipe 1)

- **You cannot change the physical nozzle.** The preset must match the nozzle
  that is installed. Bind the preset to the correct machine preset, for example
  `Bambu Lab X1 Carbon 0.8 nozzle`.
- **You cannot calibrate flow from a preset.** `filament_flow_ratio` and
  `filament_max_volumetric_speed` need a printed test part.
- **You cannot enable Flow Dynamics Calibration from a preset** on Bambu Lab
  printers. It is a print-dialog option.

---

## Recipe 2: PLA / PETG Mutual Support (Bambu H2D Technique)

PLA and PETG do not bond to each other. Use one material as the support
interface for the other. The support comes off cleanly and leaves a smooth
surface.

### 2.1 Process domain: zero-gap interface

| Key | Type | Value | Reason |
|---|---|---|---|
| `support_top_z_distance` | float string | `"0"` | PLA and PETG do not bond, so a gap is not needed. Zero gap gives the smoothest down-facing surface. |
| `support_bottom_z_distance` | float string | `"0"` | Same reason, for the surface where the model sits on the support. |
| `support_interface_spacing` | float string | `"0"` | Zero spacing makes the interface **roof** solid. A solid roof is what carries the model surface. |

> [!WARNING]
> `support_base_pattern_spacing` is a **different key**. It controls the support
> **body**, not the interface. Setting it to `"0"` makes the whole support block
> solid. That wastes filament and is hard to remove. Guides that say "base pattern
> spacing 0 (solid roof)" conflate the two keys. Set
> `support_interface_spacing` for a solid roof. Leave
> `support_base_pattern_spacing` at the parent value.

### 2.2 Process domain: interface pattern, speed, and thickness

| Key | Type | Value | Reason |
|---|---|---|---|
| `support_interface_pattern` | enum string | `"rectilinear"` | A straight-line roof gives an even, flat surface. Valid values: `auto`, `rectilinear`, `concentric`, `rectilinear_interlaced`, `grid`. |
| `support_interface_speed` | array of float-or-percent strings | `["20"]` to `["30"]` for a 0.8 mm nozzle | A slow interface lays a flat, well-fused roof. |
| `support_interface_top_layers` | int string | 2 or more; `"-1"` means auto | More layers give a stiffer roof under the model. |
| `support_interface_bottom_layers` | int string | 2 or more; `"-1"` means auto | Same, for the bottom interface. |

### 2.3 Process domain: support style and type

| Key | Valid values |
|---|---|
| `support_style` | `default`, `grid`, `snug`, `tree_slim`, `tree_strong`, `tree_hybrid`, `organic` |
| `support_type` | `normal(auto)`, `tree(auto)`, `normal(manual)`, `tree(manual)`, `hybrid(auto)` |

### 2.4 Filament domain: bed temperature compromise

Two materials share one plate, so the bed temperature must suit both. OrcaSlicer
has six plate variants. Each variant has a steady key and an initial-layer key,
for twelve keys in total. All are arrays of int strings.

| Plate | Steady key | Initial-layer key |
|---|---|---|
| Cool plate | `cool_plate_temp` | `cool_plate_temp_initial_layer` |
| Textured cool plate | `textured_cool_plate_temp` | `textured_cool_plate_temp_initial_layer` |
| Engineering plate | `eng_plate_temp` | `eng_plate_temp_initial_layer` |
| Smooth high-temp plate | `hot_plate_temp` | `hot_plate_temp_initial_layer` |
| Textured PEI plate | `textured_plate_temp` | `textured_plate_temp_initial_layer` |
| Supertack plate | `supertack_plate_temp` | `supertack_plate_temp_initial_layer` |

A value of `"0"` means the filament does not support that plate.

> [!IMPORTANT]
> OrcaSlicer reads only the pair for the plate that the operator selects. A
> correct value on the wrong plate does nothing. **Ask the operator which plate
> they use**, then set that pair. Do not guess.

### 2.5 Filament domain: interface temperature and support flag

| Key | Type | Value | Reason |
|---|---|---|---|
| `nozzle_temperature` (interface filament) | array of int strings | higher than the stock value | The interface filament must melt instantly after a tool change, so the roof fuses on the first pass. |
| `filament_is_support` | array of bool strings | `["1"]` | Marks the filament as a support material. |

> [!WARNING]
> There is **no** `filament_is_support_interface` key. `filament_is_support` is
> the only support flag in the filament domain.

### What you cannot do in a preset (Recipe 2)

> [!WARNING]
> **Flushing volumes are not preset data.** The keys `flush_volumes_matrix`,
> `flush_volumes_vector`, and `flush_multiplier` are not in any preset option
> table. They appear in **zero** of the roughly 13000 bundled vendor profiles.
> They live in `OrcaSlicer.conf`, per printer preset, as pipe-delimited float
> strings. Their size depends on the whole filament set that is loaded on the
> plate, so they cannot live in a single filament preset. Set them in the
> **Flushing volumes** dialog. A PLA-to-PETG change needs a large flush volume,
> so do not skip this step.

> [!WARNING]
> **Support interface assignment needs an AMS slot index you do not have.**
> `support_interface_filament` and `support_filament` are process-domain int
> strings that hold an extruder/filament **index**. `"0"` means auto, that is, the
> same filament as the model. The correct index depends on the operator's AMS slot
> layout. Do not hardcode a guess. Ask the operator for the slot number, or leave
> the keys at `"0"` and tell the operator to set the filament in the UI.

- **You cannot assign a per-object support material from a preset.** Per-object
  overrides live in the project file, not the preset.

---

## Recipe 3: Warping Mitigation

Warping is shrinkage tension that lifts the part off the plate. Attack it from
three directions: adhesion, geometry, and cooling.

### 3.1 Process domain: adhesion

| Key | Type | Value | Reason |
|---|---|---|---|
| `brim_type` | enum string | `"outer_only"` | The brim adds plate contact area at the edges, where lifting starts. |
| `brim_width` | float string | wider for a taller or larger part | More contact area resists more tension. |
| `brim_object_gap` | float string | `"0"` for maximum hold | A gap makes the brim easier to remove but lowers the hold. Use `"0"` when warping is the priority. |
| `elefant_foot_compensation` | float string | small positive value | A hot plate spreads the first layer outward. This key shrinks the first layer back to the correct size. Note the OrcaSlicer spelling of the key. |
| `initial_layer_line_width` | float string | wider than `line_width` | A wider first line puts more material against the plate. |

### 3.2 Process domain: geometry

| Key | Type | Value | Reason |
|---|---|---|---|
| `wall_loops` | int string | `"2"` | Each wall loop adds shrinkage tension. Fewer loops pull less on the part edges. |

### 3.3 Process domain: cooling

| Key | Type | Value | Reason |
|---|---|---|---|
| `close_fan_the_first_x_layers` | int string | raise it | The first layers must stay hot to bond to the plate. |
| `fan_min_speed` | array of int strings | lower it | Less air keeps the part warm and lowers the shrinkage rate. |
| `fan_max_speed` | array of int strings | lower it | Same reason, for the fast-cooling case. |
| `additional_cooling_fan_speed` | array of int strings | lower it, or `["0"]` | The aux fan cools the whole chamber. Turn it down for a warp-prone material. |

`fan_min_speed`, `fan_max_speed`, and `additional_cooling_fan_speed` are
**filament domain**. `close_fan_the_first_x_layers` is **process domain**.

### 3.4 Filament domain: bed temperature

Raise the plate temperature for the plate the operator uses. See the twelve-key
table in section 2.4. A hotter plate keeps the first layer soft, so it does not
pull away.

### What you cannot do in a preset (Recipe 3)

- **You cannot set the chamber temperature on a printer that has no heated
  chamber.** A cold draft warps ABS and ASA whatever the preset says. Close the
  enclosure.
- **You cannot fix a dirty or uneven plate from a preset.** Clean the plate and
  run bed levelling first.
- **You cannot add a brim to a part that has no flat bottom.** Use a raft or a
  changed orientation instead.

---

## See Also

- [Editing & DAG Inheritance Guide](editing_rules.md)
- [Generation Guide](generation_rules.md)
- SKILL.md § "Before You Generate: Operator Interview"
- SKILL.md § "Undocumented Serialization Gotchas"
