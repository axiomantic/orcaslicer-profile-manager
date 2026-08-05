# Listing, Inspecting, and Diffing Profiles

This reference guide details how to use `list-vendors`, `list-profiles`, `inspect`, and `diff` to audit, analyze, and compare OrcaSlicer configuration profiles.

## 1. Vendor Ecosystem Discovery (`list-vendors`)

Use `list-vendors` to discover all installed 3D printer hardware manufacturer ecosystems on the system:
```bash
python validate_orca.py list-vendors
python validate_orca.py list-vendors --json
```

*Summary Details Provided*:
- Canonical Vendor Name (e.g. `BBL`, `Creality`, `Voron`, `Prusa`, `RatRig`)
- Version & Description
- Total counts of associated `machine_models`, `machines`, `processes`, and `filaments`
- Manifest File Path

---

## 2. Profile Searching & Listing (`list-profiles`)

Search installed built-in and user profiles with rich filtering:
```bash
# Filter by domain
python validate_orca.py list-profiles --domain filament

# Filter by vendor
python validate_orca.py list-profiles --vendor Creality

# Filter by search query
python validate_orca.py list-profiles --query "PLA Basic"

# Detailed output showing setting_id and inherits
python validate_orca.py list-profiles --domain process --detail

# Programmatic JSON format
python validate_orca.py list-profiles --domain machine --json
```

---

## 3. Deep Profile Inspection (`inspect`)

Use `inspect` to analyze any profile (by name or file path) and generate a diagnostic report:
```bash
# Inspect built-in profile by name
python validate_orca.py inspect "Bambu PLA Basic @BBL X1C"

# Inspect custom profile by file path
python validate_orca.py inspect ./custom_process.json

# Programmatic JSON report for LLM workflows
python validate_orca.py inspect ./custom_filament.json --json
```

### Inspection Report Sections
1. **Profile Identity**: Name, Domain, 16-char `setting_id`, Version, File Path, Instantiation flag.
2. **Inheritance DAG Chain**: Traces parent inheritance sequence (`Profile -> Parent -> Base`).
3. **Independence**: Flag indicating whether profile is standalone or inherits from stock parent.
4. **Key Parameters**: Highlights critical settings (thermals, flow ratios, speeds, layer heights, AMS `filament_id`, printable area, power consumption).
5. **Child Dependents**: Lists all installed custom profiles that inherit from this profile.
6. **Schema Validation**: Evaluates JSON schema compliance and lists errors/warnings.

---

## 4. Parameter Value Comparison / Diffing (`diff`)

Use `diff` to compare two profiles and highlight exact parameter value deltas:
```bash
# Compare two built-in or custom profiles by name
python validate_orca.py diff "0.20mm Standard @Voron" "0.20mm HighSpeed Voron"

# Compare two profile JSON files
python validate_orca.py diff ./profileA.json ./profileB.json

# Compare raw unmerged JSON without resolving inheritance
python validate_orca.py diff ./profileA.json ./profileB.json --no-resolve-inherits

# Programmatic JSON format
python validate_orca.py diff ./profileA.json ./profileB.json --json
```

### Output Categories
- **Value Differences**: Properties present in both profiles with differing values (`A='val1'` vs `B='val2'`).
- **Keys Only in A**: Properties existing in Profile A but missing in Profile B.
- **Keys Only in B**: Properties existing in Profile B but missing in Profile A.
