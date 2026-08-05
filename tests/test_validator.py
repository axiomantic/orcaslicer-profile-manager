#!/usr/bin/env python3
import unittest
from pathlib import Path
import json

from validate_orca import (
    OrcaValidator,
    ProfileDAGResolver,
    OrcaSchemaStore,
    get_orcaslicer_paths,
    search_installed_profiles,
    get_vendor_summary,
    generate_setting_id
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


if __name__ == "__main__":
    unittest.main()
