#!/usr/bin/env python3
"""
OrcaSlicer Profile Manager & Validator CLI
==========================================
A comprehensive, LLM-friendly tool for locating, copying/cloning, editing,
de-linking inheritance, generating templates, inspecting, diffing, and validating
OrcaSlicer configuration and profile JSON files across operating systems.

Subcommands:
  - locate        : Discover installed OrcaSlicer built-in app & user profile directories across OSes.
  - list-vendors  : List installed vendor ecosystems with counts of models, printers, filaments, processes.
  - list-profiles : Search and list installed built-in & user profiles with domain/vendor filters and tree view.
  - inspect       : Deep inspection of a profile (metadata, DAG inheritance chain, key parameters, child dependents, schema health).
  - diff          : Compare two profiles and highlight parameter value deltas.
  - clone         : Find a built-in profile, copy/clone it, generate a new 16-char setting_id, apply edits, de-link inheritance, and validate.
  - template      : Output starter skeleton JSON for any domain (vendor, machine, filament, process, material-db).
  - vendor        : Validate Vendor Meta-Index manifest files against vendor.json schema.
  - machine       : Validate Machine models & variants against machine.json schema.
  - filament      : Validate Filament profiles against filament.json schema (enforces 8-char AMS filament_id limit).
  - process       : Validate Process execution profiles against process.json schema (Arachne/Classic walls, speeds).
  - material-db   : Validate hardware Material Database JSON mappings against material_database.json schema.
  - auto          : Auto-detect profile domain from JSON contents and validate with DAG inheritance resolution.
"""

import sys
import os
import json
import argparse
import platform
import random
import string
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Set

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


# Terminal ANSI Colors
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


def colorize(text: str, color: str, disable_color: bool = False) -> str:
    if disable_color or not sys.stdout.isatty():
        return text
    return f"{color}{text}{Colors.ENDC}"


SCHEMA_MAPPING = {
    "vendor": "vendor.json",
    "machine": "machine.json",
    "filament": "filament.json",
    "process": "process.json",
    "material-db": "material_database.json"
}

SKELETON_TEMPLATES = {
    "vendor": {
        "name": "CustomVendor",
        "version": "01.00.00.00",
        "force_update": "0",
        "description": "Custom vendor profile ecosystem",
        "machine_model_list": [
            {"name": "Custom Model", "sub_path": "machine/Custom Model.json"}
        ],
        "machine_list": [
            {"name": "Custom Printer 0.4 nozzle", "sub_path": "machine/Custom Printer 0.4 nozzle.json"}
        ],
        "process_list": [
            {"name": "0.20mm Standard @Custom", "sub_path": "process/0.20mm Standard @Custom.json"}
        ],
        "filament_list": [
            {"name": "Custom PLA", "sub_path": "filament/Custom PLA.json"}
        ]
    },
    "machine": {
        "type": "machine",
        "name": "Custom Printer 0.4 nozzle",
        "setting_id": "CUSTPRINTER04NOZ",
        "version": "1.9.0.0",
        "printer_model": "Custom Printer",
        "printer_variant": "0.4",
        "nozzle_diameter": ["0.4"],
        "printable_area": ["0x0", "250x0", "250x250", "0x250"],
        "printable_height": "250.0",
        "default_print_profile": "0.20mm Standard @Custom",
        "bed_model": "bed.stl",
        "bed_texture": "texture.png",
        "machine_max_acceleration_x": ["5000"],
        "machine_max_acceleration_y": ["5000"],
        "machine_max_acceleration_z": ["500"],
        "machine_max_speed_x": ["300"],
        "machine_max_speed_y": ["300"],
        "machine_max_speed_z": ["10"],
        "machine_max_jerk_x": ["8"],
        "extruder_colour": ["#00FF00"],
        "machine_start_gcode": "; Start G-code\nG28 ; Home all axes\n",
        "machine_end_gcode": "; End G-code\nM104 S0\nM140 S0\n",
        "use_relative_e_distances": "1",
        "use_firmware_retraction": "0",
        "is_imex": "0",
        "printer_power_consumption": "300.0",
        "electricity_rate": "0.15",
        "estimated_failure_rate": "5%"
    },
    "filament": {
        "type": "filament",
        "name": "Custom PLA @Printer",
        "setting_id": "CUSTPLAPRINTER01",
        "version": "1.9.0.0",
        "filament_id": "CPLA0001",
        "filament_type": ["PLA"],
        "compatible_printers": ["Custom Printer 0.4 nozzle"],
        "filament_density": ["1.24"],
        "filament_cost": ["25.0"],
        "filament_flow_ratio": ["0.98"],
        "fan_min_speed": ["100"],
        "fan_max_speed": ["100"],
        "hot_plate_temp": ["60"],
        "hot_plate_temp_initial_layer": ["65"],
        "nozzle_temperature": ["210"],
        "nozzle_temperature_initial_layer": ["215"],
        "idle_temperature": ["150"],
        "filament_start_gcode": "; Filament start gcode\n"
    },
    "process": {
        "type": "process",
        "name": "0.20mm Standard @Custom",
        "setting_id": "CUSTPROC02000001",
        "version": "1.9.0.0",
        "layer_height": "0.20",
        "initial_layer_print_height": "0.20",
        "wall_generator": "arachne",
        "wall_loops": "3",
        "sparse_infill_density": "15%",
        "sparse_infill_pattern": "gyroid",
        "bottom_surface_pattern": "monotonic",
        "top_surface_pattern": "monotonic",
        "initial_layer_speed": "50",
        "outer_wall_speed": "100",
        "inner_wall_speed": "150",
        "sparse_infill_speed": "180",
        "travel_speed": "250",
        "default_acceleration": "3000",
        "enable_support": "0",
        "brim_type": "no_brim"
    },
    "material-db": {
        "CPLA01": {
            "base": {
                "id": "CPLA01",
                "meterialType": "PLA",
                "name": "Custom PLA Material",
                "brand": "CustomBrand",
                "minTemp": 190.0,
                "maxTemp": 230.0
            },
            "kvParam": {
                "nozzle_temperature": 210.0,
                "filament_max_volumetric_speed": 15.0
            }
        }
    }
}


