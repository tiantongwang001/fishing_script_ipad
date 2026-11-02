"""Synthetic stream workflow integration; no HTTP connection or actual touch."""
import hashlib
import json
from unittest.mock import patch
import unittest

from PIL import Image, ImageDraw

from fishing_probe.bobber import BobberConfig
from fishing_probe.mjpeg import MJPEGFrame
from fishing_probe.workflow_runner import (
    STREAM_WDA_SETTINGS, TemporalBiteGate, calibration_issues, load_plan,
)
import test_workflow_runner as fixtures

COLORS = fixtures.COLORS
Driver = fixtures.Driver


def frame_image(state, sequence, bobber=None):
    image = Image.new("RGB", (40, 30), COLORS[state])
    image.putpixel((39, 29), (sequence, 0, 0))
    draw = ImageDraw.Draw(image)
    if bobber == "baseline":
        draw.rectangle((10, 18, 19, 23), fill="yellow")
    elif bobber == "rise":
        draw.rectangle((10, 8, 19, 13), fill="yellow")
    elif bobber == "dip":
        draw.rectangle((10, 22, 11, 23), fill="yellow")
    return image


class StreamDriver(Driver):
    def __init__(self, clock, *, hook_delay=0, hook_active_delay=0, fail_setting=False):
        super().__init__([])
        self.clock = clock
        self.window_checks = 0
        self.hook_delay = hook_delay
        self.hook_active_delay = hook_active_delay
        self.fail_setting = fail_setting
        self.original = {"mjpegServerFramerate": 10, "mjpegScalingFactor": 100,
                         "mjpegServerScreenshotQuality": 25, "mjpegFixOrientation": False}
        self.settings = self.original.copy()
        self.updates = []

    def get_settings(self):
        return self.settings.copy()

    def update_settings(self, settings):
        self.updates.append(settings.copy())
        self.settings.update(settings)
        if self.fail_setting and len(self.updates) == 1:
            raise RuntimeError("partial settings update failed")

    def get_window_size(self):
        self.window_checks += 1
        if self.window_checks == 3:
            self.clock[0] += self.hook_delay
        return super().get_window_size()

    def execute_script(self, method, payload):
        if method == "mobile: activeAppInfo" and self.active_checks == 2:
            self.clock[0] += self.hook_active_delay
        return super().execute_script(method, payload)

    def save_screenshot(self, path):
        raise AssertionError("Stream mode must not call the synchronous screenshot API")


class StreamReader:
    def __init__(self, samples, clock):
        self.samples = iter(samples)
        self.clock = clock
        self.started = False
        self.closed = False
        self.sequence = 0
        self.calls = []

    def start(self):
        self.started = True

    def close(self):
        self.closed = True

    def next_frame(self, **kwargs):
        self.calls.append(kwargs)
        state, at, bobber, age = next(self.samples)
        self.clock[0] = at
        self.sequence += 1
        image = frame_image(state, self.sequence, bobber)
        return MJPEGFrame(self.sequence, at-age, hashlib.sha256(image.tobytes()).hexdigest(),
                          image.size, image.tobytes())


