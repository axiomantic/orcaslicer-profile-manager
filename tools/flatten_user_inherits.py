#!/usr/bin/env python3
"""Repair user presets that inherit from other user presets.

OrcaSlicer silently DROPS any user preset whose "inherits" names another user preset.
The loader stages each directory pass in a local deque and only merges it into the preset
collection afterwards, while parent lookup searches the already-merged collection — so a
user preset can never see a sibling user preset as its parent, at any load order. It logs
"can not find parent %1% for config %2%!" to the debug log and moves on; nothing surfaces
in the UI. (Mechanism: PresetCollection::load_presets, src/libslic3r/Preset.cpp. See the
FLATTEN_EXCLUDED_KEYS comment in validate_orca.py.)

This script rewrites those presets in place: "inherits" is repointed at the nearest SYSTEM
ancestor, and every setting the skipped intermediate user presets declared is inlined into
the file. That is lossless, because OrcaSlicer only ever uses the parent as a STARTING
config before laying the child's own keys on top.

Nothing is written unless --apply is passed; the default is a dry run.

Usage:
  tools/flatten_user_inherits.py <preset-dir> [<preset-dir> ...]            # dry run
  tools/flatten_user_inherits.py <preset-dir> --apply                       # rewrite
"""

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from validate_orca import (  # noqa: E402
    Colors,
    ProfileDAGResolver,
    colorize,
    find_nearest_system_ancestor,
    get_orcaslicer_paths,
    is_user_preset,
    is_orcaslicer_running,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flatten_user_inherits.py",
        description="Repoint user presets that inherit from other user presets at their "
                    "nearest system ancestor, inlining the intermediate values.",
    )
    parser.add_argument("dirs", nargs="+", help="Directories of user presets to repair.")
    parser.add_argument(
        "--apply", action="store_true",
        help="Write the changes. Without it the script only reports what it would do.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Explicitly request the default behaviour: report without writing.",
    )
    parser.add_argument(
        "--ignore-running", action="store_true",
        help="Write even though OrcaSlicer appears to be running (it will discard the edits).",
    )
    return parser


def collect_presets(dirs: List[Path]) -> List[Path]:
    """Every .json file under the given directories, deduplicated and ordered."""
    found = set()
    for d in dirs:
        if not d.exists():
            print(f"Warning: skipping '{d}' — not found.", file=sys.stderr)
            continue
        found.update(p for p in d.glob("**/*.json") if p.is_file())
    return sorted(found)


def plan_repairs(paths: List[Path], resolver: ProfileDAGResolver) -> List[Dict[str, Any]]:
    """Returns one entry per preset that needs repairing. A preset needs repairing when it
    is a user preset AND its declared parent is also a user preset in the same index."""
    repairs = []
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue                                   # .info files, junk, unreadable — skip
        if not is_user_preset(data):
            continue
        inherits = data.get("inherits")
        parent = resolver.name_index.get(inherits) if inherits else None
        if parent is None or not is_user_preset(parent):
            continue

        ancestor, overrides = find_nearest_system_ancestor(resolver, data)
        if not ancestor:
            print(colorize(
                f"Cannot repair '{data.get('name', path.stem)}': its chain reaches no system "
                f"preset. Check that the vendor bundle is installed.", Colors.WARNING), file=sys.stderr)
            continue

        # The preset's own settings are already in the file; only the keys contributed by
        # the *skipped* intermediates actually get added by the rewrite.
        inlined = {k: v for k, v in overrides.items() if k not in data or data[k] != v}
        repairs.append({
            "path": path,
            "data": data,
            "name": data.get("name", path.stem),
            "old_inherits": inherits,
            "new_inherits": ancestor,
            "overrides": overrides,
            "inlined_keys": sorted(inlined),
        })
    return repairs


def apply_repair(repair: Dict[str, Any]) -> None:
    """Rewrites one preset in place, leaving a .bak of the original beside it."""
    path: Path = repair["path"]
    shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))

    data = dict(repair["data"])
    data.update(repair["overrides"])
    data["inherits"] = repair["new_inherits"]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def main() -> int:
    args = build_parser().parse_args()
    write = args.apply and not args.dry_run
    if args.apply and args.dry_run:
        print(colorize("--dry-run overrides --apply; nothing will be written.", Colors.WARNING), file=sys.stderr)

    # OrcaSlicer rewrites its entire preset state on exit, discarding anything changed
    # behind its back. A "cannot determine" answer must never block the run.
    if write and is_orcaslicer_running():
        if args.ignore_running:
            print(colorize("OrcaSlicer appears to be running; writing anyway (--ignore-running).",
                           Colors.WARNING), file=sys.stderr)
        else:
            print(colorize("Aborted: OrcaSlicer is running.", Colors.FAIL), file=sys.stderr)
            print("  It rewrites its preset state when it exits and will discard these edits.", file=sys.stderr)
            print("  Quit OrcaSlicer, then re-run. Pass --ignore-running to write anyway.", file=sys.stderr)
            return 1

    dirs = [Path(d).resolve() for d in args.dirs]

    # The system ancestor almost never lives in the user directory, so the installed
    # vendor bundles have to be indexed too or every chain would look unresolvable.
    resolver = ProfileDAGResolver()
    paths_info = get_orcaslicer_paths()
    for d in paths_info["builtin_existing"] + paths_info["user_existing"] + dirs:
        resolver.scan_directory(d)

    presets = collect_presets(dirs)
    repairs = plan_repairs(presets, resolver)

    print(colorize(f"user-from-user inheritance repair ({'APPLY' if write else 'DRY RUN'})", Colors.HEADER))
    print("=" * 60)
    print(f"Scanned {len(presets)} JSON file(s); {len(repairs)} preset(s) need repair.")
    print("=" * 60)

    for repair in repairs:
        print(f"{colorize(repair['name'], Colors.BOLD)}")
        print(f"  File          : {repair['path']}")
        print(f"  inherits      : '{repair['old_inherits']}' (user preset)"
              f" -> '{colorize(repair['new_inherits'], Colors.OKGREEN)}' (system preset)")
        if repair["inlined_keys"]:
            print(f"  Keys inlined  : {len(repair['inlined_keys'])}")
            for key in repair["inlined_keys"]:
                print(f"    - {key} = {json.dumps(repair['overrides'][key])}")
        else:
            print("  Keys inlined  : 0 (the intermediate declared no settings of its own)")
        if write:
            apply_repair(repair)
            print(f"  {colorize('WRITTEN', Colors.OKGREEN)} (original saved as {repair['path'].name}.bak)")
        print()

    print("=" * 60)
    if not repairs:
        print(colorize("Nothing to repair.", Colors.OKGREEN))
    elif write:
        print(colorize(f"Repaired {len(repairs)} preset(s).", Colors.OKGREEN))
    else:
        print(colorize(f"{len(repairs)} preset(s) would be repaired. Re-run with --apply to write.",
                       Colors.WARNING))
    return 0


if __name__ == "__main__":
    sys.exit(main())
