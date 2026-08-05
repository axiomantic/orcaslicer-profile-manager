#!/usr/bin/env python3
"""Extract the authoritative OrcaSlicer option-name tables from the app binary.

OrcaSlicer silently ignores unknown keys in a preset, so a typo'd or
wrong-domain setting produces no error and simply does nothing. To catch that,
we need the real list of option names that each preset domain accepts.

Those lists live in C++ as ``Preset::print_options()``,
``Preset::filament_options()`` and ``Preset::printer_options()`` -- static
tables of string literals. The compiler emits each table as a contiguous run of
NUL-terminated strings in the binary's cstring section, so ``strings`` recovers
them in source order.

Two complications:

1. The literals are deduplicated. A name already emitted elsewhere in the
   binary is not repeated inside the table run, so any single run is
   incomplete. macOS ships OrcaSlicer as a fat (multi-architecture) binary and
   each architecture slice dedupes differently, so the *union* of the runs
   across slices recovers substantially more names than either slice alone.
   Even the union is short: ``Preset::printer_options()`` appends the
   per-nozzle option keys at runtime rather than listing them as literals, so
   names like ``retraction_length`` and ``nozzle_diameter`` are in no table run
   at all.
2. Absolute offsets drift between OrcaSlicer versions, so the runs are located
   by landmark tokens rather than by hardcoded line numbers.

Landmarks used (all stable across the tables' source order):

* ``default_filament_colour`` -- first entry of ``filament_options()``. The
  print table is the token run immediately before it.
* the first ``machine_max_acceleration_*`` entry after the filament table --
  first entry of ``printer_options()``.
* ``faded_layers`` -- first entry of ``sla_print_options()``, which follows the
  printer table and marks its end.

To close the dedup gap the table runs are augmented from OrcaSlicer's own
bundled system profiles, which are domain-partitioned by directory
(``profiles/<vendor>/{process,filament,machine}/``). A key seen in a bundled
profile is admitted for that profile's domain only if the name still exists as
a standalone string literal somewhere in the binary. That second condition is
what keeps dead keys out: the bundles carry a long tail of settings OrcaSlicer
has since removed or renamed (``keep_fan_always_on``, ``epoxy_resin_plate_temp``)
and outright typos (``tree_support_bramch_diameter_angle``,
``nozzle_temperature_intial_layer``), and none of those appear in the binary.

Usage::

    python3 tools/extract_known_keys.py [BINARY] [-o schemas/known_keys.json]
"""

import argparse
import json
import plistlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

DEFAULT_BINARY = "/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer"

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = REPO_ROOT / "schemas" / "known_keys.json"

# An option name as it appears in the C++ tables. Deliberately permissive about
# case because a few real names are mixed case (e.g. required_nozzle_HRC).
TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

FILAMENT_START = "default_filament_colour"
PRINTER_START_PREFIX = "machine_max_acceleration_"
PRINTER_END = "faded_layers"

# Keys that are preset bookkeeping rather than slicer options. They are valid in
# any domain and are not present in the C++ option tables.
METADATA_KEYS = [
    "compatible_printers",
    "compatible_printers_condition",
    "compatible_prints",
    "compatible_prints_condition",
    "description",
    "filament_id",
    "filament_settings_id",
    "from",
    "inherits",
    "instantiation",
    "is_custom_defined",
    "name",
    "print_settings_id",
    "printer_model",
    "printer_settings_id",
    "printer_variant",
    "renamed_from",
    "setting_id",
    "sub_path",
    "type",
    "update_time",
    "user_id",
    "version",
]


def run_strings(binary: Path, min_length: int = 6) -> List[str]:
    result = subprocess.run(
        ["strings", "-n", str(min_length), str(binary)],
        check=True,
        capture_output=True,
        text=True,
        errors="replace",
    )
    return result.stdout.splitlines()


def bundled_profile_keys(profiles_dir: Path) -> Dict[str, set]:
    """Map domain -> keys observed in OrcaSlicer's bundled system profiles."""
    found: Dict[str, set] = {"process": set(), "filament": set(), "machine": set()}
    if not profiles_dir.is_dir():
        return found
    for path in profiles_dir.rglob("*.json"):
        parts = path.relative_to(profiles_dir).parts
        domain = next((p for p in parts if p in found), None)
        if domain is None:
            continue
        try:
            with path.open() as handle:
                data = json.load(handle)
        except Exception:
            continue
        if isinstance(data, dict):
            found[domain].update(data.keys())
    return found


