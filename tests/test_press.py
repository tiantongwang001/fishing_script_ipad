"""Offline validation of the short native press, with no device connection."""
import unittest

from fishing_probe.gestures import press, validate_hold, validate_press


class Driver:
    def __init__(self):
        self.calls = []

    def get_window_size(self):
        return {"width": 100, "height": 80}

    def execute_script(self, method, payload):
        self.calls.append((method, payload))


class TestPress(unittest.TestCase):
    def test_native_press_payload_and_no_long_press_validator_relaxation(self):
        driver = Driver()
        press(driver, 20, 30, 0.05)
        self.assertEqual(driver.calls, [
            ("mobile: touchAndHold", {"x": 20, "y": 30, "duration": 0.05}),
        ])
        with self.assertRaises(ValueError):
            validate_hold(20, 30, 0.05)

    def test_bad_press_cannot_send_input(self):
        for seconds in (0, 0.019, 1.001, True, float("nan"), float("inf"), None, "0.5"):
            with self.subTest(seconds=seconds):
                driver = Driver()
                with self.assertRaises(ValueError):
                    press(driver, 20, 30, seconds)
                self.assertEqual(driver.calls, [])
        for point in ((-1, 30), (100, 30), (20, 80), (True, 30), (20.5, 30)):
            with self.subTest(point=point):
                driver = Driver()
                with self.assertRaises(ValueError):
                    press(driver, *point)
                self.assertEqual(driver.calls, [])

    def test_boundaries_validate_without_connecting(self):
        validate_press(0, 0, 0.02)
        validate_press(10, 10, 1)

    def test_native_exception_is_not_retried(self):
        class FailedDriver(Driver):
            def execute_script(self, method, payload):
                super().execute_script(method, payload)
                raise RuntimeError("delivery unknown")
        driver = FailedDriver()
        with self.assertRaisesRegex(RuntimeError, "delivery unknown"):
            press(driver, 20, 30)
        self.assertEqual(len(driver.calls), 1)

    def test_dispatch_guard_runs_after_bounds_and_can_refuse_touch(self):
        events = []

        class GuardedDriver(Driver):
            def get_window_size(self):
                events.append("bounds")
                return super().get_window_size()

        def refuse():
            events.append("guard")
            raise RuntimeError("deadline")

        driver = GuardedDriver()
        with self.assertRaisesRegex(RuntimeError, "deadline"):
            press(driver, 20, 30, before_dispatch=refuse)
        self.assertEqual(events, ["bounds", "guard"])
        self.assertEqual(driver.calls, [])

    def test_expected_geometry_uses_one_bounds_read_and_refuses_rotation(self):
        class CountedDriver(Driver):
            window_reads = 0

            def get_window_size(self):
                self.window_reads += 1
                return super().get_window_size()

        driver = CountedDriver()
        press(driver, 20, 30, expected_window={"width": 100, "height": 80})
        self.assertEqual(driver.window_reads, 1)
        self.assertEqual(len(driver.calls), 1)
        driver = CountedDriver()
        with self.assertRaisesRegex(ValueError, "window changed"):
            press(driver, 20, 30, expected_window={"width": 80, "height": 100})
        self.assertEqual(driver.window_reads, 1)
        self.assertEqual(driver.calls, [])


if __name__ == "__main__":
    unittest.main()