def generate_setting_id() -> str:
    """Generates a valid 16-character alphanumeric base62 setting_id string."""
    chars = string.ascii_letters + string.digits
    return "".join(random.choice(chars) for _ in range(16))


def get_orcaslicer_paths() -> Dict[str, List[Path]]:
    """
    Returns candidate paths for built-in resources and user configuration directories
    across macOS, Linux, and Windows, including account-specific user folders and Flatpak locations.
    """
    builtin = []
    user_config_bases = []

    env_builtin = os.environ.get("ORCASLICER_PROFILES_DIR")
    if env_builtin:
        builtin.append(Path(env_builtin))

    env_user = os.environ.get("ORCASLICER_USER_DIR")
    if env_user:
        user_config_bases.append(Path(env_user))

    sys_os = platform.system()

    if sys_os == "Darwin":  # macOS
        builtin.extend([
            Path("/Applications/OrcaSlicer.app/Contents/Resources/profiles"),
            Path.home() / "Library/Application Support/OrcaSlicer/system"
        ])
        user_config_bases.extend([
            Path.home() / "Library/Application Support/OrcaSlicer/user",
            Path.home() / "Library/Application Support/OrcaSlicer"
        ])
    elif sys_os == "Linux":
        builtin.extend([
            Path("/usr/share/OrcaSlicer/resources/profiles"),
            Path("/usr/share/OrcaSlicer/profiles"),
            Path.home() / ".local/share/OrcaSlicer/profiles",
            Path.home() / ".config/OrcaSlicer/system"
        ])
        user_config_bases.extend([
            Path.home() / ".config/OrcaSlicer/user",
            Path.home() / ".config/OrcaSlicer",
            Path.home() / ".var/app/io.github.softfever.OrcaSlicer/config/OrcaSlicer/user",
            Path.home() / ".var/app/io.github.softfever.OrcaSlicer/config/OrcaSlicer"
        ])
    elif sys_os == "Windows":
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        appdata = os.environ.get("APPDATA", "")
        localappdata = os.environ.get("LOCALAPPDATA", "")

        builtin.extend([
            Path(pf) / r"OrcaSlicer\resources\profiles",
            Path(pf86) / r"OrcaSlicer\resources\profiles",
            Path(localappdata) / r"Programs\OrcaSlicer\resources\profiles",
            Path(appdata) / r"OrcaSlicer\system"
        ])
        user_config_bases.extend([
            Path(appdata) / r"OrcaSlicer\user",
            Path(appdata) / r"OrcaSlicer"
        ])

    cwd_data = Path.cwd() / "data_dir"
    if cwd_data.exists():
        builtin.append(cwd_data / "system")
        user_config_bases.append(cwd_data / "user")

    user_config = []
    for base in user_config_bases:
        if base.exists():
            user_config.append(base)
            if base.name == "user" or (base / "user").exists():
                u_dir = base if base.name == "user" else base / "user"
                for child in u_dir.glob("user*"):
                    if child.is_dir():
                        user_config.append(child)
                default_dir = u_dir / "default"
                if default_dir.exists():
                    user_config.append(default_dir)

    builtin_existing = sorted(list(set([p for p in builtin if p.exists()])))
    user_existing = sorted(list(set([p for p in user_config if p.exists()])))

    return {
        "builtin_candidates": builtin,
        "builtin_existing": builtin_existing,
        "user_candidates": user_config_bases,
        "user_existing": user_existing
    }


class OrcaSchemaStore:
    """Loads and manages JSON Schemas and their cross-file $ref registry."""
    def __init__(self, schema_dir: Path):
        self.schema_dir = schema_dir
        self.schemas: Dict[str, Dict[str, Any]] = {}
        self.registry: Optional[Registry] = None
        self.load_schemas()

    def load_schemas(self):
        if not self.schema_dir.exists():
            raise FileNotFoundError(f"Schema directory does not exist: {self.schema_dir}")

        resources = []
        for file_path in self.schema_dir.glob("*.json"):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.schemas[file_path.name] = data
                    res = Resource.from_contents(data, default_specification=DRAFT202012)
                    resources.append((file_path.name, res))
                    if "$id" in data:
                        resources.append((data["$id"], res))
            except Exception as e:
                raise RuntimeError(f"Failed to load schema {file_path.name}: {e}")

        if "defs.json" not in self.schemas:
            raise FileNotFoundError("defs.json is required in the schema directory")

        self.registry = Registry().with_resources(resources)

    def get_validator(self, schema_name: str) -> jsonschema.Draft202012Validator:
        if schema_name not in self.schemas:
            raise ValueError(f"Schema '{schema_name}' not loaded.")
        schema = self.schemas[schema_name]
        return jsonschema.Draft202012Validator(schema, registry=self.registry)


