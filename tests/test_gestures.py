"""Offline payload/safety tests; no Appium session or physical input is sent."""
import copy
import unittest

from selenium.webdriver.remote.command import Command

from fishing_probe.gestures import (
    circle_points, continuous_path, drag, hold, validate_drag, validate_hold, validate_path,
)


class RecordingDriver:
    def __init__(self, width=1024, height=768, action_error=None, release_error=None):
        self.window = {"width": width, "height": height}
        self.calls = []
        self.action_error = action_error
        self.release_error = release_error

    def get_window_size(self):
        return self.window

    def execute_script(self, command, params):
        self.calls.append((command, copy.deepcopy(params)))

    def execute(self, command, params=None):
        self.calls.append((command, copy.deepcopy(params)))
        if command == Command.W3C_ACTIONS and self.action_error:
            raise self.action_error
        if command == Command.W3C_CLEAR_ACTIONS and self.release_error:
            raise self.release_error
        return {"value": None}


class TestGestures(unittest.TestCase):
    def test_offline_validators_need_no_driver(self):
        validate_hold(10, 10, 0.8)
        validate_drag(10, 10, 20, 20, 0.8)
        validate_path([(10, 10), (20, 20)])
        with self.assertRaises(ValueError):
            validate_hold(-1, 10)
        with self.assertRaises(ValueError):
            validate_drag(10, 10, 20, 20, 10)
        with self.assertRaises(ValueError):
            validate_path([(10, 10), (20, float("nan"))])

    def test_hold_uses_logical_points_without_pixel_scaling(self):
        driver = RecordingDriver()
        hold(driver, 1023, 767, 1.25)
        self.assertEqual(driver.calls, [
            ("mobile: touchAndHold", {"x": 1023, "y": 767, "duration": 1.25})
        ])

    def test_drag_duration_controls_initial_hold(self):
        driver = RecordingDriver()
        drag(driver, 10, 20, 800, 600, hold_seconds=1.2)
        self.assertEqual(driver.calls, [
            ("mobile: dragFromToForDuration", {
                "fromX": 10, "fromY": 20, "toX": 800, "toY": 600, "duration": 1.2
            })
        ])

    def test_bad_coordinates_never_inject_input(self):
        for point in [(-1, 0), (0, -1), (1024, 0), (0, 768),
                      (1.9, 2), (True, 2), (1, float("nan")), ("2", 3)]:
            for kind in ("hold", "drag", "path"):
                with self.subTest(point=point, kind=kind):
                    driver = RecordingDriver()
                    with self.assertRaises(ValueError):
                        if kind == "hold":
                            hold(driver, *point)
                        elif kind == "drag":
                            drag(driver, 10, 10, *point)
                        else:
                            continuous_path(driver, [(10, 10), (20, 20), point])
                    self.assertEqual(driver.calls, [])

    def test_bad_window_size_never_injects_input(self):
        for width in (0, -1, None, float("nan"), float("inf"), True):
            with self.subTest(width=width):
                driver = RecordingDriver(width=width)
                with self.assertRaisesRegex(ValueError, "window size"):
                    hold(driver, 1, 1)
                self.assertEqual(driver.calls, [])

    def test_hold_and_drag_reject_unbounded_or_non_numeric_duration(self):
        for seconds in (0.49, 5.01, float("nan"), float("inf"), True, "0.8", None):
            for kind in ("hold", "drag"):
                with self.subTest(seconds=seconds, kind=kind):
                    driver = RecordingDriver()
                    with self.assertRaises(ValueError):
                        if kind == "hold":
                            hold(driver, 10, 10, seconds)
                        else:
                            drag(driver, 10, 10, 20, 20, seconds)
                    self.assertEqual(driver.calls, [])

    def test_real_selenium_encoding_keeps_one_finger_down_for_path(self):
        driver = RecordingDriver()
        points = [(10, 20), (30, 40), (50, 60)]
        continuous_path(driver, points, step_seconds=0.075, initial_hold=0.125)
        self.assertEqual(len(driver.calls), 1)
        command, payload = driver.calls[0]
        self.assertEqual(command, Command.W3C_ACTIONS)
        self.assertEqual(len(payload["actions"]), 1)
        source = payload["actions"][0]
        self.assertEqual(source["type"], "pointer")
        self.assertEqual(source["parameters"], {"pointerType": "touch"})
        actions = source["actions"]
        self.assertEqual([a["type"] for a in actions], [
            "pointerMove", "pointerDown", "pause", "pointerMove", "pointerMove", "pointerUp"
        ])
        moves = [a for a in actions if a["type"] == "pointerMove"]
        self.assertEqual([(a["x"], a["y"]) for a in moves], points)
        self.assertEqual([a["duration"] for a in moves], [0, 75, 75])
        self.assertTrue(all(a["origin"] == "viewport" for a in moves))
        self.assertEqual(actions[2]["duration"], 125)
        self.assertEqual(actions[1]["button"], 0)
        self.assertEqual(actions[-1]["button"], 0)

    def test_path_can_skip_initial_hold(self):
        driver = RecordingDriver()
        continuous_path(driver, [(1, 1), (2, 2)], initial_hold=0)
        actions = driver.calls[0][1]["actions"][0]["actions"]
        self.assertNotIn("pause", [a["type"] for a in actions])

    def test_path_checks_whole_shape_and_limits_before_injection(self):
        cases = [
            ([], {}), ([(1, 1)], {}), ([(1, 1)] * 110, {}),
            ([(1, 1), (2,)], {}), ([(1, 1), "23"], {}), (None, {}),
            ([(1, 1), (2, 2)], {"step_seconds": 0.019}),
            ([(1, 1), (2, 2)], {"step_seconds": 0.251}),
            ([(1, 1), (2, 2)], {"step_seconds": float("nan")}),
            ([(1, 1), (2, 2)], {"initial_hold": -0.01}),
            ([(1, 1), (2, 2)], {"initial_hold": 1.01}),
            ([(1, 1), (2, 2)], {"initial_hold": True}),
            ([(1, 1)] * 42, {"step_seconds": 0.25, "initial_hold": 0}),
        ]
        for points, kwargs in cases:
            with self.subTest(points=points, kwargs=kwargs):
                driver = RecordingDriver()
                with self.assertRaises(ValueError):
                    continuous_path(driver, points, **kwargs)
                self.assertEqual(driver.calls, [])

    def test_path_contact_time_boundary_is_ten_seconds(self):
        driver = RecordingDriver()
        continuous_path(driver, [(1, 1)] * 37, step_seconds=0.25, initial_hold=1)
        actions = driver.calls[0][1]["actions"][0]["actions"]
        self.assertEqual(sum(a.get("duration", 0) for a in actions), 10000)

    def test_failed_path_attempts_release_without_replaying(self):
        error = RuntimeError("transport failed; delivery unknown")
        driver = RecordingDriver(action_error=error)
        with self.assertRaises(RuntimeError) as raised:
            continuous_path(driver, [(1, 1), (2, 2)])
        self.assertIs(raised.exception, error)
        self.assertEqual([c[0] for c in driver.calls], [Command.W3C_ACTIONS, Command.W3C_CLEAR_ACTIONS])

    def test_release_failure_does_not_replace_original_failure(self):
        error = RuntimeError("original")
        driver = RecordingDriver(action_error=error, release_error=RuntimeError("cleanup"))
        with self.assertRaises(RuntimeError) as raised:
            continuous_path(driver, [(1, 1), (2, 2)])
        self.assertIs(raised.exception, error)
        self.assertIn("cleanup", error.__notes__[0])
        self.assertEqual(len(driver.calls), 2)

    def test_circle_bounds_and_closure(self):
        points = circle_points(250, 250, 200, turns=3, samples_per_turn=36)
        self.assertEqual(len(points), 109)
        self.assertEqual(points[0], points[-1])
        self.assertTrue(all(50 <= x <= 450 and 50 <= y <= 450 for x, y in points))
        for kwargs in ({"radius": 201}, {"turns": 4}, {"turns": True},
                       {"samples_per_turn": 8.5}, {"cx": 4}, {"cy": -1}):
            with self.subTest(kwargs=kwargs):
                params = {"cx": 100, "cy": 100, "radius": 25}
                params.update(kwargs)
                with self.assertRaises(ValueError):
                    circle_points(**params)


if __name__ == "__main__":
    unittest.main()