class TestStreamIntegration(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TestWorkflowRunner()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        payload = self.fixture.payload
        payload["observation_source"] = "mjpeg"
        payload["settings"]["poll_seconds"] = 0.02
        payload["calibration"]["templates"] = [
            template for template in payload["calibration"]["templates"] if template["state"] != "bite"
        ]
        for template in payload["calibration"]["templates"]:
            template["regions"] = [[0, 0, 1, 1]]
        payload["calibration"]["bobber"] = {
            "image_size": [40, 30], "roi": [5, 5, 35, 28], "rise_pixels": 5,
            "provisional_reference_verified": True,
            "reference_evidence": "Synthetic temporal fixture; not a native validation claim",
        }
        self.fixture.write_plan()
        self.clock = [0.0]

    def run_stream(self, samples, *, hook_delay=0, hook_active_delay=0, fail_setting=False):
        self.driver = StreamDriver(self.clock, hook_delay=hook_delay, hook_active_delay=hook_active_delay,
                                   fail_setting=fail_setting)
        self.reader = StreamReader(samples, self.clock)
        with patch("fishing_probe.workflow_runner.MJPEGReader", return_value=self.reader), \
                patch("fishing_probe.workflow_runner.time.monotonic", side_effect=lambda: self.clock[0]):
            return self.fixture.execute(self.driver)

    def bite_samples(self):
        return [
            ("cast_ready", .01, None, 0), ("cast_ready", .02, None, 0),
            ("waiting_bite", .10, "baseline", 0), ("waiting_bite", .25, "baseline", 0),
            ("waiting_bite", .41, "baseline", 0), ("waiting_bite", .50, "rise", 0),
            ("waiting_bite", .58, "dip", 0),
        ]

    def test_temporal_config_replaces_static_bite_only_with_explicit_reference_verification(self):
        self.assertEqual(calibration_issues(load_plan(self.fixture.path)), [])
        self.fixture.payload["calibration"]["bobber"]["provisional_reference_verified"] = False
        self.fixture.write_plan()
        issues = calibration_issues(load_plan(self.fixture.path))
        self.assertTrue(any("provisional_reference_verified" in issue for issue in issues))
        self.assertTrue(any("template: bite" in issue for issue in issues))

    def test_stream_round_uses_temporal_bite_and_fresh_pre_action_frames(self):
        samples = self.bite_samples() + [
            ("reeling", .75, None, 0), ("reeling", .80, None, 0),
            ("result", 1.4, None, 0), ("result", 1.45, None, 0),
            ("cast_ready", 1.55, None, 0),
        ]
        result = self.run_stream(samples, hook_delay=.07, hook_active_delay=.051)
        self.assertEqual((result["status"], result["cycles_completed"]), ("PASS", 1))
        self.assertEqual([a["name"] for a in result["actions"]], ["cast", "hook", "reel_upper", "continue"])
        self.assertTrue(result["experimental_temporal_bite"])
        self.assertTrue(result["stream_closed"])
        self.assertTrue(result["stream_settings_restored"])
        self.assertTrue(self.reader.started and self.reader.closed)
        self.assertEqual(self.driver.updates, [STREAM_WDA_SETTINGS, self.driver.original])
        self.assertTrue(all(call["require_unique"] and call["max_age"] <= .25 for call in self.reader.calls))
        self.assertEqual(len(list(self.fixture.directory.glob("[0-9]*.png"))), len(samples))
        self.assertEqual(self.driver.window_checks, 5, "Setup plus one exact bounds check per action")
        self.assertEqual(self.driver.active_checks, 5, "Setup plus one foreground check per action")
        request = json.loads((self.fixture.directory / "0007_hook_request.json").read_text())
        self.assertTrue(request["reused_temporal_trigger_frame"])
        self.assertTrue(request["evidence_screenshot"].endswith("0007_observe.png"))
        self.assertEqual(request["stream_sequence"], 7)
        self.assertFalse(list(self.fixture.directory.glob("*_before_hook.png")))

    def test_waiting_screen_before_our_cast_never_arms_or_hooks(self):
        result = self.run_stream([("waiting_bite", .01, "dip", 0)])
        self.assertEqual(result["reason"], "unexpected_state")
        self.assertEqual(result["actions"], ())
        self.assertFalse(any(method == "mobile: touchAndHold" for method, _ in self.driver.calls))

    def test_stale_stream_frame_fails_closed_and_restores_settings(self):
        result = self.run_stream([("cast_ready", .4, None, .3)])
        self.assertEqual(result["reason"], "observer_error")
        self.assertIn("host-receipt age", result["detail"])
        self.assertFalse(any(method == "mobile: touchAndHold" for method, _ in self.driver.calls))
        self.assertTrue(self.reader.closed and self.driver.quit_called)
        self.assertEqual(self.driver.settings, self.driver.original)

    def test_hook_token_expiry_and_age_after_transport_prevent_hook(self):
        for delay, expected in ((.21, "bite token"), (.3, "host-receipt age")):
            with self.subTest(delay=delay):
                result = self.run_stream(self.bite_samples(), hook_delay=delay)
                self.assertEqual(result["reason"], "action_error")
                self.assertIn(expected, result["detail"])
                touches = [method for method, _ in self.driver.calls if method == "mobile: touchAndHold"]
                self.assertEqual(len(touches), 1, "Only the earlier cast may have been sent")
                self.clock[0] = 0

    def test_hook_reuses_current_waiting_trigger_without_consuming_another_frame(self):
        result = self.run_stream(self.bite_samples())
        self.assertEqual(result["reason"], "observer_error", "Only the next post-hook observation runs out of fixtures")
        self.assertEqual(sum(method == "mobile: touchAndHold" for method, _ in self.driver.calls), 2)
        self.assertEqual([action["name"] for action in result["actions"]], ["cast", "hook"])

    def test_partial_stream_settings_failure_restores_then_quits(self):
        with self.assertRaisesRegex(RuntimeError, "partial settings"):
            self.run_stream([], fail_setting=True)
        self.assertEqual(self.driver.settings, self.driver.original)
        self.assertTrue(self.driver.quit_called)
        self.assertFalse(self.reader.started)

    def test_hit_like_static_waiting_frame_cannot_trigger_without_motion(self):
        gate = TemporalBiteGate(BobberConfig(image_size=(40, 30), roi=(5, 5, 35, 28),
                                           rise_pixels=5), .2)
        gate.after_action("cast")
        for serial, at in enumerate((0, .15, .31, .4, .5), 1):
            image = frame_image("waiting_bite", serial, "baseline")
            frame = MJPEGFrame(serial, at, str(serial), image.size, image.tobytes())
            state, _ = gate.observe("waiting_bite", frame, image, now=at)
            self.assertEqual(state, "waiting_bite")
        with self.assertRaisesRegex(RuntimeError, "bite token"):
            gate.require_hook("waiting_bite", now=.5)


if __name__ == "__main__":
    unittest.main()
