"""Offline fixed-timing tests; never connect to a device or recognize images."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fishing_probe.clicker import (
    ACTION_NAMES, ClickerSettings, build_steps, load_recipe, run_from_args, run_schedule,
    geometry_issues,
)
from fishing_probe.config import Config


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Driver:
    def __init__(self, clock, *, fail_touch=None, bounds_delay=0, rotated=False, foreground=True):
        self.session_id = "offline-clicker-session"
        self.clock = clock
        self.calls = []
        self.quit_called = False
        self.fail_touch = fail_touch
        self.bounds_delay = bounds_delay
        self.rotated = rotated
        self.foreground = foreground
        self.touches = 0
        self.bounds_reads = 0

    def execute_script(self, method, payload):
        self.calls.append((method, payload))
        if method == "mobile: activeAppInfo":
            return {"bundleId": "test.fishing" if self.foreground else "test.other"}
        if method == "mobile: touchAndHold":
            self.touches += 1
            self.clock.now += payload["duration"] + .01
            if self.touches == self.fail_touch:
                raise RuntimeError("delivery unknown")

    def get_window_size(self):
        self.bounds_reads += 1
        self.clock.now += self.bounds_delay
        return {"width": 80, "height": 100} if self.rotated else {"width": 100, "height": 80}

    def save_screenshot(self, path):
        raise AssertionError("Fixed clicker must not capture or inspect images")

    def quit(self):
        self.quit_called = True


class TestClicker(unittest.TestCase):
    def setUp(self):
        Path("runs").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="offline_clicker_", dir="runs")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.path = self.directory / "recipe.json"
        self.payload = {
            "logical_size": [100, 80], "settings": {},
            "actions": {name: {"x": 20, "y": 30,
                               "press_seconds": .5 if name == "reel_upper" else .1,
                               "manually_verified": True} for name in ACTION_NAMES},
        }
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.payload))
        return load_recipe(self.path)

    def args(self, **kwargs):
        values = dict(recipe=str(self.path), execute=False, armed=False,
                      safe_target_confirmed=False, approval_file=None)
        values.update(kwargs)
        return argparse.Namespace(**values)

    def run_cli(self, args, driver=None, clock=None):
        with patch("fishing_probe.clicker.make_run_dir", return_value=self.directory), \
                patch("fishing_probe.clicker.load", return_value=Config(udid="synthetic", game_bundle_id="test.fishing")) as config, \
                patch("fishing_probe.clicker.connect", return_value=driver) as connection, \
                patch("fishing_probe.clicker.platform.system", return_value="Darwin"), \
                patch.dict("os.environ", {"SSH_CONNECTION": "", "SSH_CLIENT": "", "CODESPACES": "", "REMOTE_CONTAINERS": ""}), \
                patch("fishing_probe.clicker.time.monotonic", side_effect=clock or (lambda: 0)), \
                patch("fishing_probe.clicker.time.sleep", side_effect=clock.sleep if clock else lambda seconds: None), \
                contextlib.redirect_stdout(io.StringIO()):
            result = run_from_args(args)
        return result, config, connection

    def execute(self, driver, clock):
        return self.run_cli(self.args(execute=True, armed=True, safe_target_confirmed=True), driver, clock)[0]

    def test_preview_does_not_load_device_or_connect(self):
        result, config, connection = self.run_cli(self.args())
        config.assert_not_called()
        connection.assert_not_called()
        self.assertEqual(result["status"], "NOT_RUN")
        self.assertEqual(result["planned_touch_count"], 13)
        self.assertAlmostEqual(result["nominal_seconds_excluding_rpc"], 17.3)
        self.assertFalse(result["screen_recognition"])

    def test_batch_mode_executes_remainder_with_one_check_per_request(self):
        self.payload['settings'].update(reel_batch_size=3, reel_pulses=8,
                                        reel_batch_gap_seconds=.05)
        self.save()
        clock = Clock()

        class BatchDriver(Driver):
            def execute(self, command, params=None):
                self.calls.append((command, params))
                actions = params['actions'][0]['actions']
                self.touches += sum(a['type'] == 'pointerDown' for a in actions)
                self.clock.now += sum(a.get('duration', 0) for a in actions) / 1000

        driver = BatchDriver(clock)
        result = self.execute(driver, clock)
        self.assertEqual((result['status'], result['cycles_api_completed']), ('PASS', 1))
        self.assertEqual(result['action_api_returns'], 6)  # CAST, HIT, 3 groups, sell
        self.assertEqual(driver.touches, 11)  # 8 reel contacts + CAST/HIT/sell
        self.assertEqual(driver.bounds_reads, 6)
        self.assertEqual(sum(name == 'mobile: activeAppInfo' for name, _ in driver.calls), 6)
        records = [json.loads(p.read_text()) for p in sorted(self.directory.glob('*_reel_upper.json'))]
        self.assertEqual([r['count'] for r in records], [3, 3, 2])
        self.assertTrue(all(r['api_returned'] for r in records))
        self.assertFalse(result['game_effect_verified'])

    def test_batch_preview_counts_contacts_separately_from_requests(self):
        self.payload['settings'].update(reel_batch_size=3, reel_pulses=8)
        self.save()
        result, config, connection = self.run_cli(self.args())
        connection.assert_not_called()
        self.assertEqual(result['planned_touch_count'], 11)
        self.assertEqual(result['planned_input_requests'], 6)

    def test_whole_round_uses_single_request_and_duration_aware_timeout(self):
        self.payload['settings'].update(loggroup=False, reel_pulses=25,
                                        reel_batch_gap_seconds=.05)
        self.payload['actions']['reel_upper']['press_seconds'] = .55
        self.save()
        clock = Clock()

        class WholeDriver(Driver):
            def execute(self, command, params=None):
                self.calls.append((command, params))
                actions = params['actions'][0]['actions']
                self.touches += sum(a['type'] == 'pointerDown' for a in actions)
                self.clock.now += sum(a.get('duration', 0) for a in actions) / 1000

        driver = WholeDriver(clock)
        result, _, connection = self.run_cli(
            self.args(execute=True, armed=True, safe_target_confirmed=True), driver, clock)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['action_api_returns'], 4)  # CAST, HIT, all reel presses, sell
        self.assertEqual(driver.bounds_reads, 4)
        self.assertEqual(driver.touches, 28)
        self.assertEqual(sum(name == 'actions' for name, _ in driver.calls), 1)
        self.assertAlmostEqual(connection.call_args.kwargs['request_timeout_seconds'],24.95)

    def test_merged_hit_runs_once_without_native_hook_and_has_complete_preview(self):
        self.payload['settings'].update(loggroup=False, merge_hit_reel=True,
                                        reel_pulses=20, hit_to_reel_seconds=.05)
        self.payload['actions']['reel_upper']['press_seconds'] = .55
        self.save()
        preview, _, _ = self.run_cli(self.args())
        self.assertEqual(preview['planned_touch_count'],23)  # CAST, HIT, 20 reels, sell
        self.assertEqual(preview['planned_input_requests'],3)
        clock = Clock()

        class MergedDriver(Driver):
            def execute(self, command, params=None):
                self.calls.append((command, params))
                actions = params['actions'][0]['actions']
                self.touches += sum(a['type'] == 'pointerDown' for a in actions)
                self.clock.now += sum(a.get('duration', 0) for a in actions) / 1000

        driver = MergedDriver(clock)
        result, _, connection = self.run_cli(
            self.args(execute=True, armed=True, safe_target_confirmed=True), driver, clock)
        self.assertEqual(result['status'],'PASS')
        self.assertEqual(result['action_api_returns'],3)
        self.assertEqual(driver.bounds_reads,3)
        self.assertEqual(driver.touches,23)
        self.assertEqual(sum(name == 'mobile: touchAndHold' for name, _ in driver.calls),2)
        self.assertAlmostEqual(connection.call_args.kwargs['request_timeout_seconds'],22.1)
        event=json.loads(next(self.directory.glob('*_reel_upper.json')).read_text())
        self.assertEqual(event['kind'],'hook_batch')
        self.assertEqual(event['hook']['press_seconds'],.1)
        self.assertEqual(event['hit_to_reel_seconds'],.05)

    def test_recipe_has_exact_fixed_order_and_ten_upper_reel_pulses(self):
        steps = build_steps(load_recipe(self.path))
        names = [step.name for step in steps if step.kind == "touch"]
        self.assertEqual(names, ["cast", "hook"] + ["reel_upper"] * 10 + ["sell"])
        self.assertEqual([step.pulse for step in steps if step.name == "reel_upper"], list(range(1, 11)))
        self.assertTrue(all(step.seconds == .05 for step in steps if step.name == "reel_release"))

    def test_fixed_run_reports_api_schedule_not_game_success_and_saves_rpc_timing(self):
        clock = Clock()
        driver = Driver(clock)
        result = self.execute(driver, clock)
        self.assertEqual((result["status"], result["reason"]), ("PASS", "timed_sequence_api_completed"))
        self.assertEqual((result["action_api_returns"], result["cycles_api_completed"]), (13, 1))
        self.assertEqual(driver.touches, 13)
        self.assertEqual(driver.bounds_reads, 13)
        self.assertFalse(result["game_effect_verified"])
        self.assertEqual(result["game_outcome"], "NOT_RUN")
        self.assertTrue(driver.quit_called)
        first = json.loads((self.directory / "0001_cast.json").read_text())
        self.assertAlmostEqual(first["native_api_elapsed_seconds"], .11)
        self.assertTrue(first["api_returned"])
        self.assertFalse(list(self.directory.glob("*.png")))

    def test_missing_arm_or_unverified_coordinates_fail_before_connection(self):
        for armed, verified in ((False, True), (True, False)):
            with self.subTest(armed=armed, verified=verified):
                self.payload["actions"]["cast"]["manually_verified"] = verified
                self.save()
                with patch("fishing_probe.clicker.make_run_dir", return_value=self.directory), \
                        patch("fishing_probe.clicker.load") as config, \
                        patch("fishing_probe.clicker.connect") as connection, self.assertRaises(PermissionError):
                    run_from_args(self.args(execute=True, armed=armed, safe_target_confirmed=True))
                config.assert_not_called()
                connection.assert_not_called()

    def test_optional_existing_approval_binds_exact_recipe_bytes(self):
        approval = self.directory / "approval.json"
        approval.write_text(json.dumps({"recipe_sha256": load_recipe(self.path).sha256,
                                        "human_note": "Synthetic offline authorization"}))
        self.path.write_text(self.path.read_text()+"\n")
        with patch("fishing_probe.clicker.make_run_dir", return_value=self.directory), \
                patch("fishing_probe.clicker.connect") as connection, self.assertRaises(PermissionError):
            run_from_args(self.args(execute=True, armed=True, safe_target_confirmed=True,
                                    approval_file=str(approval)))
        connection.assert_not_called()

    def test_elapsed_limit_during_wait_stops_before_hook(self):
        self.payload["settings"] = {"max_elapsed_seconds": 2}
        recipe = self.save()
        clock = Clock()
        actions = []
        result = run_schedule(recipe, actions.append, clock=clock, sleep=clock.sleep)
        self.assertEqual(result["reason"], "elapsed_limit")
        self.assertEqual([step.name for step in actions], ["cast"])
        self.assertAlmostEqual(clock.now, 2)

    def test_result_confirmation_is_unconditional_but_never_in_cast_hook_wait(self):
        self.payload["settings"] = {"cycles": 2, "confirm_to_sell_seconds": .6}
        self.payload["actions"]["confirm_popup"] = {
            "x": 60, "y": 60, "press_seconds": .1, "manually_verified": True,
        }
        recipe = self.save()
        steps = build_steps(recipe)
        for cycle in (1, 2):
            subset = [s for s in steps if s.cycle == cycle]
            cast = next(i for i, s in enumerate(subset) if s.name == "cast")
            hook = next(i for i, s in enumerate(subset) if s.name == "hook")
            self.assertEqual([s.kind for s in subset[cast+1:hook]], ["wait"])
            confirm = next(i for i, s in enumerate(subset) if s.name == "confirm_popup")
            self.assertEqual([s.name for s in subset[confirm-1:confirm+3]],
                             ["result_wait", "confirm_popup", "confirm_to_sell", "sell"])
        clock = Clock()
        driver = Driver(clock)  # No image API is available on this fake driver.
        result = self.execute(driver, clock)
        self.assertEqual((result["status"], result["action_api_returns"]), ("PASS", 29))
        confirms = [p for method, p in driver.calls
                    if method == "mobile: touchAndHold" and p["x"] == 60]
        self.assertEqual(len(confirms), 2)

    def test_unverified_optional_confirmation_blocks_before_connection(self):
        self.payload["actions"]["confirm_popup"] = {
            "x": 60, "y": 60, "press_seconds": .1, "manually_verified": False,
        }
        self.save()
        with patch("fishing_probe.clicker.make_run_dir", return_value=self.directory), \
                patch("fishing_probe.clicker.connect") as connection, self.assertRaises(PermissionError):
            run_from_args(self.args(execute=True, armed=True, safe_target_confirmed=True))
        connection.assert_not_called()

    def test_partial_button_overlap_requires_separated_point(self):
        self.payload["button_geometry"] = {
            "logical_size": [100, 80], "separation_guard_points": 12,
            "controls": [{"id": "target", "bounds": [0, 0, 60, 80]},
                         {"id": "other", "bounds": [40, 0, 100, 80]}],
            "action_controls": {name: "target" for name in ACTION_NAMES},
        }
        self.payload["actions"]["cast"].update(x=40, y=40)
        self.assertTrue(any("overlaps other" in issue for issue in geometry_issues(self.save())))
        self.payload["actions"]["cast"].update(x=20, y=40)
        self.assertEqual(geometry_issues(self.save()), [])
        self.payload["button_geometry"]["controls"][1]["bounds"] = [1, 1, 61, 79]
        self.assertEqual(geometry_issues(self.save()), [])  # Heavy shared region.

    def test_bad_geometry_and_points_outside_circle_are_rejected(self):
        self.payload["button_geometry"] = {
            "logical_size": [100, 80], "separation_guard_points": 12,
            "controls": [{"id": "circle", "bounds": [0, 0, 80, 80], "shape": "ellipse"}],
            "action_controls": {name: "circle" for name in ACTION_NAMES},
        }
        self.payload["actions"]["cast"].update(x=13, y=13)
        self.assertTrue(any("ellipse" in issue for issue in geometry_issues(self.save())))
        self.payload["button_geometry"]["logical_size"] = [80, 100]
        self.assertTrue(any("logical size" in issue for issue in geometry_issues(self.save())))

    def test_delayed_final_bounds_cannot_send_touch_past_deadline(self):
        clock = Clock()
        driver = Driver(clock, bounds_delay=91)
        result = self.execute(driver, clock)
        self.assertEqual(result["reason"], "elapsed_limit")
        self.assertEqual(driver.touches, 0)
        self.assertTrue(driver.quit_called)

    def test_foreground_and_exact_geometry_fail_without_touch(self):
        for kwargs in ({"rotated": True}, {"foreground": False}):
            with self.subTest(kwargs=kwargs):
                clock = Clock()
                driver = Driver(clock, **kwargs)
                result = self.execute(driver, clock)
                self.assertEqual(result["reason"], "action_error")
                self.assertEqual(driver.touches, 0)
                self.assertTrue(driver.quit_called)

    def test_failed_touch_never_retries_and_records_unknown_delivery(self):
        clock = Clock()
        driver = Driver(clock, fail_touch=3)
        result = self.execute(driver, clock)
        self.assertEqual(result["reason"], "action_error")
        self.assertEqual((result["action_attempts"], result["action_api_returns"]), (3, 2))
        self.assertEqual(driver.touches, 3)
        failed = json.loads((self.directory / "0003_reel_upper.json").read_text())
        self.assertTrue(failed["input_delivery_unknown"])
        self.assertFalse(failed["api_returned"])

    def test_cancel_stops_between_actions(self):
        clock = Clock()
        actions = []
        result = run_schedule(load_recipe(self.path), actions.append, clock=clock, sleep=clock.sleep,
                              cancelled=lambda: len(actions) >= 1)
        self.assertEqual(result["reason"], "cancelled")
        self.assertEqual([step.name for step in actions], ["cast"])

    def test_cycles_and_parameters_are_bounded_and_never_add_popup_actions(self):
        self.payload["settings"]["cycles"] = 2
        recipe = self.save()
        names = [step.name for step in build_steps(recipe) if step.kind == "touch"]
        self.assertEqual(len(names), 27)
        self.assertEqual(names.count("cast"), 2)
        self.assertEqual(names.count("reel_upper"), 20)
        steps = build_steps(recipe)
        self.assertEqual(sum(step.name == "initial_delay" for step in steps), 1)
        self.assertEqual(sum(step.name == "final_wait" for step in steps), 1)
        first_continue = next(index for index, step in enumerate(steps) if step.name == "continue")
        self.assertEqual([(step.kind, step.name, step.seconds) for step in steps[first_continue+1:first_continue+4]],
                         [("wait", "next_cycle_delay", 0), ("wait", "continue_to_cast", 0), ("touch", "cast", .1)])
        clock = Clock()
        result = run_schedule(recipe, lambda step: None, clock=clock, sleep=clock.sleep)
        self.assertEqual((result["status"], result["cycles_api_completed"]), ("PASS", 2))
        for kwargs in ({"cycles": 0}, {"cycles": 1001}, {"reel_pulses": 121},
                       {"max_elapsed_seconds": 86401}, {"hook_wait_seconds": float("nan")},
                       {"reel_release_seconds": -1}, {"cycles": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ClickerSettings(**kwargs)
        self.payload["actions"]["close_unknown_popup"] = self.payload["actions"]["cast"].copy()
        with self.assertRaises(ValueError):
            self.save()

    def test_between_round_delay_is_independent_of_startup_and_final_settle(self):
        self.payload["settings"].update(cycles=3, initial_delay_seconds=.9,
                                         next_cycle_delay_seconds=.12, final_wait_seconds=.7)
        steps = build_steps(self.save())
        self.assertEqual([step.seconds for step in steps if step.name == "initial_delay"], [.9])
        self.assertEqual([step.seconds for step in steps if step.name == "next_cycle_delay"], [.12, .12])
        self.assertEqual([step.seconds for step in steps if step.name == "final_wait"], [.7])


if __name__ == "__main__":
    unittest.main()
