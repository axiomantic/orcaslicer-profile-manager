#!/usr/bin/env python3
import unittest
import subprocess
import sys
import tempfile
from pathlib import Path
import json

from validate_orca import (
    OrcaValidator,
    ProfileDAGResolver,
    OrcaSchemaStore,
    get_orcaslicer_paths,
    search_installed_profiles,
    get_vendor_summary,
    generate_setting_id,
    lint_user_preset,
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
            "outer_wall_speed": "180",
        }
        self.assertEqual(lint_user_preset(clean, "process"), [])

    def _run_clone(self, extra_args):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "cloned.json"
            cmd = [
                sys.executable, str(self.root_dir / "validate_orca.py"), "clone", "process",
                str(self.examples_dir / "process_020_voron.json"),
                "--name", "My Cloned Process",
                "--profiles-dir", str(self.examples_dir),
                "--out", str(out_path),
            ] + extra_args
            proc = subprocess.run(cmd, capture_output=True, text=True)
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


if __name__ == "__main__":
    unittest.main()