class ProfileDAGResolver:
    """Resolves inherited profiles across files using a Directed Acyclic Graph (DAG)."""
    def __init__(self):
        self.index: Dict[Tuple[str, str], Dict[str, Any]] = {}  # (type, name) -> data
        self.name_index: Dict[str, Dict[str, Any]] = {}         # name -> data
        self.file_index: Dict[str, Path] = {}                   # name -> file_path

    def register_profile(self, data: Dict[str, Any], file_path: Optional[Path] = None):
        if isinstance(data, dict):
            p_name = data.get("name")
            p_type = data.get("type")
            if p_name:
                self.name_index[p_name] = data
                if p_type:
                    self.index[(p_type, p_name)] = data
                if file_path:
                    self.file_index[p_name] = file_path

    def scan_directory(self, search_dir: Path):
        if not search_dir or not search_dir.exists():
            return
        for root, _, files in os.walk(search_dir):
            for file in files:
                if file.endswith(".json"):
                    fp = Path(root) / file
                    try:
                        with open(fp, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            self.register_profile(data, fp)
                    except Exception:
                        pass

    def get_inheritance_chain(self, data: Dict[str, Any], visited: Optional[List[str]] = None) -> List[str]:
        """Returns ordered list of profile names in parent inheritance chain."""
        if visited is None:
            visited = []
        p_name = data.get("name", "<unnamed>")
        visited.append(p_name)
        inherits = data.get("inherits")
        if not inherits or inherits in visited:
            return visited
        parent = self.index.get((data.get("type", ""), inherits)) or self.name_index.get(inherits)
        if parent:
            return self.get_inheritance_chain(parent, visited)
        visited.append(f"{inherits} (missing)")
        return visited

    def resolve(self, data: Dict[str, Any], visited: Optional[Set[str]] = None) -> Tuple[Dict[str, Any], List[str]]:
        warnings = []
        if not isinstance(data, dict):
            return data, warnings

        inherits = data.get("inherits")
        if not inherits:
            return dict(data), warnings

        if visited is None:
            visited = set()

        profile_name = data.get("name", "<unnamed>")
        if profile_name in visited:
            warnings.append(f"Circular inheritance detected: {profile_name}")
            return dict(data), warnings

        visited.add(profile_name)

        p_type = data.get("type")
        parent = None
        if p_type:
            parent = self.index.get((p_type, inherits))
        if not parent:
            parent = self.name_index.get(inherits)

        if not parent:
            warnings.append(f"Parent profile '{inherits}' not found in search index for resolution")
            return dict(data), warnings

        resolved_parent, parent_warnings = self.resolve(parent, visited.copy())
        warnings.extend(parent_warnings)

        merged = dict(resolved_parent)
        merged.update(data)
        return merged, warnings


class OrcaValidator:
    """Main validation coordinator."""
    def __init__(self, schema_dir: Path, inherit_dirs: Optional[List[Path]] = None):
        self.store = OrcaSchemaStore(schema_dir)
        self.dag_resolver = ProfileDAGResolver()
        if inherit_dirs:
            for d in inherit_dirs:
                if d and d.exists():
                    self.dag_resolver.scan_directory(d)

    def detect_type(self, data: Any) -> Optional[str]:
        if not isinstance(data, dict):
            return None

        if "machine_model_list" in data or "machine_list" in data:
            return "vendor"

        p_type = data.get("type")
        if p_type in ("machine_model", "machine"):
            return "machine"
        elif p_type == "filament":
            return "filament"
        elif p_type == "process":
            return "process"

        if any(isinstance(v, dict) and "base" in v for v in data.values()):
            return "material-db"

        if "printer_model" in data or "printable_area" in data:
            return "machine"
        if "filament_type" in data or "filament_flow_ratio" in data:
            return "filament"
        if "layer_height" in data or "wall_generator" in data:
            return "process"

        return None

    def validate_data(self, data: Any, expected_domain: str, resolve_inherits: bool = True) -> Tuple[bool, List[str], List[str]]:
        errors = []
        warnings = []

        schema_file = SCHEMA_MAPPING.get(expected_domain)
        if not schema_file:
            errors.append(f"Unknown domain target: '{expected_domain}'")
            return False, errors, warnings

        payload = data
        if resolve_inherits and isinstance(data, dict) and "inherits" in data:
            payload, inherit_warnings = self.dag_resolver.resolve(data)
            warnings.extend(inherit_warnings)

        validator = self.store.get_validator(schema_file)
        schema_errors = sorted(validator.iter_errors(payload), key=lambda e: e.path)

        for err in schema_errors:
            path_str = " -> ".join(str(p) for p in err.path) if err.path else "root"
            msg = f"[{path_str}] {err.message}"
            errors.append(msg)

        is_valid = len(errors) == 0
        return is_valid, errors, warnings

    def validate_file(self, file_path: Path, domain: str = "auto", resolve_inherits: bool = True) -> Dict[str, Any]:
        result = {
            "file": str(file_path),
            "domain": domain,
            "detected_domain": None,
            "valid": False,
            "errors": [],
            "warnings": []
        }

        if not file_path.exists():
            result["errors"].append(f"File not found: {file_path}")
            return result

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            result["errors"].append(f"Invalid JSON syntax: {e}")
            return result
        except Exception as e:
            result["errors"].append(f"Failed to read file: {e}")
            return result

        if isinstance(data, dict):
            self.dag_resolver.register_profile(data, file_path)

        target_domain = domain
        if domain == "auto":
            detected = self.detect_type(data)
            result["detected_domain"] = detected
            if not detected:
                result["errors"].append("Could not auto-detect OrcaSlicer configuration type")
                return result
            target_domain = detected
        else:
            result["detected_domain"] = domain

        valid, errors, warnings = self.validate_data(data, target_domain, resolve_inherits=resolve_inherits)
        result["valid"] = valid
        result["errors"] = errors
        result["warnings"] = warnings
        return result


def find_json_files(targets: List[str]) -> List[Path]:
    files = []
    for t in targets:
        p = Path(t)
        if p.is_file() and p.suffix.lower() == ".json":
            files.append(p)
        elif p.is_dir():
            for root, _, filenames in os.walk(p):
                for f in filenames:
                    if f.lower().endswith(".json"):
                        files.append(Path(root) / f)
    return sorted(list(set(files)))


def search_installed_profiles(paths: List[Path], domain: Optional[str] = None, vendor: Optional[str] = None, query: Optional[str] = None) -> List[Dict[str, Any]]:
    """Searches installed built-in & user directories for OrcaSlicer profile JSON files."""
    results = []
    query_lower = query.lower() if query else None
    vendor_lower = vendor.lower() if vendor else None

    for root_dir in paths:
        if not root_dir or not root_dir.exists():
            continue
        for file_path in root_dir.glob("**/*.json"):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                if not isinstance(data, dict):
                    continue

                p_name = data.get("name", file_path.stem)
                p_type = data.get("type")
                if not p_type:
                    if "machine_model_list" in data or "machine_list" in data:
                        p_type = "vendor"

                if domain and domain != "all" and domain != "auto":
                    if domain == "machine" and p_type not in ("machine", "machine_model"):
                        continue
                    elif domain != "machine" and p_type != domain:
                        continue

                # Determine vendor from path or name
                path_str = str(file_path)
                p_vendor = None
                for part in file_path.parts:
                    if part.endswith(".json") and part != file_path.name:
                        p_vendor = part[:-5]
                    elif part in ("BBL", "Creality", "Voron", "Prusa", "Anycubic", "Qidi", "FLSun", "Artillery"):
                        p_vendor = part

                if vendor_lower:
                    match_v = (p_vendor and vendor_lower in p_vendor.lower()) or (vendor_lower in path_str.lower())
                    if not match_v:
                        continue

                if query_lower:
                    match_name = query_lower in p_name.lower()
                    match_path = query_lower in path_str.lower()
                    if not (match_name or match_path):
                        continue

                results.append({
                    "name": p_name,
                    "type": p_type,
                    "vendor": p_vendor,
                    "path": str(file_path),
                    "inherits": data.get("inherits"),
                    "setting_id": data.get("setting_id")
                })
            except Exception:
                pass

    return results


def get_vendor_summary(paths: List[Path]) -> List[Dict[str, Any]]:
    """Lists installed vendor manifests with counts of models, printers, processes, filaments."""
    vendors = {}
    for root_dir in paths:
        if not root_dir or not root_dir.exists():
            continue
        for file_path in root_dir.glob("*.json"):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and ("machine_model_list" in data or "machine_list" in data):
                    v_name = data.get("name", file_path.stem)
                    vendors[v_name] = {
                        "name": v_name,
                        "version": data.get("version", "unknown"),
                        "description": data.get("description", ""),
                        "machine_models_count": len(data.get("machine_model_list", [])),
                        "machines_count": len(data.get("machine_list", [])),
                        "processes_count": len(data.get("process_list", [])),
                        "filaments_count": len(data.get("filament_list", [])),
                        "manifest_path": str(file_path)
                    }
            except Exception:
                pass
    return sorted(list(vendors.values()), key=lambda x: x["name"])


def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="validate_orca.py",
        description="""OrcaSlicer Configuration Validator & Cross-Platform Profile Manager (Draft 2020-12).

LLM & AUTOMATION DIRECTIVE:
This tool provides a complete programmatic interface for managing OrcaSlicer JSON profiles across macOS, Linux, and Windows.
Use the subcommands below to inspect, list, diff, generate, clone, de-link inheritance, and validate profiles.

SUBCOMMAND SUMMARY:
  locate        : Discover installed OrcaSlicer built-in app & user profile directories.
  list-vendors  : List all installed vendor ecosystems (BBL, Creality, Voron, etc.) with model/printer/profile counts.
  list-profiles : Search built-in & user profiles by domain (machine, filament, process, vendor), vendor, or query.
  inspect       : Deep inspection of a profile (metadata, DAG inheritance chain, key parameters, child dependents, schema health).
  diff          : Compare two profiles and highlight parameter value deltas.
  clone         : Find a built-in profile, copy/clone it, generate a new 16-char setting_id, apply edits, de-link inheritance (--de-link-inherits), and validate.
  template      : Output starter skeleton JSON for any domain (vendor, machine, filament, process, material-db).
  vendor        : Validate Vendor Meta-Index manifest files against vendor.json schema.
  machine       : Validate Machine models & variants against machine.json schema.
  filament      : Validate Filament profiles against filament.json schema (enforces 8-char AMS filament_id limit).
  process       : Validate Process execution profiles against process.json schema (Arachne/Classic walls, speeds).
  material-db   : Validate hardware Material Database JSON mappings against material_database.json schema.
  auto          : Auto-detect profile domain from JSON contents and validate with DAG inheritance resolution.
""",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""SUBCOMMAND USAGE & LLM EXAMPLES:

1. Discover Platform Profile Directories:
   validate_orca.py locate

2. List Installed Vendors:
   validate_orca.py list-vendors
   validate_orca.py list-vendors --json

3. Search & List Profiles:
   validate_orca.py list-profiles --domain filament --query PLA
   validate_orca.py list-profiles --vendor Voron --detail

4. Deep Profile Inspection:
   validate_orca.py inspect "Bambu PLA Basic @BBL X1C"
   validate_orca.py inspect ./custom_process.json --json

5. Diff/Compare Two Profiles:
   validate_orca.py diff "0.20mm Standard @Voron" "0.20mm HighSpeed Voron"
   validate_orca.py diff ./profileA.json ./profileB.json

6. Find, Clone, & Customize a Built-in Profile:
   # Inherited clone:
   validate_orca.py clone filament "Bambu PLA Basic @BBL X1C" --name "My Custom PLA" --out custom_pla.json --set nozzle_temperature='["225"]'
   
   # Standalone clone (de-linked inheritance to prevent stock update corruption):
   validate_orca.py clone process "0.20mm Standard @Voron" --name "0.20mm Standalone Speed" --out custom_process.json --de-link-inherits --set outer_wall_speed='"180"'

7. Validate OrcaSlicer Profiles:
   validate_orca.py auto ./resources/profiles/ --json
"""
    )

    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument("targets", nargs="*", help="File(s) or directory(ies) containing JSON config files.")
    parent_parser.add_argument("--schema-dir", type=str, help="Custom directory containing schema JSON files (defaults to ./schemas).")
    parent_parser.add_argument("--inherit-dir", action="append", help="Directory to scan for parent profiles when resolving 'inherits'. Can be used multiple times.")
    parent_parser.add_argument("--no-resolve-inherits", action="store_true", help="Disable automatic profile inheritance DAG resolution.")
    parent_parser.add_argument("--json", action="store_true", help="Output results formatted as a JSON report.")
    parent_parser.add_argument("--quiet", "-q", action="store_true", help="Only show validation failures.")
    parent_parser.add_argument("--no-color", action="store_true", help="Disable colored terminal output.")

    subparsers = parser.add_subparsers(dest="subcommand", help="Validation or profile management subcommand", required=True)

    domains = {
        "vendor": "Validate Vendor Meta-Index manifest files (e.g. BBL.json)",
        "machine": "Validate Machine models and kinematic variant profiles",
        "filament": "Validate Filament thermal, density, and flow ratio profiles",
        "process": "Validate Process execution and path planning profiles",
        "material-db": "Validate hardware Material Database JSON mappings",
        "auto": "Auto-detect profile type from JSON contents and validate"
    }

    for d, help_str in domains.items():
        subparsers.add_parser(d, parents=[parent_parser], help=help_str)

    template_parser = subparsers.add_parser("template", help="Generate a valid starter skeleton JSON template for any domain")
    template_parser.add_argument("domain", choices=["vendor", "machine", "filament", "process", "material-db"], help="Target profile domain")
    template_parser.add_argument("--out", "-o", type=str, help="Output file path. Defaults to stdout.")

    subparsers.add_parser("locate", help="Locate installed built-in resources & user configuration directories across macOS, Linux, and Windows")

    list_vendors_parser = subparsers.add_parser("list-vendors", help="List installed vendor ecosystems with counts of models, printers, filaments, processes")
    list_vendors_parser.add_argument("--json", action="store_true", help="Output results in JSON format")

    list_parser = subparsers.add_parser("list-profiles", help="Search and list installed built-in & user OrcaSlicer profiles")
    list_parser.add_argument("--domain", choices=["vendor", "machine", "filament", "process", "all"], default="all", help="Filter by profile domain")
    list_parser.add_argument("--vendor", type=str, help="Filter by vendor name (e.g. BBL, Voron, Creality)")
    list_parser.add_argument("--query", "-q", type=str, help="Name or path search query")
    list_parser.add_argument("--detail", action="store_true", help="Show setting_id and inherits information for each profile")
    list_parser.add_argument("--profiles-dir", type=str, help="Custom built-in profiles directory to search")
    list_parser.add_argument("--json", action="store_true", help="Output results in JSON format")

    inspect_parser = subparsers.add_parser("inspect", help="Deep inspection of a profile (metadata, DAG inheritance chain, key parameters, child dependents, schema health)")
    inspect_parser.add_argument("target", help="Name or file path of profile to inspect")
    inspect_parser.add_argument("--profiles-dir", type=str, help="Custom built-in profiles directory to search")
    inspect_parser.add_argument("--schema-dir", type=str, help="Custom schema directory")
    inspect_parser.add_argument("--json", action="store_true", help="Output inspection report in JSON format")

    diff_parser = subparsers.add_parser("diff", help="Compare two profiles and highlight parameter value deltas")
    diff_parser.add_argument("target_a", help="Name or file path of profile A")
    diff_parser.add_argument("target_b", help="Name or file path of profile B")
    diff_parser.add_argument("--no-resolve-inherits", action="store_true", help="Compare raw unmerged JSON instead of resolved DAG profiles")
    diff_parser.add_argument("--profiles-dir", type=str, help="Custom built-in profiles directory to search")
    diff_parser.add_argument("--json", action="store_true", help="Output diff deltas in JSON format")

    clone_parser = subparsers.add_parser("clone", help="Find a built-in profile, copy/clone it with a new name & setting_id, apply edits, de-link inheritance, and validate")
    clone_parser.add_argument("domain", choices=["vendor", "machine", "filament", "process"], help="Target profile domain")
    clone_parser.add_argument("target", help="Name or file path of existing profile to clone")
    clone_parser.add_argument("--name", required=True, help="New profile name")
    clone_parser.add_argument("--out", "-o", help="Output JSON file path. If omitted, uses default user profile directory.")
    clone_parser.add_argument("--inherits", help="Override parent 'inherits' profile name")
    clone_parser.add_argument("--de-link-inherits", action="store_true", help="Flatten parent profile properties and remove 'inherits' link to make profile completely independent of stock profile updates")
    clone_parser.add_argument("--compatible-printers", nargs="+", help="Set compatible printer model names for compatible_printers field")
    clone_parser.add_argument("--set", action="append", help="Property override in key=value format (e.g. --set nozzle_temperature='[\"225\"]')")
    clone_parser.add_argument("--profiles-dir", type=str, help="Custom built-in profiles directory to search")
    clone_parser.add_argument("--schema-dir", type=str, help="Custom schema directory")
    clone_parser.add_argument("--no-validate", action="store_true", help="Skip schema validation after cloning")

    return parser


def main():
    parser = build_cli_parser()
    args = parser.parse_args()

    paths_info = get_orcaslicer_paths()

    # Subcommand: locate
    if args.subcommand == "locate":
        print(colorize("OrcaSlicer Directory Discovery", Colors.HEADER))
        print("=" * 60)
        print(f"Platform: {platform.system()} ({platform.machine()})")
        print("\nBuilt-in App Profiles Directories:")
        if paths_info["builtin_existing"]:
            for p in paths_info["builtin_existing"]:
                print(f"  [{colorize('FOUND', Colors.OKGREEN)}] {p}")
        else:
            print(f"  [{colorize('NOT FOUND', Colors.WARNING)}] Checked:")
            for p in paths_info["builtin_candidates"]:
                print(f"    - {p}")

        print("\nUser Configuration Directories:")
        if paths_info["user_existing"]:
            for p in paths_info["user_existing"]:
                print(f"  [{colorize('FOUND', Colors.OKGREEN)}] {p}")
        else:
            print(f"  [{colorize('NOT FOUND', Colors.WARNING)}] Checked:")
            for p in paths_info["user_candidates"]:
                print(f"    - {p}")
        print("=" * 60)
        sys.exit(0)

    # Subcommand: list-vendors
    if args.subcommand == "list-vendors":
        search_dirs = []
        search_dirs.extend(paths_info["builtin_existing"])
        search_dirs.extend(paths_info["user_existing"])
        search_dirs.append(Path.cwd())

        vendors = get_vendor_summary(search_dirs)
        if args.json:
            print(json.dumps(vendors, indent=2))
        else:
            print(colorize(f"Installed Vendor Ecosystems ({len(vendors)} found)", Colors.HEADER))
            print("=" * 60)
            for v in vendors:
                print(f"Vendor: {colorize(v['name'], Colors.BOLD)}")
                print(f"  Version    : {v['version']}")
                print(f"  Description: {v['description']}")
                print(f"  Models     : {v['machine_models_count']} | Printers: {v['machines_count']} | Processes: {v['processes_count']} | Filaments: {v['filaments_count']}")
                print(f"  Manifest   : {v['manifest_path']}")
                print()
            print("=" * 60)
        sys.exit(0)

    # Subcommand: list-profiles
    if args.subcommand == "list-profiles":
        search_dirs = []
        if args.profiles_dir:
            search_dirs.append(Path(args.profiles_dir).resolve())
        else:
            search_dirs.extend(paths_info["builtin_existing"])
            search_dirs.extend(paths_info["user_existing"])
        search_dirs.append(Path.cwd())

        domain_filter = None if args.domain == "all" else args.domain
        results = search_installed_profiles(search_dirs, domain=domain_filter, vendor=args.vendor, query=args.query)

        if args.json:
            print(json.dumps(results, indent=2))
        else:
            print(colorize(f"Installed Profiles ({len(results)} found)", Colors.HEADER))
            print("=" * 60)
            for item in results:
                t = item['type'] or 'profile'
                v_str = f" [{item['vendor']}]" if item.get('vendor') else ""
                print(f"[{t.upper():<8}]{v_str} {item['name']}")
                print(f"           Path: {item['path']}")
                if args.detail:
                    if item.get('inherits'):
                        print(f"           Inherits  : {item['inherits']}")
                    if item.get('setting_id'):
                        print(f"           Setting ID: {item['setting_id']}")
                print()
            print("=" * 60)
        sys.exit(0)

    # Subcommand: inspect
    if args.subcommand == "inspect":
        search_dirs = []
        if args.profiles_dir:
            search_dirs.append(Path(args.profiles_dir).resolve())
        else:
            search_dirs.extend(paths_info["builtin_existing"])
            search_dirs.extend(paths_info["user_existing"])
        search_dirs.append(Path.cwd())

        # Resolve target
        target_path = None
        tp = Path(args.target)
        if tp.exists() and tp.is_file():
            target_path = tp.resolve()
        else:
            matches = search_installed_profiles(search_dirs, query=args.target)
            if not matches:
                print(f"Error: Could not find profile matching '{args.target}'", file=sys.stderr)
                sys.exit(1)
            target_path = Path(matches[0]["path"])

        with open(target_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        script_dir = Path(__file__).resolve().parent
        schema_dir = Path(args.schema_dir).resolve() if args.schema_dir else script_dir / "schemas"
        if not schema_dir.exists():
            schema_dir = script_dir.parent / "schemas"

        validator = OrcaValidator(schema_dir, inherit_dirs=search_dirs)
        detected_type = validator.detect_type(raw_data) or "unknown"
        chain = validator.dag_resolver.get_inheritance_chain(raw_data)
        resolved_data, warnings = validator.dag_resolver.resolve(raw_data)
        valid, schema_errors, schema_warnings = validator.validate_data(raw_data, detected_type)

        # Find child dependents
        child_dependents = []
        all_profiles = search_installed_profiles(search_dirs)
        p_name = raw_data.get("name")
        if p_name:
            for prof in all_profiles:
                if prof.get("inherits") == p_name:
                    child_dependents.append({"name": prof["name"], "type": prof["type"], "path": prof["path"]})

        # Key configuration highlights
        key_params = {}
        if detected_type == "filament":
            for k in ("filament_id", "filament_type", "filament_density", "filament_cost", "filament_flow_ratio", "nozzle_temperature", "hot_plate_temp", "fan_min_speed", "fan_max_speed"):
                if k in resolved_data:
                    key_params[k] = resolved_data[k]
        elif detected_type == "machine":
            for k in ("printer_model", "printer_variant", "nozzle_diameter", "printable_area", "printable_height", "machine_max_speed_x", "machine_max_acceleration_x", "is_imex", "printer_power_consumption"):
                if k in resolved_data:
                    key_params[k] = resolved_data[k]
        elif detected_type == "process":
            for k in ("layer_height", "wall_generator", "wall_loops", "sparse_infill_density", "sparse_infill_pattern", "outer_wall_speed", "default_acceleration", "enable_support"):
                if k in resolved_data:
                    key_params[k] = resolved_data[k]

        report = {
            "name": raw_data.get("name", target_path.stem),
            "domain": detected_type,
            "setting_id": raw_data.get("setting_id"),
            "version": raw_data.get("version"),
            "file": str(target_path),
            "inheritance_chain": chain,
            "is_independent": "inherits" not in raw_data,
            "child_dependents_count": len(child_dependents),
            "child_dependents": child_dependents,
            "schema_valid": valid,
            "schema_errors": schema_errors,
            "schema_warnings": schema_warnings + warnings,
            "key_parameters": key_params
        }

        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(colorize(f"Profile Inspection Report: {report['name']}", Colors.HEADER))
            print("=" * 60)
            print(f"Domain         : {report['domain']}")
            print(f"Setting ID     : {report['setting_id']}")
            print(f"File Path      : {report['file']}")
            print(f"Inheritance    : {'Independent (De-linked)' if report['is_independent'] else 'Inherited'}")
            print(f"DAG Chain      : {' -> '.join(chain)}")
            print(f"Schema Status  : {colorize('VALID', Colors.OKGREEN) if valid else colorize('INVALID', Colors.FAIL)}")

            if report["schema_errors"]:
                print(colorize("\nSchema Errors:", Colors.FAIL))
                for err in report["schema_errors"]:
                    print(f"  - {err}")

            print(colorize("\nKey Parameters:", Colors.BOLD))
            for k, v in key_params.items():
                print(f"  {k:<35}: {v}")

            print(colorize(f"\nChild Dependents ({len(child_dependents)}):", Colors.BOLD))
            if child_dependents:
                for child in child_dependents[:10]:
                    print(f"  - [{child['type']}] {child['name']}")
                if len(child_dependents) > 10:
                    print(f"  ... and {len(child_dependents) - 10} more.")
            else:
                print("  (None)")
            print("=" * 60)
        sys.exit(0)

    # Subcommand: diff
    if args.subcommand == "diff":
        search_dirs = []
        if args.profiles_dir:
            search_dirs.append(Path(args.profiles_dir).resolve())
        else:
            search_dirs.extend(paths_info["builtin_existing"])
            search_dirs.extend(paths_info["user_existing"])
        search_dirs.append(Path.cwd())

        def resolve_target(t: str) -> Tuple[Path, Dict[str, Any]]:
            tp = Path(t)
            if tp.exists() and tp.is_file():
                with open(tp, "r", encoding="utf-8") as f:
                    return tp.resolve(), json.load(f)
            matches = search_installed_profiles(search_dirs, query=t)
            if not matches:
                print(f"Error: Could not find profile matching '{t}'", file=sys.stderr)
                sys.exit(1)
            fpath = Path(matches[0]["path"])
            with open(fpath, "r", encoding="utf-8") as f:
                return fpath, json.load(f)

        path_a, raw_a = resolve_target(args.target_a)
        path_b, raw_b = resolve_target(args.target_b)

        resolver = ProfileDAGResolver()
        for d in search_dirs:
            resolver.scan_directory(d)

        if not args.no_resolve_inherits:
            data_a, _ = resolver.resolve(raw_a)
            data_b, _ = resolver.resolve(raw_b)
        else:
            data_a, data_b = raw_a, raw_b

        keys_a = set(data_a.keys())
        keys_b = set(data_b.keys())
        all_keys = sorted(list(keys_a.union(keys_b)))

        only_in_a = sorted(list(keys_a - keys_b))
        only_in_b = sorted(list(keys_b - keys_a))
        differing = {}

        for k in all_keys:
            if k in data_a and k in data_b:
                if data_a[k] != data_b[k]:
                    differing[k] = {"a": data_a[k], "b": data_b[k]}

        report = {
            "target_a": {"name": data_a.get("name", path_a.stem), "path": str(path_a)},
            "target_b": {"name": data_b.get("name", path_b.stem), "path": str(path_b)},
            "only_in_a": only_in_a,
            "only_in_b": only_in_b,
            "differing_count": len(differing),
            "differing_parameters": differing
        }

        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(colorize(f"Profile Parameter Diff", Colors.HEADER))
            print("=" * 60)
            print(f"Target A: {report['target_a']['name']} ({report['target_a']['path']})")
            print(f"Target B: {report['target_b']['name']} ({report['target_b']['path']})")
            print(f"Resolved Inheritance: {'No' if args.no_resolve_inherits else 'Yes'}")
            print("=" * 60)

            if differing:
                print(colorize(f"Value Differences ({len(differing)}):", Colors.WARNING))
                for k, v in differing.items():
                    print(f"  {k:<35}: A='{v['a']}' vs B='{v['b']}'")

            if only_in_a:
                print(colorize(f"\nKeys Only in A ({len(only_in_a)}):", Colors.BOLD))
                for k in only_in_a[:10]:
                    print(f"  - {k}: {data_a[k]}")

            if only_in_b:
                print(colorize(f"\nKeys Only in B ({len(only_in_b)}):", Colors.BOLD))
                for k in only_in_b[:10]:
                    print(f"  - {k}: {data_b[k]}")

            if not differing and not only_in_a and not only_in_b:
                print(colorize("Profiles are identical!", Colors.OKGREEN))
            print("=" * 60)
        sys.exit(0)

    # Subcommand: template
    if args.subcommand == "template":
        tmpl = SKELETON_TEMPLATES.get(args.domain)
        if not tmpl:
            print(f"Error: No template found for domain '{args.domain}'", file=sys.stderr)
            sys.exit(1)

        json_output = json.dumps(tmpl, indent=2) + "\n"
        if args.out:
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(json_output)
            print(f"Wrote {args.domain} template to {out_path}")
        else:
            print(json_output)
        sys.exit(0)

    # Subcommand: clone
    if args.subcommand == "clone":
        source_path = None
        target_p = Path(args.target)
        if target_p.exists() and target_p.is_file():
            source_path = target_p.resolve()
        else:
            search_dirs = []
            if args.profiles_dir:
                search_dirs.append(Path(args.profiles_dir).resolve())
            else:
                search_dirs.extend(paths_info["builtin_existing"])
                search_dirs.extend(paths_info["user_existing"])
            search_dirs.append(Path.cwd())

            matches = search_installed_profiles(search_dirs, domain=args.domain, query=args.target)
            if not matches:
                print(f"Error: Could not find existing {args.domain} profile matching '{args.target}'", file=sys.stderr)
                sys.exit(1)
            source_path = Path(matches[0]["path"])

        print(f"Cloning source profile: {source_path}")
        with open(source_path, "r", encoding="utf-8") as f:
            raw_source = json.load(f)

        search_dirs = [source_path.parent] + paths_info["builtin_existing"] + paths_info["user_existing"]
        resolver = ProfileDAGResolver()
        for d in search_dirs:
            resolver.scan_directory(d)

        if args.de_link_inherits:
            print("De-linking profile inheritance (flattening parent chain for independence)...")
            profile_data, _ = resolver.resolve(raw_source)
            profile_data.pop("inherits", None)
            profile_data.pop("from", None)
        else:
            profile_data = dict(raw_source)
            if args.inherits:
                profile_data["inherits"] = args.inherits

        profile_data["name"] = args.name
        profile_data["setting_id"] = generate_setting_id()
        profile_data["from"] = "User"

        if args.compatible_printers:
            profile_data["compatible_printers"] = args.compatible_printers

        if args.set:
            for kv in args.set:
                if "=" not in kv:
                    print(f"Warning: Ignoring invalid --set argument '{kv}', expected key=value", file=sys.stderr)
                    continue
                k, v = kv.split("=", 1)
                try:
                    parsed_val = json.loads(v)
                except Exception:
                    parsed_val = v
                profile_data[k] = parsed_val

        if args.out:
            out_path = Path(args.out).resolve()
        else:
            if paths_info["user_existing"]:
                user_dir = paths_info["user_existing"][0]
            else:
                user_dir = paths_info["user_candidates"][0]
            out_path = user_dir / args.domain / f"{args.name}.json"
            print(f"Auto-resolved output path to: {out_path}")

        out_path.parent.mkdir(parents=True, exist_ok=True)

        if not args.no_validate:
            script_dir = Path(__file__).resolve().parent
            schema_dir = Path(args.schema_dir).resolve() if args.schema_dir else script_dir / "schemas"
            if not schema_dir.exists():
                schema_dir = script_dir.parent / "schemas"

            validator = OrcaValidator(schema_dir, inherit_dirs=search_dirs)
            valid, errors, warnings = validator.validate_data(profile_data, args.domain, resolve_inherits=not args.de_link_inherits)

            if not valid:
                print(colorize("Cloned profile failed schema validation:", Colors.FAIL), file=sys.stderr)
                for err in errors:
                    print(f"  ERROR: {err}", file=sys.stderr)
                sys.exit(1)

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(profile_data, f, indent=2)
            f.write("\n")

        info_path = out_path.with_suffix(".info")
        with open(info_path, "w", encoding="utf-8") as info_f:
            info_f.write(f"sync_info = create\nuser_id = \nsetting_id = \nbase_id = {profile_data['setting_id']}\nupdated_time = 0\n")

        print(colorize(f"Successfully cloned profile to {out_path}", Colors.OKGREEN))
        print(f"  - Name: {args.name}")
        print(f"  - Setting ID: {profile_data['setting_id']}")
        if args.de_link_inherits:
            print("  - Inheritance: Independent (De-linked from stock parent)")
        elif profile_data.get("inherits"):
            print(f"  - Inherits: {profile_data['inherits']}")
        sys.exit(0)

    # Validation Subcommands (vendor, machine, filament, process, material-db, auto)
    if not args.targets:
        print("Error: Targets required for validation.", file=sys.stderr)
        sys.exit(1)

    script_dir = Path(__file__).resolve().parent
    if args.schema_dir:
        schema_dir = Path(args.schema_dir).resolve()
    else:
        candidate_dirs = [
            script_dir / "schemas",
            script_dir.parent / "schemas",
            Path.cwd() / "schemas"
        ]
        schema_dir = None
        for candidate in candidate_dirs:
            if candidate.exists() and (candidate / "defs.json").exists():
                schema_dir = candidate
                break

        if not schema_dir:
            schema_dir = script_dir / "schemas"

    if not schema_dir.exists():
        print(f"Error: Schema directory not found at {schema_dir}", file=sys.stderr)
        sys.exit(2)

    inherit_dirs = []
    if args.inherit_dir:
        for d in args.inherit_dir:
            inherit_dirs.append(Path(d).resolve())

    for t in args.targets:
        tp = Path(t).resolve()
        if tp.is_dir():
            inherit_dirs.append(tp)
        elif tp.is_file():
            inherit_dirs.append(tp.parent)

    inherit_dirs.extend(paths_info["builtin_existing"])
    inherit_dirs.extend(paths_info["user_existing"])

    try:
        validator = OrcaValidator(schema_dir, inherit_dirs=inherit_dirs)
    except Exception as e:
        print(f"Failed to initialize validator: {e}", file=sys.stderr)
        sys.exit(2)

    json_files = find_json_files(args.targets)
    if not json_files:
        print("No JSON files found in provided targets.", file=sys.stderr)
        sys.exit(1)

    results = []
    total_files = len(json_files)
    passed_count = 0
    failed_count = 0

    resolve_inherits = not args.no_resolve_inherits

    for fpath in json_files:
        res = validator.validate_file(fpath, domain=args.subcommand, resolve_inherits=resolve_inherits)
        results.append(res)
        if res["valid"]:
            passed_count += 1
        else:
            failed_count += 1

    if args.json:
        summary = {
            "total": total_files,
            "passed": passed_count,
            "failed": failed_count,
            "results": results
        }
        print(json.dumps(summary, indent=2))
    else:
        print(colorize(f"OrcaSlicer Config Validation Summary ({args.subcommand.upper()})", Colors.HEADER, args.no_color))
        print("=" * 60)

        for res in results:
            rel_path = res["file"]
            try:
                rel_path = str(Path(res["file"]).relative_to(Path.cwd()))
            except Exception:
                pass

            domain_tag = res['detected_domain'] or res['domain']

            if res["valid"]:
                if not args.quiet:
                    status_str = colorize("PASS", Colors.OKGREEN, args.no_color)
                    print(f"[{status_str}] ({domain_tag}) {rel_path}")
            else:
                status_str = colorize("FAIL", Colors.FAIL, args.no_color)
                print(f"[{status_str}] ({domain_tag}) {rel_path}")

                for warn in res["warnings"]:
                    w_str = colorize("WARNING:", Colors.WARNING, args.no_color)
                    print(f"  {w_str} {warn}")

                for err in res["errors"]:
                    e_str = colorize("ERROR:", Colors.FAIL, args.no_color)
                    print(f"  {e_str} {err}")

        print("=" * 60)
        summary_str = f"Total: {total_files} | Passed: {passed_count} | Failed: {failed_count}"
        if failed_count == 0:
            print(colorize(summary_str, Colors.OKGREEN, args.no_color))
        else:
            print(colorize(summary_str, Colors.FAIL, args.no_color))

    sys.exit(0 if failed_count == 0 else 1)


if __name__ == "__main__":
    main()
