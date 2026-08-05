#!/usr/bin/env python3
import unittest
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock
import json

from validate_orca import (
    is_orcaslicer_running,
    OrcaValidator,
    ProfileDAGResolver,
    OrcaSchemaStore,
    get_orcaslicer_paths,
    search_installed_profiles,
    get_vendor_summary,
    generate_setting_id,
    lint_user_preset,
    lint_unknown_keys,
)


class TestOrcaValidator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root_dir = Path(__file__).resolve().parent.parent
        cls.schema_dir = cls.root_dir / "schemas"
        cls.examples_dir = cls.root_dir / "examples"
        cls.validator = OrcaValidator(cls.schema_dir, inherit_dirs=[cls.examples_dir])

    def test_vendor_validation(self):
        res = self.validator.validate_file(self.examples_dir / "BBL_vendor.json", domain="vendor")
        self.assertTrue(res["valid"], f"Vendor validation failed: {res['errors']}")

    def test_machine_validation(self):
        res = self.validator.validate_file(self.examples_dir / "voron_machine.json", domain="machine")
        self.assertTrue(res["valid"], f"Machine validation failed: {res['errors']}")

    def test_filament_validation(self):
        res = self.validator.validate_file(self.examples_dir / "pla_filament.json", domain="filament")
        self.assertTrue(res["valid"], f"Filament validation failed: {res['errors']}")

    def test_process_base_validation(self):
        res = self.validator.validate_file(self.examples_dir / "fdm_process_common.json", domain="process")
        self.assertTrue(res["valid"], f"Process base validation failed: {res['errors']}")

    def test_process_inherited_dag_validation(self):
        res = self.validator.validate_file(self.examples_dir / "process_020_voron.json", domain="process", resolve_inherits=True)
        self.assertTrue(res["valid"], f"Inherited process validation failed: {res['errors']}")

    def test_material_db_validation(self):
        res = self.validator.validate_file(self.examples_dir / "material_db.json", domain="material-db")
        self.assertTrue(res["valid"], f"Material DB validation failed: {res['errors']}")

    def test_auto_detection(self):
        for f in ["BBL_vendor.json", "voron_machine.json", "pla_filament.json", "fdm_process_common.json", "material_db.json"]:
            res = self.validator.validate_file(self.examples_dir / f, domain="auto")
            self.assertTrue(res["valid"], f"Auto detection failed for {f}: {res['errors']}")

    def test_invalid_filament_fails(self):
        res = self.validator.validate_file(self.examples_dir / "invalid_filament.json", domain="filament")
        self.assertFalse(res["valid"])
        self.assertGreater(len(res["errors"]), 0)

    def test_path_discovery(self):
        paths = get_orcaslicer_paths()
        self.assertIn("builtin_candidates", paths)
        self.assertIn("user_candidates", paths)

    def test_search_installed_profiles(self):
        results = search_installed_profiles([self.examples_dir], domain="filament")
        self.assertGreater(len(results), 0)

    def test_vendor_summary(self):
        vendors = get_vendor_summary([self.examples_dir])
        self.assertGreater(len(vendors), 0)
        self.assertEqual(vendors[0]["name"], "BBL")

    def test_generate_setting_id(self):
        sid = generate_setting_id()
        self.assertEqual(len(sid), 16)
        self.assertTrue(sid.isalnum())

    # --- Regression coverage for the OrcaSlicer user-preset format bug ---
    # System bundle profiles carry "type"/"setting_id"/"compatible_printers" and
    # OrcaSlicer's preset loader silently rejects USER presets that carry them
    # (undocumented; see https://github.com/OrcaSlicer/OrcaSlicer/issues/12223).
    # A clone that regresses to copying these fields into a user preset will not
    # error, will not crash, and will not show up in OrcaSlicer either — it fails
    # silently. These tests exist so that failure mode is caught here instead.

    def test_lint_user_preset_flags_system_only_fields(self):
        bad = {
            "type": "process",
            "name": "Bad Clone",
            "inherits": "0.20mm Standard @Voron",
            "setting_id": "AAAAAAAAAAAAAAAA",
            "compatible_printers": ["Voron 2.4 350 0.4 nozzle"],
            "from": "User",
            "print_settings_id": "Bad Clone",
        }
        violations = lint_user_preset(bad, "process")
        self.assertTrue(any("type" in v for v in violations))
        self.assertTrue(any("setting_id" in v for v in violations))

    def test_lint_user_preset_flags_missing_inherits(self):
        standalone = {"name": "Standalone", "from": "User", "print_settings_id": "Standalone"}
        violations = lint_user_preset(standalone, "process")
        self.assertTrue(any("inherits" in v for v in violations))

    def test_lint_user_preset_accepts_clean_diff(self):
        clean = {
            "inherits": "0.20mm Standard @Voron",
            "name": "My Clone",
            "from": "User",
            "print_settings_id": "My Clone",
            "version": "2.1.0.19",
            "outer_wall_speed": "180",
        }
        self.assertEqual(lint_user_preset(clean, "process"), [])

    def _clone_proc(self, extra_args, out_path):
        cmd = [
            sys.executable, str(self.root_dir / "validate_orca.py"), "clone", "process",
            str(self.examples_dir / "process_020_voron.json"),
            "--name", "My Cloned Process",
            "--profiles-dir", str(self.examples_dir),
            "--out", str(out_path),
        ] + extra_args
        return subprocess.run(cmd, capture_output=True, text=True)

    def _run_clone(self, extra_args):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "cloned.json"
            proc = self._clone_proc(extra_args, out_path)
            self.assertEqual(proc.returncode, 0, f"clone failed: {proc.stderr}")
            return json.loads(out_path.read_text())

    def test_clone_produces_valid_user_preset(self):
        data = self._run_clone(["--set", 'outer_wall_speed="190"'])
        self.assertEqual(lint_user_preset(data, "process"), [])
        self.assertEqual(data["inherits"], "0.20mm Standard @Voron")
        self.assertNotIn("type", data)
        self.assertNotIn("setting_id", data)
        self.assertNotIn("compatible_printers", data)
        self.assertEqual(data["print_settings_id"], "My Cloned Process")
        # A user preset missing "version" is silently skipped by OrcaSlicer's
        # loader (confirmed against a real install) even though it's otherwise
        # a perfectly valid diff — regression coverage for that specific field.
        self.assertTrue(data.get("version"))

    def test_delinked_clone_produces_valid_user_preset(self):
        data = self._run_clone(["--de-link-inherits", "--set", 'outer_wall_speed="190"'])
        violations = lint_user_preset(data, "process")
        self.assertEqual(violations, [])
        # De-linking must still leave a valid, resolvable "inherits" target —
        # OrcaSlicer rejects a standalone preset with no inherits at all.
        self.assertTrue(data["inherits"])
        self.assertNotIn("type", data)
        self.assertNotIn("setting_id", data)
        # Flattening pulled every ancestor key into the child explicitly.
        self.assertIn("layer_height", data)

    # --- Regression coverage: user presets invisible as clone sources ---
    # Valid USER presets deliberately carry no "type" key (required by
    # OrcaSlicer's user-preset format — see SKILL.md § "User Presets vs
    # System Presets"). search_installed_profiles() must still be able to
    # infer the domain for such presets, or they silently vanish as clone
    # sources.

    def test_search_finds_user_preset_via_settings_id_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            preset_path = Path(tmp) / "user_process.json"
            preset_path.write_text(json.dumps({
                "name": "My User Process",
                "from": "User",
                "inherits": "0.20mm Standard @Voron",
                "print_settings_id": "My User Process",
            }))
            results = search_installed_profiles([Path(tmp)], domain="process")
            self.assertTrue(any(r["name"] == "My User Process" for r in results))

    def test_search_finds_user_preset_via_directory_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            process_dir = Path(tmp) / "process"
            process_dir.mkdir()
            preset_path = process_dir / "user_process.json"
            # No "print_settings_id" key either, so only the containing
            # directory name ("process") can identify the domain.
            preset_path.write_text(json.dumps({
                "name": "My Dirname Process",
                "from": "User",
                "inherits": "0.20mm Standard @Voron",
            }))
            results = search_installed_profiles([Path(tmp)], domain="process")
            self.assertTrue(any(r["name"] == "My Dirname Process" for r in results))

    # --- Regression coverage: setting_id pattern rejects real values ---
    # Surveyed 9821 bundled profiles carrying a setting_id: real values use
    # alphanumeric plus underscore/hyphen (e.g. "GFSG96_00", "BFLSBS99-1").
    # The pattern must accept those and still reject clearly invalid ids.

    def _validate_filament_with_setting_id(self, setting_id):
        data = json.loads((self.examples_dir / "pla_filament.json").read_text())
        data["setting_id"] = setting_id
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp) / "filament.json"
            tmp_path.write_text(json.dumps(data))
            return self.validator.validate_file(tmp_path, domain="filament")

    def test_setting_id_with_underscore_is_valid(self):
        res = self._validate_filament_with_setting_id("GFSG96_00")
        self.assertTrue(res["valid"], f"Underscore setting_id rejected: {res['errors']}")

    def test_setting_id_with_invalid_chars_still_rejected(self):
        res = self._validate_filament_with_setting_id("GFSG96 00@")
        self.assertFalse(res["valid"])

    # --- Regression coverage: keys OrcaSlicer silently ignores ---
    # OrcaSlicer's preset loader drops any key it does not recognise for the
    # preset's domain. A typo'd setting name, or a real setting written into the
    # wrong domain (a process key in a filament preset is the common one), raises
    # no error, loads fine, and simply does nothing — the operator sees a preset
    # that "applied" and a print that ignored it. schemas/known_keys.json holds the
    # real per-domain option names lifted from the OrcaSlicer binary (see
    # tools/extract_known_keys.py); these tests keep that check honest.

    def test_lint_unknown_keys_flags_typo(self):
        violations = lint_unknown_keys({"layer_hieght": "0.2"}, "process")
        self.assertEqual(len(violations), 1)
        self.assertIn("layer_hieght", violations[0])
        self.assertIn("not a known process setting", violations[0])

    def test_lint_unknown_keys_names_the_owning_domain(self):
        # "layer_height" is real, but it is a process setting; setting it on a
        # filament preset is a no-op, and the message has to say why.
        violations = lint_unknown_keys({"layer_height": "0.2"}, "filament")
        self.assertEqual(len(violations), 1)
        self.assertIn("layer_height", violations[0])
        self.assertIn("process setting", violations[0])
        self.assertIn("filament preset", violations[0])

    def test_lint_unknown_keys_accepts_own_domain_and_metadata(self):
        clean = {
            "name": "My Custom PLA",
            "inherits": "Bambu PLA Basic @BBL X1C",
            "from": "User",
            "version": "2.1.0.19",
            "filament_settings_id": "My Custom PLA",
            "compatible_printers": ["Bambu Lab X1 Carbon 0.4 nozzle"],
            "nozzle_temperature": ["225"],
            "hot_plate_temp": ["60"],
        }
        self.assertEqual(lint_unknown_keys(clean, "filament"), [])

    def test_clone_aborts_on_unknown_set_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "cloned.json"
            proc = self._clone_proc(["--set", 'outer_wall_sped="190"'], out_path)
            self.assertNotEqual(proc.returncode, 0, "clone accepted a bogus --set key")
            self.assertIn("outer_wall_sped", proc.stderr)
            self.assertFalse(out_path.exists(), "aborted clone still wrote the preset")

    def test_clone_allow_unknown_keys_escape_hatch(self):
        data = self._run_clone(["--allow-unknown-keys", "--set", 'outer_wall_sped="190"'])
        self.assertEqual(data["outer_wall_sped"], "190")

    # --- Regression coverage: the three silent preflight failures ---
    # None of these produce an error from OrcaSlicer; in every case the preset file is
    # written, is schema-valid, and simply never appears in the UI:
    #   1. A preset written while OrcaSlicer is running is discarded when it exits.
    #   2. A preset whose "inherits" target does not exist is dropped by the loader.
    #      Two profiles were lost this way to a parent name that was never installed.
    #   3. A preset bound via compatible_printers to a printer its parent chain does
    #      not accept is invisible for that printer.
    # is_orcaslicer_running() is only tested for its graceful-degradation contract:
    # asserting on a live process would make the suite depend on what the machine
    # happens to be running.

    def test_is_orcaslicer_running_degrades_when_probe_missing(self):
        with mock.patch("validate_orca.subprocess.run", side_effect=FileNotFoundError("pgrep")):
            self.assertIsNone(is_orcaslicer_running())

    def test_is_orcaslicer_running_degrades_on_probe_error(self):
        # pgrep exits >1 on a real failure (as opposed to 1 for "no match"), which is
        # "cannot determine" — never an assertion that OrcaSlicer is not running.
        with mock.patch("validate_orca.subprocess.run", return_value=subprocess.CompletedProcess([], 2, "", "")):
            self.assertIsNone(is_orcaslicer_running())

    def test_is_orcaslicer_running_ignores_own_process_tree(self):
        # "pgrep -f" matches full command lines, so this tool matches itself whenever it
        # runs from a path containing the app name. Only our own pids came back here.
        own = f"{os.getpid()}\n{os.getppid()}\n"
        with mock.patch("validate_orca.subprocess.run", return_value=subprocess.CompletedProcess([], 0, own, "")):
            self.assertIs(is_orcaslicer_running(), False)

    def test_is_orcaslicer_running_detects_foreign_pid(self):
        with mock.patch("validate_orca.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "1\n", "")):
            self.assertIs(is_orcaslicer_running(), True)

    def test_clone_aborts_on_nonexistent_inherits(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "cloned.json"
            proc = self._clone_proc(["--inherits", "Generic PETG @Nonexistent Printer"], out_path)
            self.assertNotEqual(proc.returncode, 0, "clone accepted an unresolvable --inherits")
            self.assertIn("Generic PETG @Nonexistent Printer", proc.stderr)
            self.assertFalse(out_path.exists(), "aborted clone still wrote the preset")

    def test_clone_accepts_existing_inherits(self):
        data = self._run_clone(["--inherits", "fdm_process_common"])
        self.assertEqual(data["inherits"], "fdm_process_common")

    def _write_parent_fixture(self, tmp_dir, name, compatible_printers=None):
        """Writes a self-contained process profile into tmp_dir and returns its path.
        Self-contained (the example's own parent chain is merged in first) so the clone
        under test resolves entirely within tmp_dir, and uniquely named so it can never
        collide with a same-named profile in a real OrcaSlicer install on this machine."""
        base = json.loads((self.examples_dir / "fdm_process_common.json").read_text())
        child = json.loads((self.examples_dir / "process_020_voron.json").read_text())
        merged = dict(base)
        merged.update(child)
        merged.pop("inherits", None)
        merged.pop("compatible_printers", None)
        merged["name"] = name
        if compatible_printers is not None:
            merged["compatible_printers"] = compatible_printers
        parent_path = Path(tmp_dir) / f"{name}.json"
        parent_path.write_text(json.dumps(merged))
        return parent_path

    def _clone_from_parent(self, tmp_dir, parent_path, extra_args):
        cmd = [
            sys.executable, str(self.root_dir / "validate_orca.py"), "clone", "process",
            str(parent_path),
            "--name", "Compat Bound Clone",
            "--out", str(Path(tmp_dir) / "cloned.json"),
        ] + extra_args
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_clone_aborts_on_printer_parent_does_not_support(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = self._write_parent_fixture(tmp, "ZZ Compat Parent @Unittest", ["ZZ Test Printer 0.4 nozzle"])
            proc = self._clone_from_parent(tmp, parent, ["--compatible-printers", "ZZ Other Printer 0.8 nozzle"])
            self.assertNotEqual(proc.returncode, 0, "clone accepted an unsupported printer binding")
            self.assertIn("ZZ Other Printer 0.8 nozzle", proc.stderr)
            self.assertIn("ZZ Compat Parent @Unittest", proc.stderr)
            self.assertIn("ZZ Test Printer 0.4 nozzle", proc.stderr)
            self.assertFalse((Path(tmp) / "cloned.json").exists(), "aborted clone still wrote the preset")

    def test_clone_skip_compat_check_escape_hatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = self._write_parent_fixture(tmp, "ZZ Compat Parent @Unittest", ["ZZ Test Printer 0.4 nozzle"])
            proc = self._clone_from_parent(
                tmp, parent, ["--skip-compat-check", "--compatible-printers", "ZZ Other Printer 0.8 nozzle"]
            )
            self.assertEqual(proc.returncode, 0, f"clone failed: {proc.stderr}")
            data = json.loads((Path(tmp) / "cloned.json").read_text())
            self.assertEqual(data["compatible_printers"], ["ZZ Other Printer 0.8 nozzle"])

    def test_clone_accepts_printer_parent_supports(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = self._write_parent_fixture(tmp, "ZZ Compat Parent @Unittest", ["ZZ Test Printer 0.4 nozzle"])
            proc = self._clone_from_parent(tmp, parent, ["--compatible-printers", "ZZ Test Printer 0.4 nozzle"])
            self.assertEqual(proc.returncode, 0, f"clone failed: {proc.stderr}")
            data = json.loads((Path(tmp) / "cloned.json").read_text())
            self.assertEqual(data["compatible_printers"], ["ZZ Test Printer 0.4 nozzle"])

    def test_clone_accepts_any_printer_for_abstract_parent(self):
        # A parent chain declaring no compatible_printers at all (an "@base"-style
        # abstract profile) is universal; binding a child to any printer is legitimate.
        with tempfile.TemporaryDirectory() as tmp:
            parent = self._write_parent_fixture(tmp, "ZZ Abstract Parent @Unittest")
            proc = self._clone_from_parent(tmp, parent, ["--compatible-printers", "ZZ Other Printer 0.8 nozzle"])
            self.assertEqual(proc.returncode, 0, f"clone failed: {proc.stderr}")

    def _validate_user_preset_with_inherits(self, inherits):
        with tempfile.TemporaryDirectory() as tmp:
            preset_path = Path(tmp) / "preset.json"
            preset_path.write_text(json.dumps({
                "name": "Inherits Probe",
                "from": "User",
                "version": "2.1.0.19",
                "inherits": inherits,
                "print_settings_id": "Inherits Probe",
            }))
            return self.validator.validate_file(preset_path, domain="auto")

    def test_validate_file_warns_on_unresolved_inherits(self):
        res = self._validate_user_preset_with_inherits("0.20mm Standard @Hallucinated")
        self.assertTrue(
            any("[unresolved inherits]" in w for w in res["warnings"]),
            f"no unresolved-inherits warning: {res['warnings']}",
        )

    def test_validate_file_accepts_resolvable_inherits(self):
        res = self._validate_user_preset_with_inherits("0.20mm Standard @Voron")
        self.assertFalse(
            any("[unresolved inherits]" in w for w in res["warnings"]),
            f"false positive on a resolvable parent: {res['warnings']}",
        )


if __name__ == "__main__":
    unittest.main()
