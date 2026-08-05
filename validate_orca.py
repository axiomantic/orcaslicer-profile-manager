#!/usr/bin/env python3
"""
OrcaSlicer Profile Manager & Validator CLI
==========================================
A comprehensive, LLM-friendly tool for locating, copying/cloning, editing,
de-linking inheritance, generating templates, inspecting, diffing, and validating
OrcaSlicer configuration and profile JSON files across operating systems.

Subcommands:
  - locate        : Discover installed OrcaSlicer built-in app & user profile directories across OSes.
  - doctor        : Read OrcaSlicer's own debug log for presets the runtime dropped, keys it removed, and count mismatches.
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
import difflib
import platform
import random
import re
import string
import subprocess
from datetime import datetime
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

# OrcaSlicer's *user* preset JSON format is a strict subset of the *system* preset
# format. System bundle profiles are full, self-contained definitions and carry
# "type", "setting_id", and "compatible_printers". A **user** preset that carries
# any of these fields is silently rejected/ignored by OrcaSlicer's preset loader
# (undocumented; see https://github.com/OrcaSlicer/OrcaSlicer/issues/12223).
# User presets must instead: omit "type"/"setting_id"/"compatible_printers"/
# "instantiation" entirely, always declare a non-empty "inherits" pointing at an
# existing (system or user) profile, and set the domain-specific "*_settings_id"
# identity field to match "name".
USER_PRESET_FORBIDDEN_KEYS = ("type", "setting_id", "instantiation")

DOMAIN_SETTINGS_ID_KEY = {
    "process": "print_settings_id",
    "filament": "filament_settings_id",
    "machine": "printer_settings_id",
}

# A user preset with no "version" field is silently skipped by OrcaSlicer's
# loader (confirmed empirically: an otherwise-identical file with "version" set
# loads correctly). This is the preset-format version, not the app version —
# copied from a real installed 0.40mm-derived user preset that loads correctly.
USER_PRESET_VERSION = "2.1.0.19"


def lint_user_preset(profile_data: Dict[str, Any], domain: str) -> List[str]:
    """Checks a profile dict against OrcaSlicer's undocumented user-preset format rules.
    Returns a list of human-readable violations; empty list means the preset is safe to write.
    Note: "compatible_printers" is intentionally not flagged here — clone() always strips it
    from the cloned source first, so if it's present at lint time the caller added it on purpose
    (--compatible-printers or --set compatible_printers=...)."""
    violations = []
    for key in USER_PRESET_FORBIDDEN_KEYS:
        if key in profile_data:
            violations.append(
                f"'{key}' is a system-preset-only field. OrcaSlicer silently rejects user "
                f"presets that carry it; remove it."
            )
    if not profile_data.get("inherits"):
        violations.append(
            "'inherits' is missing/empty. OrcaSlicer requires every user process/filament/machine "
            "preset to inherit from an existing profile; a standalone preset with no inherits is rejected."
        )
    if profile_data.get("from") != "User":
        violations.append("'from' must be set to \"User\" for a user preset.")
    if not profile_data.get("version"):
        violations.append(
            "'version' is missing/empty. OrcaSlicer's loader silently skips user "
            "presets with no version field."
        )
    settings_id_key = DOMAIN_SETTINGS_ID_KEY.get(domain)
    if settings_id_key and not profile_data.get(settings_id_key):
        violations.append(f"'{settings_id_key}' is missing/empty; it should match 'name'.")
    return violations


# A user preset can NEVER inherit from another user preset. OrcaSlicer's loader
# (PresetCollection::load_presets, src/libslic3r/Preset.cpp) stages every preset it reads
# during a directory pass in a *local* deque, and only merges that deque into the
# collection AFTER the loop finishes. Parent lookup, meanwhile, goes through
# find_preset2()/find_preset_internal(), which only searches the already-merged
# collection — so a user preset can never see a sibling user preset as its parent, at any
# load order, on any single pass (there is no retry). On failure the loader logs
# "can not find parent %1% for config %2%!", bumps its error counter, and `continue`s: the
# preset is DROPPED with no UI error at all. Preset.hpp says as much about find_preset2:
# "This function should only be used when finding system(parent) presets for custom preset."
#
# The fix is always the same: point "inherits" at the nearest SYSTEM ancestor and inline
# the values the skipped intermediate user presets declared. That is faithful, because the
# parent is only a STARTING config — load_preset does `preset.config = inherit_preset->config;`
# and then update_diff_values_to_child_config() lays the child's own keys on top.
#
# Keys that describe the preset rather than configure the slicer; they identify the
# intermediate preset, not its settings, so they are never inlined into a child.
FLATTEN_EXCLUDED_KEYS = frozenset(
    ("name", "inherits", "from", "version", "compatible_printers") + USER_PRESET_FORBIDDEN_KEYS
) | set(DOMAIN_SETTINGS_ID_KEY.values())


def is_user_preset(profile_data: Any) -> bool:
    """Returns True when a profile dict is an OrcaSlicer *user* preset rather than a
    system bundle profile. A user preset either says so outright ("from": "User"), or is
    recognisable by the format it is required to use: no "type" key (system-only, see
    USER_PRESET_FORBIDDEN_KEYS) plus a domain identity "*_settings_id" field."""
    if not isinstance(profile_data, dict):
        return False
    if profile_data.get("from") == "User":
        return True
    if "type" in profile_data:
        return False
    return any(key in profile_data for key in DOMAIN_SETTINGS_ID_KEY.values())


def find_nearest_system_ancestor(
    resolver: "ProfileDAGResolver", profile_data: Dict[str, Any]
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Walks UP the "inherits" chain from `profile_data` past every user preset and returns
    (nearest_system_ancestor_name, merged_intermediate_overrides).

    `merged_intermediate_overrides` is the union of the setting keys declared by every USER
    preset on the way up — including `profile_data` itself — applied outermost-first so that
    values nearer the child win. Metadata (FLATTEN_EXCLUDED_KEYS) is left out: only real
    settings get inlined.

    When `profile_data` is already a system preset this is a no-op: it returns that
    profile's own name and an empty dict, so the common case is completely unchanged.
    The ancestor name is None only when the chain runs out of "inherits" without ever
    reaching a system preset; an unresolvable parent name is returned as-is, since the
    caller ([unresolved inherits] / --inherits checks) reports that failure better."""
    if not is_user_preset(profile_data):
        return profile_data.get("name"), {}

    user_chain = []          # child-first: [profile_data, ...outward]
    ancestor_name = None
    current = profile_data
    visited: Set[str] = set()

    while True:
        name = current.get("name")
        if name in visited:
            break                                   # circular chain; stop with what we have
        visited.add(name)
        user_chain.append(current)

        parent_name = current.get("inherits")
        if not parent_name:
            break                                   # chain ends without a system ancestor
        parent = resolver.name_index.get(parent_name)
        if parent is None or not is_user_preset(parent):
            ancestor_name = parent.get("name") if parent else parent_name
            break
        current = parent

    overrides: Dict[str, Any] = {}
    for preset in reversed(user_chain):             # outermost first, nearest wins
        for key, value in preset.items():
            if key not in FLATTEN_EXCLUDED_KEYS:
                overrides[key] = value
    return ancestor_name, overrides