def detect_version(binary: Path) -> Optional[str]:
    plist = binary.parent.parent / "Info.plist"
    try:
        with plist.open("rb") as handle:
            data = plistlib.load(handle)
    except Exception:
        return None
    value = data.get("CFBundleShortVersionString") or data.get("CFBundleVersion")
    return str(value) if value else None


def _walk_back(lines: Sequence[str], stop_before: int) -> int:
    """Return the first index of the token run that ends at ``stop_before``."""
    index = stop_before
    while index > 0 and TOKEN_RE.match(lines[index - 1]):
        index -= 1
    return index


def extract_tables(lines: Sequence[str]) -> Dict[str, List[str]]:
    anchors = [i for i, line in enumerate(lines) if line == FILAMENT_START]
    if not anchors:
        raise SystemExit(
            f"landmark {FILAMENT_START!r} not found; the binary layout changed "
            "and this extractor needs updating"
        )

    tables: Dict[str, set] = {"process": set(), "filament": set(), "machine": set()}

    for anchor in anchors:
        printer_start = next(
            (
                i
                for i in range(anchor + 1, len(lines))
                if lines[i].startswith(PRINTER_START_PREFIX)
            ),
            None,
        )
        if printer_start is None:
            raise SystemExit(
                f"no {PRINTER_START_PREFIX}* landmark after the filament table"
            )
        printer_end = next(
            (i for i in range(printer_start + 1, len(lines)) if lines[i] == PRINTER_END),
            None,
        )
        if printer_end is None:
            raise SystemExit(f"landmark {PRINTER_END!r} not found after printer table")

        print_start = _walk_back(lines, anchor)

        tables["process"].update(lines[print_start:anchor])
        tables["filament"].update(lines[anchor:printer_start])
        tables["machine"].update(lines[printer_start:printer_end])

    # A handful of names legitimately appear in more than one table run because
    # of how the slices dedupe. Keep them everywhere they were found; the
    # validator treats membership per domain.
    return {domain: sorted(keys) for domain, keys in tables.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", nargs="?", default=DEFAULT_BINARY)
    parser.add_argument("-o", "--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--profiles",
        default=None,
        help="bundled system profiles directory (default: alongside the binary)",
    )
    args = parser.parse_args()

    binary = Path(args.binary)
    if not binary.exists():
        print(f"error: binary not found: {binary}", file=sys.stderr)
        return 1

    profiles_dir = (
        Path(args.profiles)
        if args.profiles
        else binary.parent.parent / "Resources" / "profiles"
    )

    lines = run_strings(binary)
    tables = extract_tables(lines)

    # Short names (z_hop, wipe, url) need a lower threshold than the table scan.
    literals = set(run_strings(binary, min_length=3))
    metadata = set(METADATA_KEYS)
    from_profiles = bundled_profile_keys(profiles_dir)
    added = {domain: 0 for domain in tables}
    for domain, keys in from_profiles.items():
        for key in keys:
            if key in metadata or key in tables[domain] or key not in literals:
                continue
            tables[domain].append(key)
            added[domain] += 1
    tables = {domain: sorted(keys) for domain, keys in tables.items()}

    payload = {
        "_source": {
            "binary": str(binary),
            "orcaslicer_version": detect_version(binary),
            "bundled_profiles": str(profiles_dir),
            "extracted_by": "tools/extract_known_keys.py",
        },
        "metadata": METADATA_KEYS,
        "process": tables["process"],
        "filament": tables["filament"],
        "machine": tables["machine"],
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")

    print(f"wrote {output}")
    print(f"  orcaslicer_version: {payload['_source']['orcaslicer_version']}")
    for domain in ("process", "filament", "machine"):
        print(
            f"  {domain:9s} {len(tables[domain]):5d} keys "
            f"({added[domain]} from bundled profiles)"
        )
    print(f"  metadata  {len(METADATA_KEYS):5d} keys")
    return 0


if __name__ == "__main__":
    sys.exit(main())
