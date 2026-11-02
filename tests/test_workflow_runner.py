"""Run approved workflows against synthetic images/drivers only."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from fishing_probe.config import Config
from fishing_probe.connection import connect
from fishing_probe.workflow import Approval
from fishing_probe.workflow_runner import (
    calibration_issues, load_approval, load_plan, required_actions, run_from_args,
)


COLORS = {
    "cast_ready": (25, 0, 0), "waiting_bite": (65, 0, 0), "bite": (105, 0, 0),
    "reeling": (145, 0, 0), "result": (185, 0, 0), "unknown": (225, 0, 0),
    "sale_offer": (0, 80, 0), "sale_pending": (0, 160, 0),
}


class Driver:
    def __init__(self, states, *, foreground_switch=False, touch_error=False, quit_error=False):
        self.states = iter(states)
        self.session_id = "synthetic-session"
        self.capabilities = {"synthetic": True}
        self.calls = []
        self.quit_called = False
        self.quit_error = quit_error
        self.touch_error = touch_error
        self.foreground_switch = foreground_switch
        self.active_checks = 0

    def get_window_size(self):
        return {"width": 20, "height": 15}

    def save_screenshot(self, path):
        Image.new("RGB", (40, 30), COLORS[next(self.states)]).save(path)
        return True

    def execute_script(self, method, payload):
        self.calls.append((method, payload))
        if method == "mobile: activeAppInfo":
            self.active_checks += 1
            bundle = "test.other" if self.foreground_switch and self.active_checks >= 3 else "test.fishing"
            return {"bundleId": bundle}
        if method == "mobile: touchAndHold" and self.touch_error:
            raise RuntimeError("touch delivery unknown")

    def quit(self):
        self.quit_called = True
        if self.quit_error:
            raise RuntimeError("quit failed")


class TestWorkflowRunner(unittest.TestCase):
    def setUp(self):
        Path("runs").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="offline_workflow_", dir="runs")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.path = self.directory / "plan.json"
        self.approval_path = self.directory / "approval.json"
        templates = []
        for state in ("cast_ready", "waiting_bite", "bite", "reeling", "result"):
            reference = self.directory / f"reference_{state}.png"
            Image.new("RGB", (40, 30), COLORS[state]).save(reference)
            templates.append({
                "state": state, "manually_verified": True, "reference": str(reference),
                "regions": [[0, 0, 20, 15]], "max_error": 0,
                "continue_label_verified": state == "result",
            })
        self.payload = {
            "logical_size": [20, 15], "screenshot_size": [40, 30],
            "settings": {"transition_grace_seconds": 0, "max_reel_pulses": 3},
            "actions": {name: {"x": 5, "y": 6, "press_seconds": 0.5,
                               "manually_verified": True}
                        for name in ("cast", "hook", "reel_upper", "continue")},
            "calibration": {
                "manually_verified": True, "logical_size": [20, 15],
                "screenshot_size": [40, 30], "orientation": "landscape",
                "bite_trigger_verified": True,
                "bite_trigger_evidence": "Synthetic test fixture, not actual HIT calibration",
                "templates": templates,
            },
        }
        self.write_plan()

    def write_plan(self):
        self.path.write_text(json.dumps(self.payload), encoding="utf-8")
        plan = load_plan(self.path)
        self.approval_payload = {
            "plan_sha256": plan.sha256, "human_note": "Synthetic offline test approval only",
            "allowed_actions": sorted(required_actions(plan)), "max_cycles": 1, "max_reel_pulses": 3,
        }
        self.write_approval()

    def write_approval(self):
        self.approval_path.write_text(json.dumps(self.approval_payload), encoding="utf-8")

    def args(self, **kwargs):
        values = dict(plan=str(self.path), execute=False, armed=False,
                      safe_target_confirmed=False, approval_file=None)
        values.update(kwargs)
        return argparse.Namespace(**values)

    def execute(self, driver):
        with patch("fishing_probe.workflow_runner.make_run_dir", return_value=self.directory), \
                patch("fishing_probe.workflow_runner._require_local_mac"), \
                patch("fishing_probe.workflow_runner.load", return_value=Config(
                    udid="synthetic", game_bundle_id="test.fishing")), \
                patch("fishing_probe.workflow_runner.connect", return_value=driver) as connection, \
                patch("fishing_probe.workflow_runner.time.sleep"), \
                contextlib.redirect_stdout(io.StringIO()):
            result = run_from_args(self.args(execute=True, armed=True,
                                            safe_target_confirmed=True,
                                            approval_file=str(self.approval_path)))
        connection.assert_called_once()
        self.assertEqual(connection.call_args.kwargs["request_timeout_seconds"], 15)
        return result

    def test_preview_never_loads_device_configuration_or_connects(self):
        with patch("fishing_probe.workflow_runner.make_run_dir", return_value=self.directory), \
                patch("fishing_probe.workflow_runner.load") as configuration, \
                patch("fishing_probe.workflow_runner.connect") as connection, \
                contextlib.redirect_stdout(io.StringIO()):
            preview = run_from_args(self.args())
        self.assertTrue(preview["ready_for_separate_approval"])
        self.assertEqual(preview["status"], "NOT_RUN")
        configuration.assert_not_called()
        connection.assert_not_called()
        self.assertTrue((self.directory / "workflow_preview.json").is_file())

    def test_preview_lists_missing_calibrations(self):
        self.payload["actions"]["hook"]["manually_verified"] = False
        self.payload["calibration"]["bite_trigger_verified"] = False
        self.payload["calibration"]["templates"] = []
        self.write_plan()
        issues = calibration_issues(load_plan(self.path))
        self.assertTrue(any("hook" in issue for issue in issues))
        self.assertTrue(any("bobber" in issue for issue in issues))
        self.assertTrue(any("bite" in issue for issue in issues))

    def test_execution_flags_missing_fail_before_configuration_or_connection(self):
        for args in (
            self.args(execute=True), self.args(execute=True, armed=True, safe_target_confirmed=True),
        ):
            with self.subTest(args=args), \
                    patch("fishing_probe.workflow_runner.make_run_dir", return_value=self.directory), \
                    patch("fishing_probe.workflow_runner.load") as configuration, \
                    patch("fishing_probe.workflow_runner.connect") as connection, \
                    contextlib.redirect_stdout(io.StringIO()), self.assertRaises(PermissionError):
                run_from_args(args)
            configuration.assert_not_called()
            connection.assert_not_called()

    def test_digest_binds_exact_plan_bytes(self):
        self.path.write_text(self.path.read_text() + "\n")
        with self.assertRaisesRegex(PermissionError, "digest"):
            load_approval(self.approval_path, load_plan(self.path))

    def test_approval_requires_human_note_all_actions_and_explicit_bounds(self):
        changes = [
            {"human_note": ""}, {"allowed_actions": ["cast"]},
            {"allowed_actions": ["cast", "cast"]}, {"max_cycles": 0},
        ]
        original = self.approval_payload.copy()
        for change in changes:
            with self.subTest(change=change):
                self.approval_payload = {**original, **change}
                self.write_approval()
                with self.assertRaises(PermissionError):
                    load_approval(self.approval_path, load_plan(self.path))
        self.approval_payload = {k: v for k, v in original.items() if k != "max_reel_pulses"}
        self.write_approval()
        with self.assertRaises(PermissionError):
            load_approval(self.approval_path, load_plan(self.path))

    def test_unverified_required_calibration_prevents_connection(self):
        self.payload["calibration"]["templates"][2]["manually_verified"] = False
        self.write_plan()
        with patch("fishing_probe.workflow_runner.make_run_dir", return_value=self.directory), \
                patch("fishing_probe.workflow_runner.load") as configuration, \
                patch("fishing_probe.workflow_runner.connect") as connection, \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(PermissionError):
            run_from_args(self.args(execute=True, armed=True, safe_target_confirmed=True,
                                    approval_file=str(self.approval_path)))
        configuration.assert_not_called()
        connection.assert_not_called()

    def test_result_template_must_include_verified_continue_label(self):
        self.payload["calibration"]["templates"][-1]["continue_label_verified"] = False
        self.write_plan()
        self.assertTrue(any("Continue Fishing label" in issue
                            for issue in calibration_issues(load_plan(self.path))))

    def test_unknown_current_screen_stops_without_input_and_quits(self):
        driver = Driver(["unknown"])
        result = self.execute(driver)
        self.assertEqual((result["status"], result["reason"]), ("FAILED", "unknown_state"))
        self.assertTrue(driver.quit_called)
        self.assertFalse(any(method == "mobile: touchAndHold" for method, _ in driver.calls))

    def test_mocked_one_round_has_fresh_observation_before_every_action(self):
        driver = Driver([
            "cast_ready", "cast_ready", "bite", "bite", "reeling", "reeling",
            "result", "result", "cast_ready",
        ])
        result = self.execute(driver)
        self.assertEqual((result["status"], result["cycles_completed"]), ("PASS", 1))
        touches = [payload for method, payload in driver.calls if method == "mobile: touchAndHold"]
        self.assertEqual(len(touches), 4)
        self.assertEqual(len(list(self.directory.glob("[0-9]*.png"))), 9)
        self.assertTrue(driver.quit_called)
        self.assertTrue((self.directory / "workflow_result.json").exists())

    def test_pre_action_changed_screen_and_foreground_prevent_touch(self):
        for driver in (
            Driver(["cast_ready", "unknown"]),
            Driver(["cast_ready", "cast_ready"], foreground_switch=True),
        ):
            with self.subTest(driver=driver):
                result = self.execute(driver)
                self.assertEqual(result["reason"], "action_error")
                self.assertFalse(any(method == "mobile: touchAndHold" for method, _ in driver.calls))
                self.assertTrue(driver.quit_called)

    def test_window_change_immediately_before_input_prevents_touch(self):
        class RotatedDriver(Driver):
            window_checks = 0

            def get_window_size(self):
                self.window_checks += 1
                if self.window_checks >= 3:
                    return {"width": 15, "height": 20}
                return super().get_window_size()

        driver = RotatedDriver(["cast_ready", "cast_ready"])
        result = self.execute(driver)
        self.assertEqual(result["reason"], "action_error")
        self.assertIn("window changed", result["detail"])
        self.assertFalse(any(method == "mobile: touchAndHold" for method, _ in driver.calls))
        self.assertTrue(driver.quit_called)

    def test_pixel_geometry_mismatch_stops_even_with_valid_logical_window(self):
        class WrongPixelsDriver(Driver):
            def save_screenshot(self, path):
                Image.new("RGB", (80, 60), COLORS["cast_ready"]).save(path)
                return True

        driver = WrongPixelsDriver([])
        result = self.execute(driver)
        self.assertEqual(result["reason"], "observer_error")
        self.assertIn("dimensions differ", result["detail"])
        self.assertFalse(any(method == "mobile: touchAndHold" for method, _ in driver.calls))
        self.assertTrue(driver.quit_called)

    def test_final_bounds_latency_cannot_bypass_dispatch_deadline_or_press_budget(self):
        for delayed_time in (91.0, 89.75):
            with self.subTest(delayed_time=delayed_time):
                clock = [0.0]

                class DelayedBoundsDriver(Driver):
                    checks = 0

                    def get_window_size(self):
                        self.checks += 1
                        if self.checks == 3:
                            clock[0] = delayed_time
                        return super().get_window_size()

                driver = DelayedBoundsDriver(["cast_ready", "cast_ready"])
                with patch("fishing_probe.workflow_runner.time.monotonic", side_effect=lambda: clock[0]):
                    result = self.execute(driver)
                self.assertEqual(result["reason"], "action_error")
                self.assertIn("dispatch deadline", result["detail"])
                self.assertFalse(any(method == "mobile: touchAndHold" for method, _ in driver.calls))
                self.assertTrue(driver.quit_called)

    def test_action_error_preserved_when_cleanup_also_fails(self):
        driver = Driver(["cast_ready", "cast_ready"], touch_error=True, quit_error=True)
        result = self.execute(driver)
        self.assertEqual(result["reason"], "action_error")
        self.assertIn("touch delivery unknown", result["detail"])
        self.assertIn("quit failed", result["cleanup_error"])
        self.assertEqual(sum(method == "mobile: touchAndHold" for method, _ in driver.calls), 1)

    def test_sale_requires_distinct_verified_template_and_approval(self):
        self.payload["settings"]["sell_catches"] = True
        self.payload["actions"]["sell"] = {"x": 5, "y": 6, "press_seconds": 0.5,
                                             "manually_verified": True}
        reference = self.directory / "reference_sale.png"
        Image.new("RGB", (40, 30), COLORS["sale_offer"]).save(reference)
        self.payload["calibration"]["templates"].append({
            "state": "sale_offer", "manually_verified": True, "reference": str(reference),
            "regions": [[0, 0, 20, 15]], "max_error": 0, "sell_label_verified": True,
        })
        self.write_plan()
        self.assertEqual(calibration_issues(load_plan(self.path)), [])
        self.approval_payload["allowed_actions"].remove("sell")
        self.write_approval()
        with self.assertRaisesRegex(PermissionError, "omits"):
            load_approval(self.approval_path, load_plan(self.path))
        self.write_plan()
        driver = Driver([
            "cast_ready", "cast_ready", "bite", "bite", "sale_offer", "sale_offer",
            "sale_offer", "result", "result", "cast_ready",
        ])
        result = self.execute(driver)
        self.assertEqual((result["status"], result["cycles_completed"]), ("PASS", 1))
        self.assertEqual([a["name"] for a in result["actions"]], ["cast", "hook", "sell", "continue"])

    def test_workflow_transport_has_finite_timeout_and_disables_retries(self):
        with patch("fishing_probe.connection.webdriver.Remote") as remote:
            connect(Config(udid="synthetic"), request_timeout_seconds=12)
        client = remote.call_args.kwargs["client_config"]
        self.assertEqual(client.timeout, 12)
        self.assertIs(client.init_args_for_pool_manager["init_args_for_pool_manager"]["retries"], False)


if __name__ == "__main__":
    unittest.main()