# OrcaSlicer's preset loader silently drops any key it does not recognise: a typo'd
# setting name, or a setting written into the wrong domain, produces no error and
# simply does nothing. schemas/known_keys.json carries the real per-domain option
# names lifted out of the OrcaSlicer binary (see tools/extract_known_keys.py), which
# lets us catch that class of mistake before it reaches disk.
KNOWN_KEYS_FILE = Path(__file__).resolve().parent / "schemas" / "known_keys.json"


def _load_known_keys() -> Optional[Dict[str, Any]]:
    """Loads schemas/known_keys.json. Returns None if it is missing or unusable, in
    which case known-key checking is skipped entirely rather than failing the run."""
    try:
        with open(KNOWN_KEYS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    domains = {}
    for domain in DOMAIN_SETTINGS_ID_KEY:
        keys = data.get(domain)
        if not isinstance(keys, list) or not keys:
            return None
        domains[domain] = set(keys)
    metadata = data.get("metadata")
    return {
        "domains": domains,
        "metadata": set(metadata) if isinstance(metadata, list) else set(),
        "version": (data.get("_source") or {}).get("orcaslicer_version"),
    }


KNOWN_KEYS = _load_known_keys()

KNOWN_KEYS_UNAVAILABLE_NOTE = (
    f"Note: {KNOWN_KEYS_FILE.name} is missing or unreadable, so unknown-setting "
    f"checking is disabled. Regenerate it with tools/extract_known_keys.py."
)


def lint_unknown_keys(profile_data: Dict[str, Any], domain: str) -> List[str]:
    """Checks every key in a profile against the option names OrcaSlicer actually
    accepts for that domain. Returns one human-readable message per key that is
    neither a known option for `domain` nor recognised preset metadata; empty list
    means every key will really take effect. Silently returns [] when the key tables
    are unavailable, or for domains OrcaSlicer has no option table for (e.g. vendor)."""
    if KNOWN_KEYS is None or domain not in KNOWN_KEYS["domains"]:
        return []
    if not isinstance(profile_data, dict):
        return []
    own = KNOWN_KEYS["domains"][domain]
    violations = []
    for key in profile_data:
        if key in own or key in KNOWN_KEYS["metadata"] or key.endswith("_settings_id"):
            continue
        other = [d for d in DOMAIN_SETTINGS_ID_KEY if key in KNOWN_KEYS["domains"][d]]
        if other:
            owners = " or ".join(other)
            violations.append(
                f"'{key}' is a {owners} setting and has no effect in a {domain} preset. "
                f"OrcaSlicer silently ignores it; move it to the {owners} preset."
            )
        else:
            violations.append(
                f"'{key}' is not a known {domain} setting (typo, or removed from this "
                f"OrcaSlicer version). OrcaSlicer silently ignores unknown keys, so it "
                f"will have no effect."
            )
    return violations


# OrcaSlicer rewrites its whole preset state to disk when it exits, so a preset file
# written while the app is open is overwritten/discarded on quit — the operator sees
# no error anywhere, just a preset that never appears. Detection is best-effort: a
# probe that cannot answer must never stop the tool.
ORCASLICER_PROCESS_NAME = "OrcaSlicer"


def is_orcaslicer_running() -> Optional[bool]:
    """Returns True if a running OrcaSlicer process was detected, False if none was, or
    None when detection itself is unavailable (probe binary missing, unsupported
    platform, timeout). Never raises."""
    try:
        if platform.system() == "Windows":
            proc = subprocess.run(["tasklist"], capture_output=True, text=True, timeout=10)
            if proc.returncode != 0:
                return None
            return ORCASLICER_PROCESS_NAME.lower() in proc.stdout.lower()
        proc = subprocess.run(["pgrep", "-f", ORCASLICER_PROCESS_NAME], capture_output=True, text=True, timeout=10)
        # pgrep exits 1 with empty output when nothing matched; anything above that is
        # a real failure of the probe rather than an answer.
        if proc.returncode not in (0, 1):
            return None
        # "-f" matches full command lines, so this tool matches itself whenever it is
        # run from a path containing the app name. Never count our own process tree.
        own = {str(os.getpid()), str(os.getppid())}
        pids = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        return any(pid not in own for pid in pids)
    except Exception:
        return None


def resolve_effective_compatible_printers(resolver: "ProfileDAGResolver", parent_name: str) -> Optional[List[str]]:
    """Returns the compatible_printers list `parent_name` effectively declares once its
    own inheritance chain is merged. Returns None when the parent is unknown or when the
    whole chain declares none at all — an abstract "@base"-style profile, which
    OrcaSlicer treats as compatible with everything."""
    parent = resolver.name_index.get(parent_name)
    if not parent:
        return None
    merged, _ = resolver.resolve(parent)
    printers = merged.get("compatible_printers")
    if isinstance(printers, str):
        printers = [printers]
    if not isinstance(printers, list) or not printers:
        return None
    return printers


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


# ---------------------------------------------------------------------------
# Runtime log forensics ("doctor")
# ---------------------------------------------------------------------------
# Every other check in this file is static analysis: it reads a JSON file and
# reasons about what OrcaSlicer *should* do with it. OrcaSlicer's own debug log is
# the only place that says what it *did* do. A preset it refuses to load is silent
# in the UI but always named in the log, so the log is the ground truth this tool
# otherwise has no access to.
PRESET_DOMAINS = ("process", "filament", "machine")


def get_orcaslicer_log_dirs() -> List[Path]:
    """Returns candidate OrcaSlicer log directories for this platform, in preference
    order. Mirrors the user-config conventions of get_orcaslicer_paths(): the log
    directory is always a "log" folder beside the "user" folder in the data dir."""
    dirs: List[Path] = []

    env_log = os.environ.get("ORCASLICER_LOG_DIR")
    if env_log:
        dirs.append(Path(env_log))

    env_user = os.environ.get("ORCASLICER_USER_DIR")
    if env_user:
        # ORCASLICER_USER_DIR usually points at <data_dir>/user (or a sub-account
        # folder below it); the log dir is a sibling of "user" in the data dir.
        p = Path(env_user)
        for base in (p, p.parent, p.parent.parent):
            dirs.append(base / "log")

    sys_os = platform.system()
    if sys_os == "Darwin":
        dirs.append(Path.home() / "Library/Application Support/OrcaSlicer/log")
    elif sys_os == "Linux":
        dirs.append(Path.home() / ".config/OrcaSlicer/log")
        dirs.append(Path.home() / ".var/app/io.github.softfever.OrcaSlicer/config/OrcaSlicer/log")
    elif sys_os == "Windows":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            dirs.append(Path(appdata) / r"OrcaSlicer\log")

    cwd_data = Path.cwd() / "data_dir"
    if cwd_data.exists():
        dirs.append(cwd_data / "log")

    seen, ordered = set(), []
    for d in dirs:
        if str(d) not in seen:
            seen.add(str(d))
            ordered.append(d)
    return ordered


def find_newest_log(log_dirs: List[Path]) -> Optional[Path]:
    """Returns the most recently modified OrcaSlicer log file across `log_dirs`, or
    None when no readable log file exists. OrcaSlicer names them
    debug_<Day>_<Mon>_<DD>_<HH>_<MM>_<SS>_<pid>.log.<n>; rotation means the newest
    file is not the one with the newest name, so mtime decides."""
    candidates: List[Path] = []
    for d in log_dirs:
        if not d.is_dir():
            continue
        for pattern in ("debug_*.log*", "*.log*"):
            candidates.extend(p for p in d.glob(pattern) if p.is_file())
            if candidates:
                break
    if not candidates:
        return None
    return max(set(candidates), key=lambda p: p.stat().st_mtime)


# Three phrasings of the same fatal condition, from three OrcaSlicer code paths
# (load, import, save). All three mean: the preset was DROPPED, and the UI said
# nothing. Verified against OrcaSlicer 2.4.2 sources and a real log.
_DROP_PATTERNS = (
    re.compile(r"can not find parent preset for (?P<config>.+?)\s*,\s*inherits (?P<parent>.+?)\s*$"),
    re.compile(r"can not find inherit preset for user preset (?P<config>.+?)\s*,\s*just skip"),
    re.compile(r"can not find parent (?P<parent>.+?) for config (?P<config>.+?)!\s*$"),
)

# Emitted once per preset directory by PresetCollection::load_presets.
_LOADED_RE = re.compile(r'loaded (?P<count>\d+) presets? from "(?P<dir>[^"]+)"\s*,\s*type (?P<type>[A-Za-z_]+)')

# Emitted by Preset::remove_invalid_keys -- the runtime counterpart of this tool's
# static known-key check. Whatever OrcaSlicer removed here really was rejected.
_INCORRECT_KEYS_RE = re.compile(
    r"contains the following incorrect keys:\s*(?P<keys>.+?)\s*,?\s*which (?:were|was) removed",
    re.IGNORECASE,
)


def _preset_name_from_path(text: str) -> str:
    """Reduces a logged config reference to a bare preset name. OrcaSlicer logs a
    full .json path in some messages and a plain name in others; both must collapse
    to the same identity so counts and cross-references line up."""
    text = text.strip().strip('"')
    if "/" in text or "\\" in text:
        text = text.replace("\\", "/").rsplit("/", 1)[-1]
    if text.lower().endswith(".json"):
        text = text[:-5]
    return text


def _domain_from_config_path(text: str) -> Optional[str]:
    parts = text.replace("\\", "/").split("/")
    for domain in PRESET_DOMAINS:
        if domain in parts[:-1]:
            return domain
    return None


def _preset_name_from_log_prefix(prefix: str) -> str:
    """Extracts the preset name from the text preceding 'contains the following
    incorrect keys'. The name is quoted when OrcaSlicer has one to quote; otherwise
    fall back to the message body after the log's own '[thread]:' prefix."""
    quoted = re.findall(r'"([^"]+)"', prefix)
    if quoted:
        return _preset_name_from_path(quoted[-1])
    tail = prefix.rsplit("]:", 1)[-1]
    tail = re.sub(r"^.*?(?:Error in a preset file:)?\s*(?:The\s+)?[Pp]reset\s+", "", tail).strip()
    return _preset_name_from_path(tail)


def parse_orca_log(text: str) -> Dict[str, Any]:
    """Parses an OrcaSlicer debug log into the three facts static analysis cannot
    see: presets dropped for an unresolvable parent, keys OrcaSlicer stripped out of
    a preset, and how many presets each directory actually loaded."""
    dropped: List[Dict[str, str]] = []
    removed: List[Dict[str, Any]] = []
    loaded: Dict[str, Dict[str, Any]] = {}

    for line in text.splitlines():
        for pattern in _DROP_PATTERNS:
            m = pattern.search(line)
            if not m:
                continue
            groups = m.groupdict()
            config = groups["config"]
            entry = {
                "preset": _preset_name_from_path(config),
                "parent": (groups.get("parent") or "").strip() or None,
                "domain": _domain_from_config_path(config),
                "path": config.strip() if ("/" in config or "\\" in config) else None,
            }
            if entry not in dropped:
                dropped.append(entry)
            break

        m = _INCORRECT_KEYS_RE.search(line)
        if m:
            keys = [k.strip().strip('"\'') for k in re.split(r"[,\s]+", m.group("keys")) if k.strip()]
            removed.append({
                "preset": _preset_name_from_log_prefix(line[:m.start()]),
                "keys": keys,
            })

        m = _LOADED_RE.search(line)
        if m:
            domain = m.group("type").strip().lower()
            # A session can load a directory more than once; the last report wins.
            loaded[domain] = {"count": int(m.group("count")), "dir": m.group("dir")}

    return {"dropped": dropped, "removed_keys": removed, "loaded": loaded}


def _known_key_domains(key: str) -> List[str]:
    if KNOWN_KEYS is None:
        return []
    return [d for d in PRESET_DOMAINS if key in KNOWN_KEYS["domains"].get(d, set())]


def detect_known_key_drift(removed: List[Dict[str, Any]], preset_domains: Dict[str, str]) -> List[Dict[str, Any]]:
    """Cross-references keys OrcaSlicer removed at runtime against known_keys.json.
    A key our table calls valid but OrcaSlicer threw away is DRIFT: either the table
    is wrong or the installed OrcaSlicer is a different version than it was
    extracted from. Returns one record per removed-key occurrence that drifts."""
    if KNOWN_KEYS is None:
        return []
    drift = []
    for entry in removed:
        domain = preset_domains.get(entry["preset"])
        for key in entry["keys"]:
            owners = _known_key_domains(key)
            if not owners:
                continue  # genuinely unknown to us too -- our table already agrees.
            if domain is not None and domain not in owners:
                continue  # a wrong-domain key; the static check already flags this.
            drift.append({
                "preset": entry["preset"],
                "key": key,
                "domain": domain,
                "known_for": owners,
            })
    return drift


def _resolve_user_dir(explicit: Optional[str], loaded: Dict[str, Dict[str, Any]]) -> Optional[Path]:
    """Finds the preset directory whose contents the log's load counts describe.
    Preference: what the operator named, then what the log itself named, then
    discovery. The log's own path is trusted over discovery because it is the
    directory OrcaSlicer actually read."""
    if explicit:
        return Path(explicit).expanduser()
    for info in loaded.values():
        d = Path(info["dir"])
        if d.parent.exists():
            return d.parent
    for base in get_orcaslicer_paths()["user_existing"]:
        if any((base / domain).is_dir() for domain in PRESET_DOMAINS):
            return base
    return None


def run_doctor(log_path: Path, user_dir: Optional[Path]) -> Dict[str, Any]:
    """Reads one OrcaSlicer log and reports what the runtime rejected. Returns a
    machine-readable report; never raises for a merely unhealthy install."""
    text = log_path.read_text(encoding="utf-8", errors="replace")
    parsed = parse_orca_log(text)
    resolved_dir = _resolve_user_dir(str(user_dir) if user_dir else None, parsed["loaded"])

    # Map preset name -> domain, from the files on disk plus the paths in the log.
    # Needed to decide which known_keys domain a removed key should be judged against.
    preset_domains: Dict[str, str] = {}
    files_by_domain: Dict[str, List[str]] = {}
    newest_preset_mtime = 0.0
    for domain in PRESET_DOMAINS:
        d = (resolved_dir / domain) if resolved_dir else None
        names = sorted(p.stem for p in d.glob("*.json")) if d and d.is_dir() else []
        files_by_domain[domain] = names
        for name in names:
            preset_domains.setdefault(name, domain)
        if d and d.is_dir():
            for p in d.glob("*.json"):
                newest_preset_mtime = max(newest_preset_mtime, p.stat().st_mtime)
    for entry in parsed["dropped"]:
        if entry["domain"]:
            preset_domains.setdefault(entry["preset"], entry["domain"])

    counts = []
    for domain in PRESET_DOMAINS:
        info = parsed["loaded"].get(domain)
        on_disk = files_by_domain[domain]
        record: Dict[str, Any] = {
            "domain": domain,
            "files": len(on_disk),
            "loaded": info["count"] if info else None,
            "dir": info["dir"] if info else (str(resolved_dir / domain) if resolved_dir else None),
            "mismatch": False,
            "unaccounted": [],
            "unexplained": 0,
        }
        if info is not None and resolved_dir is not None and record["loaded"] != record["files"]:
            record["mismatch"] = True
            dropped_here = [e["preset"] for e in parsed["dropped"] if preset_domains.get(e["preset"]) == domain]
            record["unaccounted"] = [n for n in dropped_here if n in on_disk] or dropped_here
            record["unexplained"] = max(0, (record["files"] - record["loaded"]) - len(record["unaccounted"]))
        counts.append(record)

    log_mtime = log_path.stat().st_mtime
    stale = bool(newest_preset_mtime and log_mtime < newest_preset_mtime)

    return {
        "log": str(log_path),
        "log_mtime": datetime.fromtimestamp(log_mtime).isoformat(timespec="seconds"),
        "user_dir": str(resolved_dir) if resolved_dir else None,
        "stale": stale,
        "newest_preset_mtime": (
            datetime.fromtimestamp(newest_preset_mtime).isoformat(timespec="seconds")
            if newest_preset_mtime else None
        ),
        "dropped": parsed["dropped"],
        "removed_keys": parsed["removed_keys"],
        "known_key_drift": detect_known_key_drift(parsed["removed_keys"], preset_domains),
        "known_keys_version": KNOWN_KEYS["version"] if KNOWN_KEYS else None,
        "counts": counts,
        "healthy": not parsed["dropped"] and not any(c["mismatch"] for c in counts),
    }


def print_doctor_report(report: Dict[str, Any], no_color: bool = False) -> None:
    print(colorize("OrcaSlicer Runtime Log Diagnosis", Colors.HEADER, no_color))
    print("=" * 60)
    print(f"Log       : {report['log']}")
    print(f"Written   : {report['log_mtime']}")
    print(f"User dir  : {report['user_dir'] or '(not found)'}")
    if report["stale"]:
        print(colorize(
            f"  STALE LOG: presets were modified at {report['newest_preset_mtime']}, after this log "
            f"was written. Restart OrcaSlicer and re-run, or this report describes an older state.",
            Colors.WARNING, no_color))
    print()

    print(colorize(f"Dropped presets ({len(report['dropped'])})", Colors.BOLD, no_color))
    if not report["dropped"]:
        print(f"  [{colorize('OK', Colors.OKGREEN, no_color)}] no preset was rejected for an unresolvable parent.")
    for entry in report["dropped"]:
        tag = colorize("DROPPED", Colors.FAIL, no_color)
        domain = entry["domain"] or "?"
        print(f"  [{tag}] ({domain}) {entry['preset']}")
        print(f"            unresolved parent: {entry['parent'] or '(not named in log)'}")
    print()

    print(colorize(f"Keys removed by OrcaSlicer ({len(report['removed_keys'])})", Colors.BOLD, no_color))
    if not report["removed_keys"]:
        print(f"  [{colorize('OK', Colors.OKGREEN, no_color)}] no preset had keys stripped at load time.")
    for entry in report["removed_keys"]:
        tag = colorize("REMOVED", Colors.WARNING, no_color)
        print(f"  [{tag}] {entry['preset']}: {', '.join(entry['keys'])}")
    if report["known_key_drift"]:
        print()
        print(colorize(f"KNOWN-KEY DRIFT ({len(report['known_key_drift'])})", Colors.FAIL, no_color))
        print(f"  schemas/known_keys.json was extracted from OrcaSlicer {report['known_keys_version'] or '?'}.")
        for d in report["known_key_drift"]:
            print(f"  [{colorize('DRIFT', Colors.FAIL, no_color)}] '{d['key']}' is listed as a valid "
                  f"{'/'.join(d['known_for'])} setting, but OrcaSlicer removed it from {d['preset']}.")
        print("  Our key table disagrees with the installed OrcaSlicer. Re-run tools/extract_known_keys.py.")
    print()

    print(colorize("Preset counts (files on disk vs presets OrcaSlicer loaded)", Colors.BOLD, no_color))
    for c in report["counts"]:
        if c["loaded"] is None:
            print(f"  [{colorize('SKIP', Colors.WARNING, no_color)}] {c['domain']:9s} "
                  f"files={c['files']} loaded=? (log reports no load for this directory)")
        elif c["mismatch"]:
            print(f"  [{colorize('MISMATCH', Colors.FAIL, no_color)}] {c['domain']:9s} "
                  f"files={c['files']} loaded={c['loaded']}")
            for name in c["unaccounted"]:
                print(f"            unaccounted for: {name}")
            if c["unexplained"]:
                print(f"            {c['unexplained']} further preset(s) unaccounted for with no log line naming them.")
        else:
            print(f"  [{colorize('OK', Colors.OKGREEN, no_color)}] {c['domain']:9s} "
                  f"files={c['files']} loaded={c['loaded']}")
    print("=" * 60)
    if report["healthy"]:
        print(colorize("No dropped presets and no count mismatches.", Colors.OKGREEN, no_color))
    else:
        print(colorize(
            f"{len(report['dropped'])} dropped preset(s), "
            f"{sum(1 for c in report['counts'] if c['mismatch'])} count mismatch(es).",
            Colors.FAIL, no_color))


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

        # A minimal-diff *user* preset (correctly!) carries none of the probe keys
        # above — it only has identity fields plus whatever few keys were overridden.
        # Its domain-specific "*_settings_id" field is the one thing that's always
        # present and unambiguous, so fall back to it before giving up.
        for domain, settings_id_key in DOMAIN_SETTINGS_ID_KEY.items():
            if settings_id_key in data:
                return domain

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

        # A profile living under a "user/" directory (or explicitly tagged from:"User")
        # is subject to OrcaSlicer's stricter, undocumented user-preset format — flag
        # violations as warnings here even when the file is otherwise schema-valid, since
        # schema validity does NOT guarantee OrcaSlicer's preset loader will show the profile.
        looks_like_user_preset = isinstance(data, dict) and (
            data.get("from") == "User" or "user" in {p.lower() for p in file_path.parts}
        )
        if looks_like_user_preset and target_domain in DOMAIN_SETTINGS_ID_KEY and isinstance(data, dict):
            for lint_msg in lint_user_preset(data, target_domain):
                warnings.append(f"[user-preset format] {lint_msg}")

        # A preset naming a parent that does not exist is silently dropped by OrcaSlicer's
        # loader: the file is perfectly valid, and the preset simply never appears in the
        # UI. The parent is very often another *user* preset, so this resolves against
        # everything the DAG resolver indexed (built-in and user directories alike) —
        # checking only the built-in bundles would flag legitimate user-to-user chains.
        if looks_like_user_preset and isinstance(data, dict):
            inherits = data.get("inherits")
            if inherits and inherits not in self.dag_resolver.name_index:
                warnings.append(
                    f"[unresolved inherits] Parent profile '{inherits}' was not found in any indexed "
                    f"profile directory. OrcaSlicer silently drops a preset whose parent cannot be "
                    f"resolved; run 'list-profiles' to find the exact parent name."
                )

        # A parent that resolves is still not necessarily a parent OrcaSlicer can USE: a
        # user preset naming another *user* preset is dropped by the loader just as
        # silently as one naming a parent that does not exist at all (see
        # FLATTEN_EXCLUDED_KEYS comment above for the mechanism).
        if looks_like_user_preset and isinstance(data, dict):
            inherits = data.get("inherits")
            parent = self.dag_resolver.name_index.get(inherits) if inherits else None
            if parent is not None and is_user_preset(parent):
                ancestor, _ = find_nearest_system_ancestor(self.dag_resolver, data)
                ancestor_str = f"'{ancestor}'" if ancestor else "the nearest system preset"
                warnings.append(
                    f"[user-from-user inherits] Parent profile '{inherits}' is itself a user preset. "
                    f"OrcaSlicer cannot resolve a user preset's parent to another user preset and "
                    f"silently drops the child, so this preset will never appear. Point 'inherits' at "
                    f"{ancestor_str} instead and inline the intermediate values "
                    f"(tools/flatten_user_inherits.py does exactly that)."
                )

        # Keys OrcaSlicer does not recognise for this domain are accepted by the schema
        # but silently dropped by the preset loader, so surface them as warnings too.
        if isinstance(data, dict) and target_domain in DOMAIN_SETTINGS_ID_KEY:
            for lint_msg in lint_unknown_keys(data, target_domain):
                warnings.append(f"[unknown key] {lint_msg}")

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
                    else:
                        # Valid USER presets deliberately carry no "type" key (this is
                        # required by OrcaSlicer's user-preset format — see SKILL.md
                        # § "User Presets vs System Presets"). Without this fallback,
                        # every user preset gets p_type = None and is silently rejected
                        # by the domain filter below, making them invisible as clone
                        # sources. Infer the domain from whichever domain identity key
                        # is present in the body, falling back to the containing
                        # directory name (user presets live in user/default/<domain>/).
                        for candidate_domain, id_key in DOMAIN_SETTINGS_ID_KEY.items():
                            if id_key in data:
                                p_type = candidate_domain
                                break
                        else:
                            for part in file_path.parts:
                                if part in DOMAIN_SETTINGS_ID_KEY:
                                    p_type = part
                                    break

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
  doctor        : Diagnose the installed setup from OrcaSlicer's own debug log (silently dropped presets, removed keys, count mismatches).
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

8. Diagnose What OrcaSlicer Actually Rejected At Runtime:
   validate_orca.py doctor
   validate_orca.py doctor --log ~/Library/Application\\ Support/OrcaSlicer/log/debug_....log.0 --json
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

    doctor_parser = subparsers.add_parser("doctor", help="Read OrcaSlicer's own debug log to find presets the runtime silently dropped, keys it removed, and file-vs-loaded count mismatches")
    doctor_parser.add_argument("--log", type=str, help="Path to a specific OrcaSlicer debug log. Defaults to the newest log in the platform log directory.")
    doctor_parser.add_argument("--user-dir", type=str, help="User preset directory containing process/, filament/, machine/ subfolders. Defaults to the directory named in the log.")
    doctor_parser.add_argument("--json", action="store_true", help="Output the diagnosis in JSON format")
    doctor_parser.add_argument("--no-color", action="store_true", help="Disable colored terminal output")

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
    clone_parser.add_argument("--allow-unknown-keys", action="store_true", help="Downgrade the abort on an unknown/wrong-domain --set key to a warning (for settings added by a newer OrcaSlicer than schemas/known_keys.json was extracted from)")
    clone_parser.add_argument("--ignore-running", action="store_true", help="Downgrade the abort on a running OrcaSlicer process to a warning (OrcaSlicer discards presets written while it is open)")
    clone_parser.add_argument("--skip-compat-check", action="store_true", help="Downgrade the abort on a --compatible-printers value the parent chain does not support to a warning")

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

    # Subcommand: doctor
    if args.subcommand == "doctor":
        if args.log:
            log_path = Path(args.log).expanduser()
            if not log_path.is_file():
                msg = f"No OrcaSlicer log at {log_path}. Pass an existing file with --log."
                print(json.dumps({"ok": False, "error": msg}, indent=2) if args.json
                      else colorize(f"ERROR: {msg}", Colors.FAIL, args.no_color))
                sys.exit(2)
        else:
            log_dirs = get_orcaslicer_log_dirs()
            log_path = find_newest_log(log_dirs)
            if log_path is None:
                msg = (
                    "No OrcaSlicer log directory or log file was found, so runtime diagnosis is "
                    "not possible. Checked: " + ", ".join(str(d) for d in log_dirs) +
                    ". Run OrcaSlicer once, or pass a log explicitly with --log."
                )
                print(json.dumps({"ok": False, "error": msg}, indent=2) if args.json
                      else colorize(f"ERROR: {msg}", Colors.FAIL, args.no_color))
                sys.exit(2)

        report = run_doctor(log_path, Path(args.user_dir).expanduser() if args.user_dir else None)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print_doctor_report(report, args.no_color)
        sys.exit(0 if report["healthy"] else 1)

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
        # Nothing below is worth doing while OrcaSlicer is open: it rewrites its preset
        # state on exit and throws away whatever was written behind its back. Checked
        # first so the abort costs nothing. A "cannot determine" answer never blocks.
        running = is_orcaslicer_running()
        if running:
            if args.ignore_running:
                print(colorize("OrcaSlicer appears to be running; writing anyway (--ignore-running).", Colors.WARNING), file=sys.stderr)
            else:
                print(colorize("Clone aborted: OrcaSlicer is running.", Colors.FAIL), file=sys.stderr)
                print("  OrcaSlicer rewrites its preset state when it exits and will discard any preset written while it is open.", file=sys.stderr)
                print("  Quit OrcaSlicer, then re-run. Pass --ignore-running to write anyway.", file=sys.stderr)
                sys.exit(1)

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

        # OrcaSlicer user presets are always a *diff* against a named parent that must
        # still exist (see USER_PRESET_FORBIDDEN_KEYS comment above) — a fully
        # standalone preset with no "inherits" is rejected by the loader. So both
        # modes below set "inherits" to the profile actually being cloned (never a
        # grandparent), and differ only in how much of its resolved body gets
        # explicitly redeclared in the child:
        #   - normal clone : empty diff: only the explicit --set overrides. Future
        #     edits to the parent chain continue to flow through, same as a GUI
        #     "Save As" of a modified preset.
        #   - --de-link-inherits : every resolved key is redeclared in the child, so
        #     the child's values can never drift when the parent chain is updated.
        #     Full independence isn't possible in OrcaSlicer's preset system (the
        #     "inherits" link itself must stay valid), so this only protects values,
        #     not the link target.
        parent_name = raw_source.get("name") or args.target

        # ...with one hard exception: the source may not be a system preset. OrcaSlicer
        # cannot resolve a user preset's parent to another user preset and drops the child
        # outright (see FLATTEN_EXCLUDED_KEYS comment above), so cloning from a user preset
        # has to skip past it to the nearest SYSTEM ancestor and re-declare, in the child,
        # every setting the skipped user presets contributed. --set is applied further down
        # and therefore still wins over anything inlined here.
        flattened_overrides: Dict[str, Any] = {}
        if is_user_preset(raw_source):
            system_ancestor, flattened_overrides = find_nearest_system_ancestor(resolver, raw_source)
            if system_ancestor:
                parent_name = system_ancestor
            print(colorize(
                f"Source '{raw_source.get('name', args.target)}' is a user preset. OrcaSlicer does not "
                f"support user-from-user inheritance and silently drops such presets, so the chain was "
                f"flattened.", Colors.WARNING))
            print(f"  - Inherits set to nearest system ancestor: {parent_name}")
            if flattened_overrides:
                print(f"  - Inlined {len(flattened_overrides)} intermediate setting(s): "
                      f"{', '.join(sorted(flattened_overrides))}")

        # A hallucinated --inherits target is this tool's most expensive silent failure:
        # the preset writes, validates, and then never appears, because OrcaSlicer drops
        # any preset whose parent cannot be resolved. Only an *explicit* --inherits is
        # checked — the normal path inherits the cloned source's own name, which exists
        # by construction, so re-resolving it could only ever produce a false alarm.
        if args.inherits and args.inherits not in resolver.name_index:
            print(colorize(f"Clone aborted: --inherits '{args.inherits}' does not name any installed profile.", Colors.FAIL), file=sys.stderr)
            close = difflib.get_close_matches(args.inherits, list(resolver.name_index), n=5)
            if close:
                print("  Closest installed profile names:", file=sys.stderr)
                for c in close:
                    print(f"    - {c}", file=sys.stderr)
            print("  Run 'validate_orca.py list-profiles --query <text>' to find the exact parent name.", file=sys.stderr)
            sys.exit(1)

        if args.de_link_inherits:
            print("De-linking profile inheritance (flattening parent chain for independence)...")
            profile_data, _ = resolver.resolve(raw_source)
        else:
            profile_data = {}

        # Applied for both modes so they stay consistent. Under --de-link-inherits the
        # resolved body already carries these values (resolve() merges the same chain), so
        # this re-states rather than changes them; on the normal path it is the only thing
        # carrying the skipped user presets' settings into the child.
        profile_data.update(flattened_overrides)

        profile_data["inherits"] = args.inherits if args.inherits else parent_name
        profile_data["name"] = args.name
        profile_data["from"] = "User"
        profile_data.setdefault("version", USER_PRESET_VERSION)

        settings_id_key = DOMAIN_SETTINGS_ID_KEY.get(args.domain)
        if settings_id_key:
            profile_data[settings_id_key] = args.name

        for forbidden_key in USER_PRESET_FORBIDDEN_KEYS:
            profile_data.pop(forbidden_key, None)
        profile_data.pop("compatible_printers", None)

        if args.compatible_printers:
            profile_data["compatible_printers"] = args.compatible_printers

            # OrcaSlicer only offers a preset for a printer that its *parent chain* also
            # accepts. Binding a child to a printer the parent does not support leaves a
            # perfectly valid file that is invisible in the UI for that printer. A parent
            # chain declaring no compatible_printers at all is abstract (an "@base"
            # profile) and is compatible with everything, so it never blocks.
            parent_printers = resolve_effective_compatible_printers(resolver, profile_data["inherits"])
            unsupported = [p for p in args.compatible_printers if p not in parent_printers] if parent_printers else []
            if unsupported:
                headline = (
                    f"Parent profile '{profile_data['inherits']}' does not support: "
                    + ", ".join(f"'{p}'" for p in unsupported)
                )
                supported = "  Parent supports only: " + ", ".join(f"'{p}'" for p in parent_printers)
                if args.skip_compat_check:
                    print(colorize(f"WARNING: {headline} (--skip-compat-check)", Colors.WARNING), file=sys.stderr)
                    print(supported, file=sys.stderr)
                else:
                    print(colorize(f"Clone aborted: {headline}", Colors.FAIL), file=sys.stderr)
                    print(supported, file=sys.stderr)
                    print("  The preset would be valid but invisible in OrcaSlicer for that printer.", file=sys.stderr)
                    print("  Clone a parent that supports it, or pass --skip-compat-check to write anyway.", file=sys.stderr)
                    sys.exit(1)

        set_keys = []
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
                set_keys.append(k)

        # A --set key OrcaSlicer does not know is worse than useless: the clone writes
        # fine, the slicer loads it, and the setting silently does nothing. Catch it
        # before anything reaches disk.
        if set_keys:
            if KNOWN_KEYS is None:
                print(colorize(KNOWN_KEYS_UNAVAILABLE_NOTE, Colors.WARNING), file=sys.stderr)
            unknown_violations = lint_unknown_keys({k: profile_data[k] for k in set_keys}, args.domain)
            if unknown_violations:
                if args.allow_unknown_keys:
                    print(colorize("Proceeding with unrecognised --set keys (--allow-unknown-keys):", Colors.WARNING), file=sys.stderr)
                    for v in unknown_violations:
                        print(f"  WARNING: {v}", file=sys.stderr)
                else:
                    print(colorize("Clone aborted: --set used keys OrcaSlicer will silently ignore:", Colors.FAIL), file=sys.stderr)
                    for v in unknown_violations:
                        print(f"  ERROR: {v}", file=sys.stderr)
                    print("  Pass --allow-unknown-keys to write the preset anyway.", file=sys.stderr)
                    sys.exit(1)

        lint_violations = lint_user_preset(profile_data, args.domain)
        if lint_violations:
            print(colorize("Cloned profile violates OrcaSlicer's user-preset format rules:", Colors.FAIL), file=sys.stderr)
            for v in lint_violations:
                print(f"  ERROR: {v}", file=sys.stderr)
            sys.exit(1)

        cloned_setting_id = generate_setting_id()

        if args.out:
            out_path = Path(args.out).resolve()
        else:
            if paths_info["user_existing"]:
                # The user_existing path is usually the root 'OrcaSlicer' folder or 'user' folder.
                # OrcaSlicer stores presets in 'user/default' for local users.
                user_base = paths_info["user_existing"][0]
                if user_base.name != "default":
                    user_dir = user_base / "user" / "default"
                else:
                    user_dir = user_base
            else:
                user_dir = paths_info["user_candidates"][0] / "user" / "default"
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
            info_f.write(f"sync_info = create\nuser_id = \nsetting_id = \nbase_id = {cloned_setting_id}\nupdated_time = 0\n")

        print(colorize(f"Successfully cloned profile to {out_path}", Colors.OKGREEN))
        print(f"  - Name: {args.name}")
        print(f"  - Setting ID (in .info): {cloned_setting_id}")
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
