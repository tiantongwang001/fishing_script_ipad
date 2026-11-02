"""Offline temporal evidence checks; no Appium, filesystem captures, or touch."""
from dataclasses import replace
import unittest

from PIL import Image, ImageDraw

from fishing_probe.bobber import BobberConfig, BobberDetector


class TestBobberDetector(unittest.TestCase):
    def setUp(self):
        self.config = BobberConfig(image_size=(80, 80), roi=(20, 10, 60, 70))
        self.detector = BobberDetector(self.config)

    def frame(self, kind="baseline", serial=0):
        image = Image.new("RGB", self.config.image_size)
        draw = ImageDraw.Draw(image)
        # Distinct captured frame contents; this pixel is outside the bobber ROI.
        image.putpixel((0, 0), (serial, 0, 0))
        if kind == "baseline":
            draw.rectangle((30, 40, 39, 49), fill="yellow")
        elif kind == "rise":
            draw.rectangle((30, 28, 39, 37), fill="yellow")
        elif kind == "dip":
            draw.rectangle((30, 47, 32, 49), fill="yellow")
        elif kind == "red":
            draw.rectangle((30, 28, 39, 37), fill="red")
        return image

    def observe(self, at, kind="baseline", serial=0, **kwargs):
        arguments = dict(captured_at=at, observed_at=at, confirmed_phase="waiting_bite")
        arguments.update(kwargs)
        return self.detector.observe(self.frame(kind, serial), **arguments)

    def baseline(self):
        self.detector.arm(confirmed_phase="waiting_bite")
        self.assertEqual(self.observe(0, serial=1).event, "collecting_baseline")
        self.assertEqual(self.observe(.15, serial=2).event, "collecting_baseline")
        result = self.observe(.31, serial=3)
        self.assertEqual(result.event, "baseline_ready")
        self.assertEqual(result.baseline_samples, 3)
        self.assertEqual(result.baseline_area, 100)
        self.assertEqual(result.baseline_top_y, 40)

    def test_requires_explicit_waiting_phase_arm(self):
        self.assertEqual(self.observe(0).event, "unarmed")
        for phase in ("bite", "reeling", "HIT", "unknown"):
            with self.subTest(phase=phase), self.assertRaises(ValueError):
                self.detector.arm(confirmed_phase=phase)
        self.assertFalse(self.detector.armed)

    def test_baseline_needs_three_distinct_frames_and_time_span(self):
        self.detector.arm(confirmed_phase="waiting_bite")
        self.observe(0, serial=1)
        self.observe(.05, serial=2)
        too_soon = self.observe(.1, serial=3)
        self.assertEqual(too_soon.event, "collecting_baseline")
        self.assertIsNone(too_soon.baseline_area)
        self.assertEqual(self.observe(.31, serial=4).event, "baseline_ready")

    def test_identical_image_content_cannot_fill_baseline(self):
        self.detector.arm(confirmed_phase="waiting_bite")
        for at in (0, .2, .4):
            result = self.observe(at, serial=1)
            self.assertEqual(result.event, "collecting_baseline")
        self.assertEqual(result.baseline_samples, 1)

    def test_rise_then_dip_emits_only_once_per_arm(self):
        self.baseline()
        self.assertEqual(self.observe(.4, "rise", 4).event, "rise")
        result = self.observe(.5, "dip", 5)
        self.assertTrue(result.triggered)
        for at, kind in ((.6, "rise"), (.7, "dip")):
            observed = self.observe(at, kind, 6)
            self.assertEqual(observed.event, "already_triggered")
            self.assertFalse(observed.triggered)

    def test_dip_without_rise_does_not_trigger(self):
        self.baseline()
        self.assertEqual(self.observe(.4, "dip", 4).event, "waiting")

    def test_late_dip_does_not_trigger(self):
        self.baseline()
        self.observe(.4, "rise", 4)
        result = self.observe(.75, "dip", 5)
        self.assertFalse(result.triggered)
        self.assertEqual(result.event, "waiting")

    def test_battle_frames_reset_and_cannot_trigger(self):
        self.baseline()
        self.observe(.4, "rise", 4)
        result = self.observe(.5, "dip", 5, confirmed_phase="reeling")
        self.assertEqual(result.event, "reset")
        self.assertFalse(result.triggered)
        self.assertFalse(self.detector.armed)
        # Returning the phase string to waiting does not implicitly rearm.
        self.assertEqual(self.observe(.6, "dip", 6).event, "unarmed")

    def test_red_animation_is_not_a_yellow_rise(self):
        self.baseline()
        self.assertEqual(self.observe(.4, "red", 4).event, "waiting")
        self.assertFalse(self.observe(.5, "dip", 5).triggered)

    def test_stale_duplicate_backwards_future_and_invalid_times_reset(self):
        cases = [
            dict(captured_at=.4, observed_at=.7),
            dict(captured_at=.31, observed_at=.31),
            dict(captured_at=.2, observed_at=.31),
            dict(captured_at=.4, observed_at=.39),
            dict(captured_at=float("nan"), observed_at=.4),
            dict(captured_at=True, observed_at=.4),
            dict(captured_at=1.0, observed_at=1.0),
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                self.baseline()
                result = self.detector.observe(self.frame("rise", 4), confirmed_phase="waiting_bite", **arguments)
                self.assertEqual(result.event, "reset")
                self.assertFalse(self.detector.armed)
                self.assertIsNone(result.baseline_area)

    def test_geometry_change_discards_prior_rise_and_baseline(self):
        self.baseline()
        self.observe(.4, "rise", 4)
        result = self.detector.observe(Image.new("RGB", (81, 80)), captured_at=.5,
                                       observed_at=.5, confirmed_phase="waiting_bite")
        self.assertEqual(result.event, "reset")
        self.assertFalse(self.observe(.6, "dip", 6).triggered)

    def test_reset_requires_a_new_arm_and_baseline(self):
        self.baseline()
        first_arm = self.detector.arm_id
        self.observe(.4, "rise", 4)
        self.detector.reset()
        self.assertEqual(self.observe(.5, "dip", 5).event, "unarmed")
        self.detector.arm(confirmed_phase="waiting_bite")
        result = self.observe(.6, "dip", 6)
        self.assertEqual(result.event, "collecting_baseline")
        self.assertEqual(result.arm_id, first_arm+1)
        self.assertFalse(result.triggered)

    def test_missing_yellow_cannot_establish_baseline(self):
        self.detector.arm(confirmed_phase="waiting_bite")
        for serial, at in enumerate((0, .2, .4)):
            result = self.observe(at, "red", serial)
            self.assertIsNone(result.baseline_area)
            self.assertEqual(result.baseline_samples, 0)

    def test_config_rejects_weakened_baseline_invalid_geometry_and_thresholds(self):
        for modification in (
            {"baseline_frames": 2}, {"baseline_seconds": .2}, {"roi": (20, 10, 81, 70)},
            {"image_size": (True, 80)}, {"rise_pixels": float("inf")},
            {"dip_area_fraction": .8}, {"maximum_frame_age_seconds": 0},
            {"hue_degrees": (80, 40)}, {"minimum_saturation": 1.1},
        ):
            with self.subTest(modification=modification), self.assertRaises(ValueError):
                replace(self.config, **modification)


if __name__ == "__main__":
    unittest.main()
