"""The convenience launcher never touches the device in these tests."""
import contextlib
import io
from pathlib import Path
import unittest
import tempfile
import json
from unittest.mock import patch

import run_fishing


class TestLauncher(unittest.TestCase):
    def test_manual_start_executes_bounded_default_recipe(self):
        root = Path("/synthetic/project")
        with patch.object(run_fishing, "PROJECT_ROOT", root), \
                patch("pathlib.Path.is_file", return_value=True), \
                patch("run_fishing.prepare_default_recipe", return_value=root / "clicker_recipe.json"), \
                patch("run_fishing.os.chdir") as chdir, patch("run_fishing.os.execv") as execute:
            self.assertEqual(run_fishing.main([]), 0)
        python = str(root / ".venv/bin/python")
        execute.assert_called_once_with(python, [python, "-m", "fishing_probe", "clicker",
                                                "--recipe", str(root / "clicker_recipe.json"),
                                                "--execute", "--armed", "--safe-target-confirmed"])
        chdir.assert_called_once_with(root)

    def test_forwards_explicit_recipe_and_execution_flags_without_adding_approval(self):
        arguments = ["--recipe", "other.json", "--execute", "--armed", "--safe-target-confirmed"]
        with patch("pathlib.Path.is_file", return_value=True), patch("run_fishing.os.chdir"), \
                patch("run_fishing.os.execv") as execute:
            run_fishing.main(arguments)
        self.assertEqual(execute.call_args.args[1][4:], arguments)

    def test_missing_venv_reports_error_without_running_or_installing(self):
        with patch("pathlib.Path.is_file", return_value=False), patch("run_fishing.os.execv") as execute, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run_fishing.main([]), 2)
        execute.assert_not_called()

    def test_edited_count_reaches_actual_schedule(self):
        from fishing_probe.clicker import load_recipe, build_steps
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "clicker_recipe.json").write_bytes(
                (run_fishing.PROJECT_ROOT / "clicker_recipe.json").read_bytes())
            for count in (25, 17):
                with patch.object(run_fishing, "PROJECT_ROOT", root), \
                        patch.object(run_fishing, "REEL_PULSES", count):
                    recipe = load_recipe(run_fishing.prepare_default_recipe())
                pulses = [s for s in build_steps(recipe) if s.name == "reel_upper"]
                self.assertEqual(sum(s.count for s in pulses), count * run_fishing.MAX_ROUNDS)
                self.assertEqual(recipe.settings.cycles, run_fishing.MAX_ROUNDS)
                self.assertEqual(recipe.settings.max_elapsed_seconds, run_fishing.MAX_RUN_SECONDS)
                self.assertEqual(recipe.settings.reel_batch_size, run_fishing.REEL_BATCH_SIZE)
                for step in pulses:
                    expected = step.count * run_fishing.REEL_HOLD_SECONDS + (step.count - 1) * recipe.settings.reel_batch_gap_seconds
                    if step.kind == "hook_batch":
                        expected += recipe.payload["actions"]["hook"]["press_seconds"] + run_fishing.HIT_TO_REEL_SECONDS
                    self.assertAlmostEqual(step.seconds, expected)

    def test_invalid_count_rejected_before_writing(self):
        for count in (True, 0, 121, 2.5):
            with patch.object(run_fishing, "REEL_PULSES", count):
                with self.assertRaises(ValueError):
                    run_fishing.prepare_default_recipe()

    def test_release_variable_controls_scheduled_waits_and_rejects_negative(self):
        from fishing_probe.clicker import load_recipe, build_steps
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "clicker_recipe.json").write_bytes(
                (run_fishing.PROJECT_ROOT / "clicker_recipe.json").read_bytes())
            for seconds in (0, 0.0125, 0.05):
                with patch.object(run_fishing, "PROJECT_ROOT", root), \
                        patch.object(run_fishing, "REEL_RELEASE_SECONDS", seconds), \
                        patch.object(run_fishing, "loggroup", True):
                    recipe = load_recipe(run_fishing.prepare_default_recipe())
                waits = [s for s in build_steps(recipe) if s.name == "reel_release"]
                self.assertEqual(len(waits), ((run_fishing.REEL_PULSES + run_fishing.REEL_BATCH_SIZE - 1) // run_fishing.REEL_BATCH_SIZE) * run_fishing.MAX_ROUNDS)
                self.assertTrue(all(s.seconds == seconds for s in waits))
            with patch.object(run_fishing, "PROJECT_ROOT", root), \
                    patch.object(run_fishing, "REEL_RELEASE_SECONDS", -0.01):
                with self.assertRaises(ValueError):
                    load_recipe(run_fishing.prepare_default_recipe())

    def test_preview_never_arms_or_connects(self):
        with patch("pathlib.Path.is_file", return_value=True), \
                patch("run_fishing.os.chdir"), patch("run_fishing.os.execv") as execute:
            self.assertEqual(run_fishing.main(["--preview", "--recipe", "other.json"]), 0)
        args = execute.call_args.args[1]
        for flag in ("--preview", "--execute", "--armed", "--safe-target-confirmed"):
            self.assertNotIn(flag, args)

    def test_preview_execute_conflict_does_not_launch(self):
        with patch("run_fishing.os.execv") as execute, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run_fishing.main(["--preview", "--execute"]), 2)
        execute.assert_not_called()

    def test_invalid_round_limit_rejected(self):
        for rounds in (True, 0, 1001, 1.5):
            with patch.object(run_fishing, "MAX_ROUNDS", rounds):
                with self.assertRaises(ValueError):
                    run_fishing.prepare_default_recipe()

    def test_fast_mode_has_no_repair_or_pause_between_continue_and_cast(self):
        from fishing_probe.clicker import load_recipe, build_steps, geometry_issues
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/"clicker_recipe.json").write_bytes(
                (run_fishing.PROJECT_ROOT/"clicker_recipe.json").read_bytes())
            with patch.object(run_fishing,"PROJECT_ROOT",root), \
                    patch.object(run_fishing,"MAX_ROUNDS",2), \
                    patch.object(run_fishing,"AUTO_REPAIR",0), \
                    patch.object(run_fishing,"CONTINUE_TO_CAST_SECONDS",0):
                recipe=load_recipe(run_fishing.prepare_default_recipe())
        self.assertEqual(geometry_issues(recipe),[])
        steps=build_steps(recipe)
        self.assertFalse(any(s.name.startswith("repair") for s in steps))
        for i,step in enumerate(steps):
            if step.name in ("startup_continue","continue") and step.cycle<2:
                between=[]
                for following in steps[i+1:]:
                    if following.name=="cast": break
                    between.append(following)
                else:
                    self.fail("No CAST after continue")
                self.assertTrue(all(s.kind=="wait" and s.seconds==0 for s in between))
