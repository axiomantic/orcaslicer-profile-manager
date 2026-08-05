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
    is_user_preset,
    find_nearest_system_ancestor,
    parse_orca_log,
    run_doctor,
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
            # Every clone here writes to a throwaway temp path, never into a real preset
            # directory, so OrcaSlicer cannot clobber it on exit. Without this the whole
            # clone half of the suite fails purely because the operator happens to have
            # OrcaSlicer open.
            "--ignore-running",
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

    # --- Regression coverage: schema enums must accept every shipped value ---
    # An enum that is missing a legitimate value fails a preset OrcaSlicer accepts
    # happily, which reads as "the tool says my working preset is broken" and is
    # worse than no check at all. support_interface_pattern lost this way:
    # "rectilinear_interlaced" ships in 8 bundled profiles and lives in the binary's
    # enum pool, but the schema listed only 4 of the 6 values.

    def test_support_interface_pattern_accepts_all_shipped_values(self):
        for value in ("auto", "rectilinear", "rectilinear_interlaced", "concentric", "grid", "default"):
            with self.subTest(pattern=value):
                res = self.validator.validate_file(
                    self._write_tmp_process({"support_interface_pattern": value}), domain="process"
                )
                self.assertTrue(res["valid"], f"{value} rejected: {res['errors']}")

    def test_support_interface_pattern_rejects_bogus_value(self):
        res = self.validator.validate_file(
            self._write_tmp_process({"support_interface_pattern": "zigzag"}), domain="process"
        )
        self.assertFalse(res["valid"])

    def _write_tmp_process(self, extra):
        """Writes a minimal valid process profile plus `extra` to a temp file."""
        data = {
            "type": "process",
            "name": "ZZ Enum Probe @Unittest",
            "layer_height": "0.2",
            "wall_generator": "classic",
            "sparse_infill_density": "15%",
        }
        data.update(extra)
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(data, tmp)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return Path(tmp.name)

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

    # --- Regression coverage: the manual-steps notice ---
    # A multi-material / support-interface setup needs two settings that no preset can
    # carry: the flushing volumes (they live in OrcaSlicer.conf, per printer, sized by
    # the whole filament set on the plate) and the support-interface AMS slot index
    # (it depends on the operator's physical slot layout). A generated preset set
    # therefore validates clean and still does not print correctly. The clone command
    # prints the checklist at creation time so the operator learns this even when the
    # agent never read the recipe. The notice is informational: exit code stays 0.

    def _clone_filament_proc(self, extra_args, out_path):
        cmd = [
            sys.executable, str(self.root_dir / "validate_orca.py"), "clone", "filament",
            str(self.examples_dir / "pla_filament.json"),
            "--name", "My Cloned Filament",
            "--profiles-dir", str(self.examples_dir),
            "--out", str(out_path),
            "--ignore-running",
        ] + extra_args
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_clone_marking_filament_as_support_prints_manual_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "cloned.json"
            proc = self._clone_filament_proc(
                ["--set", 'filament_is_support=["1"]'], out_path)
            self.assertEqual(proc.returncode, 0, f"clone failed: {proc.stderr}")
            self.assertIn("REQUIRED MANUAL STEPS", proc.stdout)
            self.assertIn("Flushing volumes", proc.stdout)
            self.assertIn("Support interface filament", proc.stdout)
            self.assertIn("recipes.md", proc.stdout)

    def test_clone_of_plain_filament_does_not_print_manual_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "cloned.json"
            proc = self._clone_filament_proc(
                ["--set", 'nozzle_temperature=["225"]'], out_path)
            self.assertEqual(proc.returncode, 0, f"clone failed: {proc.stderr}")
            self.assertNotIn("REQUIRED MANUAL STEPS", proc.stdout)

    def test_clone_with_zero_support_gap_prints_manual_steps(self):
        # Zero gap between support and model only works because the two materials do
        # not bond, so it is the mutual-support signature even with no filament index
        # set anywhere in the preset.
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "cloned.json"
            proc = self._clone_proc(
                ["--set", 'support_top_z_distance="0"'], out_path)
            self.assertEqual(proc.returncode, 0, f"clone failed: {proc.stderr}")
            self.assertIn("REQUIRED MANUAL STEPS", proc.stdout)

    def test_clone_of_plain_process_does_not_print_manual_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "cloned.json"
            proc = self._clone_proc(["--set", 'outer_wall_speed="190"'], out_path)
            self.assertEqual(proc.returncode, 0, f"clone failed: {proc.stderr}")
            self.assertNotIn("REQUIRED MANUAL STEPS", proc.stdout)

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
            "--ignore-running",
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

    # --- Regression coverage: user presets cannot inherit from user presets ---
    # OrcaSlicer's loader (PresetCollection::load_presets, src/libslic3r/Preset.cpp) stages
    # each directory pass in a LOCAL deque and only merges it into the preset collection
    # after the loop ends, while parent lookup (find_preset2 -> find_preset_internal)
    # searches only the already-merged collection. A user preset therefore can never see a
    # sibling user preset as its parent, at any load order, and there is no second pass. On
    # failure the loader logs "can not find parent %1% for config %2%!" and `continue`s --
    # the preset is DROPPED with no error anywhere in the UI. This was confirmed live: four
    # of the operator's presets were missing and the debug log named all four.
    #
    # The repair is to point "inherits" at the nearest SYSTEM ancestor and inline whatever
    # the skipped user presets declared. That is faithful to how OrcaSlicer applies a
    # parent: load_preset does `preset.config = inherit_preset->config;` and then
    # update_diff_values_to_child_config() lays the child's own keys on top, so the parent
    # is only ever a starting config. These tests cover the chain walk, the clone wiring,
    # and the validator warning that catches an already-broken preset on disk.

    ZZ_SYSTEM = "ZZ Flatten System @Unittest"
    ZZ_USER_A = "ZZ Flatten UserA @Unittest"
    ZZ_USER_B = "ZZ Flatten UserB @Unittest"

    def _write_flatten_chain(self, tmp_dir):
        """Builds system <- userA <- userB in tmp_dir and returns (resolver, userB_path).

        userA and userB both declare "outer_wall_speed" so the merge order is observable,
        and userA alone declares "top_shell_layers" and "sparse_infill_density" so the
        intermediate's unique contribution is observable too. Names are "ZZ ..."-prefixed so
        they can never collide with a real profile in an OrcaSlicer install on this machine."""
        tmp_path = Path(tmp_dir)
        self._write_parent_fixture(tmp_dir, self.ZZ_SYSTEM)

        user_a = {
            "name": self.ZZ_USER_A,
            "from": "User",
            "version": "2.1.0.19",
            "inherits": self.ZZ_SYSTEM,
            "print_settings_id": self.ZZ_USER_A,
            "outer_wall_speed": "111",
            "top_shell_layers": "5",
            "sparse_infill_density": "22%",
        }
        user_b = {
            "name": self.ZZ_USER_B,
            "from": "User",
            "version": "2.1.0.19",
            "inherits": self.ZZ_USER_A,
            "print_settings_id": self.ZZ_USER_B,
            "outer_wall_speed": "222",
        }
        (tmp_path / "user_a.json").write_text(json.dumps(user_a))
        user_b_path = tmp_path / "user_b.json"
        user_b_path.write_text(json.dumps(user_b))

        resolver = ProfileDAGResolver()
        resolver.scan_directory(tmp_path)
        return resolver, user_b_path

    def test_find_nearest_system_ancestor_walks_a_user_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            resolver, user_b_path = self._write_flatten_chain(tmp)
            user_b = json.loads(user_b_path.read_text())

            ancestor, overrides = find_nearest_system_ancestor(resolver, user_b)

            self.assertEqual(ancestor, self.ZZ_SYSTEM)
            # userB is nearer the child than userA, so its value for the shared key wins.
            self.assertEqual(overrides["outer_wall_speed"], "222")
            # ...while userA's own keys still have to survive the flattening.
            self.assertEqual(overrides["top_shell_layers"], "5")
            self.assertEqual(overrides["sparse_infill_density"], "22%")
            # Identity/metadata describes the intermediate preset, not its settings.
            for meta_key in ("name", "inherits", "from", "version", "print_settings_id"):
                self.assertNotIn(meta_key, overrides)

    def test_find_nearest_system_ancestor_is_a_noop_for_system_presets(self):
        with tempfile.TemporaryDirectory() as tmp:
            resolver, _ = self._write_flatten_chain(tmp)
            system = resolver.name_index[self.ZZ_SYSTEM]

            ancestor, overrides = find_nearest_system_ancestor(resolver, system)

            self.assertFalse(is_user_preset(system))
            self.assertEqual(ancestor, self.ZZ_SYSTEM)
            self.assertEqual(overrides, {})

    def test_clone_from_user_preset_flattens_to_system_ancestor(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, user_b_path = self._write_flatten_chain(tmp)
            out_path = Path(tmp) / "cloned.json"
            proc = subprocess.run([
                sys.executable, str(self.root_dir / "validate_orca.py"), "clone", "process",
                str(user_b_path),
                "--name", "ZZ Flatten Clone @Unittest",
                "--out", str(out_path),
                "--ignore-running",
                "--set", 'sparse_infill_density="99%"',
            ], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, f"clone failed: {proc.stderr}")
            data = json.loads(out_path.read_text())

            # Never the user preset that was cloned -- OrcaSlicer would drop the result.
            self.assertEqual(data["inherits"], self.ZZ_SYSTEM)
            # The skipped intermediates' settings are re-declared in the child instead.
            self.assertEqual(data["outer_wall_speed"], "222")
            self.assertEqual(data["top_shell_layers"], "5")
            # ...but an explicit --set still outranks anything inlined from the chain.
            self.assertEqual(data["sparse_infill_density"], "99%")
            self.assertEqual(lint_user_preset(data, "process"), [])
            self.assertIn(self.ZZ_SYSTEM, proc.stdout)

    def _validate_flatten_chain_member(self, filename):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_flatten_chain(tmp)
            validator = OrcaValidator(self.schema_dir, inherit_dirs=[Path(tmp)])
            return validator.validate_file(Path(tmp) / filename, domain="process")

    def test_validate_file_warns_on_user_from_user_inherits(self):
        # user_b.json inherits userA, which is itself a user preset.
        res = self._validate_flatten_chain_member("user_b.json")
        matches = [w for w in res["warnings"] if "[user-from-user inherits]" in w]
        self.assertTrue(matches, f"no user-from-user warning: {res['warnings']}")
        self.assertIn(self.ZZ_USER_A, matches[0])
        # The warning is only actionable if it names the parent to switch to.
        self.assertIn(self.ZZ_SYSTEM, matches[0])

    def test_validate_file_accepts_user_preset_inheriting_a_system_preset(self):
        # user_a.json inherits the system fixture directly, which is entirely legal.
        res = self._validate_flatten_chain_member("user_a.json")
        self.assertFalse(
            any("[user-from-user inherits]" in w for w in res["warnings"]),
            f"false positive on a system parent: {res['warnings']}",
        )


    # --- Runtime log forensics: the `doctor` subcommand ---
    # Every other check in this tool is static analysis and structurally cannot see
    # what OrcaSlicer did with a file it read. The runtime is silent in the UI but
    # explicit in its debug log: a preset dropped for an unresolvable parent, keys
    # stripped by Preset::remove_invalid_keys, and a per-directory "loaded N presets"
    # tally are all logged. `doctor` reads that log back.
    #
    # These fixtures are synthetic log text, deliberately: the tests must not depend
    # on OrcaSlicer being installed, on the operator's own log, or on the state of
    # ~/Library/Application Support/OrcaSlicer. The line formats below are copied
    # verbatim (modulo names) from a real OrcaSlicer 2.4.2 log and from the format
    # strings in its sources, so a format change breaks these tests rather than
    # silently degrading the parser to "found nothing".

    LOG_PREFIX = "[error]\t2026-08-05 03:21:42.547656[Thread 0x00000001fbbb1e80]:"
    INFO_PREFIX = "[info]\t2026-08-05 03:21:42.548137[Thread 0x00000001fbbb1e80]:"

    def _drop_line(self, user_dir, domain, preset, parent):
        return (f"{self.LOG_PREFIX}can not find parent {parent} for config "
                f"{Path(user_dir) / domain / (preset + '.json')}!")

    def _loaded_line(self, user_dir, domain, count):
        return (f'{self.INFO_PREFIX}load_presets: loaded {count} presets from '
                f'"{Path(user_dir) / domain}", type {domain}')

    def _write_doctor_fixture(self, tmp, presets, log_lines):
        """Builds a user preset dir ({domain: [names]}) plus a log file, and returns
        (user_dir, log_path). Only the filenames matter -- doctor counts files."""
        user_dir = Path(tmp) / "user" / "default"
        for domain, names in presets.items():
            (user_dir / domain).mkdir(parents=True, exist_ok=True)
            for name in names:
                (user_dir / domain / f"{name}.json").write_text(json.dumps({"name": name}))
        log_path = Path(tmp) / "debug_Wed_Aug_05_03_21_41_14247.log.0"
        log_path.write_text("\n".join(log_lines) + "\n")
        return user_dir, log_path

    def test_doctor_parses_dropped_preset_and_parent(self):
        lines = [
            self._drop_line("/u", "process", "ZZ Doctor Child", "ZZ Doctor Missing Parent"),
            f"{self.LOG_PREFIX}, can not find inherit preset for user preset ZZ Doctor Imported, just skip",
            f"{self.LOG_PREFIX} can not find parent preset for ZZ Doctor Saved , inherits ZZ Doctor Absent",
        ]
        parsed = parse_orca_log("\n".join(lines))
        by_name = {e["preset"]: e for e in parsed["dropped"]}
        self.assertEqual(set(by_name), {"ZZ Doctor Child", "ZZ Doctor Imported", "ZZ Doctor Saved"})
        # The parent is the actionable half of the report -- it names what to fix.
        self.assertEqual(by_name["ZZ Doctor Child"]["parent"], "ZZ Doctor Missing Parent")
        self.assertEqual(by_name["ZZ Doctor Child"]["domain"], "process")
        self.assertEqual(by_name["ZZ Doctor Saved"]["parent"], "ZZ Doctor Absent")

    def test_doctor_parses_removed_incorrect_keys(self):
        line = (f'{self.LOG_PREFIX}Error in a preset file: The preset "ZZ Doctor Keys" contains '
                f'the following incorrect keys: zz_not_a_real_setting, zz_also_fake, which were removed')
        parsed = parse_orca_log(line)
        self.assertEqual(len(parsed["removed_keys"]), 1)
        self.assertEqual(parsed["removed_keys"][0]["preset"], "ZZ Doctor Keys")
        self.assertEqual(parsed["removed_keys"][0]["keys"], ["zz_not_a_real_setting", "zz_also_fake"])

    def _run_doctor_on_removed_key(self, key):
        # The preset lives in process/, so the removed key is judged against the
        # process key table -- which is exactly the table `clone --set` trusts.
        with tempfile.TemporaryDirectory() as tmp:
            line = (f'{self.LOG_PREFIX}The preset "ZZ Doctor Drift" contains the following '
                    f'incorrect keys: {key}, which were removed')
            user_dir, log_path = self._write_doctor_fixture(
                tmp, {"process": ["ZZ Doctor Drift"]},
                [line, self._loaded_line(Path(tmp) / "user" / "default", "process", 1)],
            )
            return run_doctor(log_path, user_dir)

    def test_doctor_flags_known_key_drift(self):
        # OrcaSlicer removed a key our table calls valid: either the table is wrong or
        # the installed OrcaSlicer is not the version it was extracted from.
        report = self._run_doctor_on_removed_key("top_shell_layers")
        self.assertEqual(len(report["known_key_drift"]), 1)
        self.assertEqual(report["known_key_drift"][0]["key"], "top_shell_layers")
        self.assertIn("process", report["known_key_drift"][0]["known_for"])

    def test_doctor_does_not_flag_drift_for_genuinely_unknown_key(self):
        # Our table already agrees the key is bogus, so there is no disagreement to report.
        report = self._run_doctor_on_removed_key("zz_not_a_real_setting")
        self.assertEqual(report["known_key_drift"], [])
        self.assertEqual(report["removed_keys"][0]["keys"], ["zz_not_a_real_setting"])

    def test_doctor_detects_count_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "user" / "default"
            user_dir, log_path = self._write_doctor_fixture(
                tmp,
                {"process": ["ZZ Doctor Kept", "ZZ Doctor Lost"], "filament": [], "machine": []},
                [
                    self._drop_line(base, "process", "ZZ Doctor Lost", "ZZ Doctor Gone"),
                    self._loaded_line(base, "process", 1),
                    self._loaded_line(base, "filament", 0),
                    self._loaded_line(base, "machine", 0),
                ],
            )
            report = run_doctor(log_path, user_dir)

            counts = {c["domain"]: c for c in report["counts"]}
            self.assertTrue(counts["process"]["mismatch"])
            self.assertEqual((counts["process"]["files"], counts["process"]["loaded"]), (2, 1))
            self.assertEqual(counts["process"]["unaccounted"], ["ZZ Doctor Lost"])
            self.assertFalse(counts["filament"]["mismatch"])
            self.assertFalse(counts["machine"]["mismatch"])
            self.assertFalse(report["healthy"])

    def test_doctor_reports_clean_for_healthy_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "user" / "default"
            user_dir, log_path = self._write_doctor_fixture(
                tmp,
                {"process": ["ZZ Doctor Fine"], "filament": [], "machine": []},
                [
                    f"{self.INFO_PREFIX}load_presets: start load_presets",
                    self._loaded_line(base, "process", 1),
                    self._loaded_line(base, "filament", 0),
                    self._loaded_line(base, "machine", 0),
                ],
            )
            report = run_doctor(log_path, user_dir)

            self.assertEqual(report["dropped"], [])
            self.assertEqual(report["removed_keys"], [])
            self.assertTrue(report["healthy"])
            self.assertFalse(any(c["mismatch"] for c in report["counts"]))

    # --- The key-removal check is BLIND on the user preset directory load path ---
    # EMPIRICAL FINDING, OrcaSlicer 2.4.2, controlled experiment:
    #   A user filament preset was written with two bogus keys: zz_not_a_real_setting
    #   (pure junk) and layer_height (a real PROCESS key in a FILAMENT preset).
    #   OrcaSlicer was restarted. The preset LOADED ("load config successful", and the
    #   loaded count matched the file count) and NO "incorrect keys" line was emitted.
    #   The format string IS in the binary, and [warning] lines ARE captured in that
    #   same log file, so this is not a verbosity effect. A grep for "incorrect keys"
    #   over every log file on the machine returns nothing, ever.
    # CONCLUSION: Preset::remove_invalid_keys does not run, or does not log, on the
    #   user preset DIRECTORY load path. It presumably applies on another path
    #   (project/3mf, import_json_presets). So `doctor` CANNOT detect a typo'd or
    #   wrong-domain key in a user preset, and the absence of removal lines is NOT
    #   evidence that the keys are valid.
    # THEREFORE: the zero case must not print an [OK] marker. A false reassurance is
    #   worse than no check. Do NOT "fix" these tests back into a green [OK] line --
    #   re-run the experiment first. The parser and the non-zero reporting path stay,
    #   because the other load paths and future versions do emit the line.

    def _doctor_stdout(self, log_lines, presets=None, extra_args=()):
        """Runs the doctor CLI over a synthetic fixture and returns its stdout."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "user" / "default"
            user_dir, log_path = self._write_doctor_fixture(
                tmp,
                presets if presets is not None else {"process": ["ZZ Doctor Fine"], "filament": [], "machine": []},
                [line(base) if callable(line) else line for line in log_lines],
            )
            proc = subprocess.run([
                sys.executable, str(self.root_dir / "validate_orca.py"), "doctor",
                "--log", str(log_path), "--user-dir", str(user_dir), "--no-color", *extra_args,
            ], capture_output=True, text=True)
            self.assertNotIn("Traceback", proc.stderr)
            return proc.stdout

    def _healthy_log_lines(self):
        return [
            lambda base: self._loaded_line(base, "process", 1),
            lambda base: self._loaded_line(base, "filament", 0),
            lambda base: self._loaded_line(base, "machine", 0),
        ]

    def test_doctor_zero_removals_does_not_claim_the_keys_are_valid(self):
        out = self._doctor_stdout(self._healthy_log_lines())
        # Isolate the key-removal section: everything from its header to the next blank line.
        section = out.split("Keys removed by OrcaSlicer (0)", 1)[1].split("\n\n", 1)[0]
        # No [OK] marker: nothing was verified, so nothing may look verified.
        self.assertNotIn("[OK]", section, f"the blind check still claims a pass:\n{section}")
        self.assertIn("[NOT CHECKED]", section)
        # The output must say WHY the absence means nothing, and what to use instead.
        self.assertIn("does not log key removal", section)
        self.assertIn("no evidence", section)
        self.assertIn("clone", section)
        # The count-mismatch section, which IS a working check, keeps its [OK].
        self.assertIn("[OK]", out.split("Preset counts", 1)[1])

    def test_doctor_json_marks_the_key_check_as_not_capable_when_no_removals(self):
        report = json.loads(self._doctor_stdout(self._healthy_log_lines(), extra_args=("--json",)))
        # An empty list alone cannot tell a machine consumer "clean" from "blind".
        self.assertEqual(report["removed_keys"], [])
        self.assertEqual(report["removed_keys_check"], "not-capable")
        self.assertFalse(report["removed_keys_check_detects_user_preset_loads"])

    def test_doctor_still_reports_removals_when_the_log_does_name_them(self):
        # Unchanged behaviour: another load path (project/3mf, import) or a future
        # OrcaSlicer version does emit the line, and it must still be reported.
        removal = (f'{self.LOG_PREFIX}The preset "ZZ Doctor Fine" contains the following '
                   f'incorrect keys: zz_not_a_real_setting, which were removed')
        out = self._doctor_stdout([removal] + self._healthy_log_lines())
        self.assertIn("Keys removed by OrcaSlicer (1)", out)
        self.assertIn("[REMOVED] ZZ Doctor Fine: zz_not_a_real_setting", out)
        self.assertNotIn("[NOT CHECKED]", out)

        report = json.loads(self._doctor_stdout([removal] + self._healthy_log_lines(), extra_args=("--json",)))
        self.assertEqual(report["removed_keys_check"], "removals-found")
        self.assertTrue(report["removed_keys_check_detects_user_preset_loads"])

    def test_doctor_still_reports_known_key_drift_when_removals_are_logged(self):
        # The drift cross-check is the other half of the non-zero path and is untouched.
        drift = (f'{self.LOG_PREFIX}The preset "ZZ Doctor Fine" contains the following '
                 f'incorrect keys: top_shell_layers, which were removed')
        out = self._doctor_stdout([drift] + self._healthy_log_lines())
        self.assertIn("KNOWN-KEY DRIFT (1)", out)
        self.assertIn("top_shell_layers", out)

    def test_doctor_degrades_gracefully_without_a_log_directory(self):
        # A machine with no OrcaSlicer install must get a clear message and a distinct
        # "could not check" exit code -- never a crash, and never a silent pass that
        # would let this be used as a gate while checking nothing.
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ)
            env["HOME"] = tmp
            env["ORCASLICER_LOG_DIR"] = str(Path(tmp) / "definitely-absent-log-dir")
            env.pop("ORCASLICER_USER_DIR", None)
            env.pop("APPDATA", None)
            proc = subprocess.run(
                [sys.executable, str(self.root_dir / "validate_orca.py"), "doctor", "--no-color"],
                capture_output=True, text=True, env=env, cwd=tmp,
            )
            self.assertEqual(proc.returncode, 2, f"stdout={proc.stdout} stderr={proc.stderr}")
            self.assertIn("No OrcaSlicer log directory", proc.stdout)
            self.assertNotIn("Traceback", proc.stderr)

    def test_doctor_cli_json_reports_dropped_preset_and_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "user" / "default"
            user_dir, log_path = self._write_doctor_fixture(
                tmp,
                {"process": ["ZZ Doctor Kept", "ZZ Doctor Lost"], "filament": [], "machine": []},
                [
                    self._drop_line(base, "process", "ZZ Doctor Lost", "ZZ Doctor Gone"),
                    self._loaded_line(base, "process", 1),
                ],
            )
            proc = subprocess.run([
                sys.executable, str(self.root_dir / "validate_orca.py"), "doctor",
                "--log", str(log_path), "--user-dir", str(user_dir), "--json",
            ], capture_output=True, text=True)
            # Non-zero so it is usable as a gate.
            self.assertEqual(proc.returncode, 1, f"stderr={proc.stderr}")
            report = json.loads(proc.stdout)
            self.assertEqual(report["log"], str(log_path))
            self.assertEqual([d["preset"] for d in report["dropped"]], ["ZZ Doctor Lost"])
            self.assertFalse(report["healthy"])


if __name__ == "__main__":
    unittest.main()
