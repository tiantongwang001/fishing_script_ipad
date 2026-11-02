"""Offline template comparisons; all generated screenshots stay under runs/."""
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from fishing_probe.vision import PreparedObserver, observe_screenshot


class TestScreenshotObserver(unittest.TestCase):
    def setUp(self):
        Path("runs").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="offline_vision_", dir="runs")
        self.directory = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.reference = self.directory / "reference.png"
        self.current = self.directory / "current.png"
        source = Image.new("RGB", (80, 40), "black")
        ImageDraw.Draw(source).rectangle((8, 8, 23, 23), fill="red")
        source.save(self.reference)
        source.save(self.current)
        self.calibration = {
            "manually_verified": True, "logical_size": [40, 20],
            "screenshot_size": [80, 40], "orientation": "landscape",
            "templates": [{"state": "cast_ready", "manually_verified": True,
                           "reference": "reference.png", "regions": [[4, 4, 8, 8]],
                           "max_error": 0.03}],
        }

    def observe(self, **kwargs):
        args = dict(logical_size=(40, 20), calibration=self.calibration,
                    base_dir=self.directory)
        args.update(kwargs)
        return observe_screenshot(self.current, **args)

    def test_matching_region_ignores_animated_background(self):
        current = Image.new("RGB", (80, 40), "blue")
        ImageDraw.Draw(current).rectangle((8, 8, 23, 23), fill="red")
        current.save(self.current)
        observed = self.observe(logical_size={"width": 40, "height": 20})
        self.assertEqual(observed.state, "cast_ready")
        self.assertEqual(observed.confidence, 1.0)
        self.assertEqual(observed.evidence[0]["region_errors"], [0.0])

    def test_prepared_in_memory_observer_matches_file_semantics_without_io(self):
        prepared = PreparedObserver(logical_size=(40, 20), calibration=self.calibration,
                                    base_dir=self.directory)
        expected = self.observe()
        with Image.open(self.current) as source:
            image = source.convert("RGB")
        with patch("pathlib.Path.read_bytes", side_effect=AssertionError("unexpected file read")), \
                patch("PIL.Image.open", side_effect=AssertionError("unexpected image open")):
            actual = prepared.observe_image(image, logical_size=(40, 20))
        self.assertEqual(actual, expected)

    def test_prepared_reference_pixels_and_manifest_are_frozen_during_run(self):
        original_hash = hashlib.sha256(self.reference.read_bytes()).hexdigest()
        prepared = PreparedObserver(logical_size=(40, 20), calibration=self.calibration,
                                    base_dir=self.directory)
        with Image.open(self.current) as source:
            image = source.convert("RGB")
        Image.new("RGB", (80, 40), "blue").save(self.reference)
        self.calibration["templates"][0]["regions"][0][0] = 0
        self.assertEqual(prepared.observe_image(image, logical_size=(40, 20)).state, "cast_ready")
        self.assertEqual(prepared.reference_manifest()[0]["reference_sha256"], original_hash)
        self.assertEqual(prepared.reference_manifest()[0]["regions"], ((4, 4, 8, 8),))
        self.assertNotEqual(original_hash, hashlib.sha256(self.reference.read_bytes()).hexdigest())

    def test_prepared_observer_still_rejects_current_geometry_and_ambiguity(self):
        prepared = PreparedObserver(logical_size=(40, 20), calibration=self.calibration,
                                    base_dir=self.directory)
        self.assertEqual(prepared.observe_image(Image.new("RGB", (40, 80)),
                                               logical_size=(40, 20)).state, "unknown")
        self.assertEqual(prepared.observe_image(Image.new("RGB", (80, 40)),
                                               logical_size=(20, 40)).state, "unknown")
        extra = copy.deepcopy(self.calibration["templates"][0])
        extra["state"] = "waiting_bite"
        self.calibration["templates"].append(extra)
        ambiguous = PreparedObserver(logical_size=(40, 20), calibration=self.calibration,
                                     base_dir=self.directory)
        with Image.open(self.current) as image:
            observation = ambiguous.observe_image(image, logical_size=(40, 20))
        self.assertEqual(observation.state, "unknown")
        self.assertIn("Ambiguous", observation.reason)

    def test_different_region_fails_closed(self):
        Image.new("RGB", (80, 40), "black").save(self.current)
        observed = self.observe()
        self.assertEqual(observed.state, "unknown")
        self.assertEqual(observed.confidence, 0)

    def test_each_region_must_match(self):
        self.calibration["templates"][0]["regions"].append([30, 1, 4, 4])
        current = Image.open(self.current).convert("RGB")
        ImageDraw.Draw(current).rectangle((60, 2, 67, 9), fill="white")
        current.save(self.current)
        observed = self.observe()
        self.assertEqual(observed.state, "unknown")
        self.assertEqual(observed.evidence[0]["region_errors"], [0.0, 1.0])

    def test_ambiguous_distinct_states_are_unknown(self):
        other = copy.deepcopy(self.calibration["templates"][0])
        other["state"] = "bite"
        self.calibration["templates"].append(other)
        observed = self.observe()
        self.assertEqual(observed.state, "unknown")
        self.assertIn("Ambiguous", observed.reason)

    def test_sale_offer_is_distinguished_from_result_at_same_button_location(self):
        # The same coordinates can mean selling or continuing. A matching
        # location/background alone must not establish which action is shown.
        templates = []
        for state, label, color in (("result", "NEXT", (0, 90, 180)),
                                    ("sale_offer", "SELL", (180, 90, 0))):
            image = Image.new("RGB", (80, 40), "black")
            draw = ImageDraw.Draw(image)
            draw.rectangle((32, 16, 75, 35), fill=color)
            draw.text((36, 19), label, fill="white")
            image.save(self.directory / f"{state}.png")
            templates.append({
                "state": state, "manually_verified": True,
                "reference": f"{state}.png", "regions": [[16, 8, 22, 10]],
                "max_error": 0.03,
            })
        self.calibration["templates"] = templates
        for expected_state in ("result", "sale_offer"):
            with self.subTest(state=expected_state):
                with Image.open(self.directory / f"{expected_state}.png") as image:
                    image.save(self.current)
                observed = self.observe()
                self.assertEqual(observed.state, expected_state)
                self.assertEqual(sum(item["matched"] for item in observed.evidence), 1)

        # A poorly selected region that contains only a shared background is
        # explicitly ambiguous, even though both references are readable.
        for template in templates:
            template["regions"] = [[0, 0, 8, 4]]
        self.assertEqual(self.observe().state, "unknown")
        self.assertIn("Ambiguous", self.observe().reason)

    def test_sale_pending_is_an_explicit_separate_state(self):
        self.calibration["templates"][0]["state"] = "sale_pending"
        self.assertEqual(self.observe().state, "sale_pending")

    def test_missing_or_unverified_calibration_is_unknown(self):
        for calibration in (None, {}, {**self.calibration, "manually_verified": False},
                            {**self.calibration, "manually_verified": "true"},
                            {**self.calibration, "templates": []}):
            with self.subTest(calibration=calibration):
                self.assertEqual(self.observe(calibration=calibration).state, "unknown")

    def test_unverified_or_missing_template_invalidates_other_match(self):
        for modification in ({"manually_verified": False}, {"reference": "missing.png"}):
            with self.subTest(modification=modification):
                calibration = copy.deepcopy(self.calibration)
                invalid = {**calibration["templates"][0], **modification, "state": "bite"}
                calibration["templates"].append(invalid)
                self.assertEqual(self.observe(calibration=calibration).state, "unknown")

    def test_missing_current_screenshot_is_unknown(self):
        self.current.unlink()
        self.assertEqual(self.observe().state, "unknown")

    def test_geometry_must_be_verified_and_unchanged(self):
        cases = [
            {"logical_size": [20, 40]}, {"screenshot_size": [40, 80]},
            {"screenshot_size": [80, 60]}, {"orientation": "portrait"},
            {"logical_size": [True, 20]}, {"logical_size": [40.0, 20]},
        ]
        for change in cases:
            with self.subTest(change=change):
                self.assertEqual(self.observe(calibration={**self.calibration, **change}).state, "unknown")
        self.assertEqual(self.observe(logical_size=(20, 40)).state, "unknown")

    def test_current_and_reference_resolution_must_both_match(self):
        Image.new("RGB", (40, 80)).save(self.current)
        self.assertEqual(self.observe().state, "unknown")
        Image.open(self.reference).save(self.current)
        Image.new("RGB", (40, 80)).save(self.reference)
        self.assertEqual(self.observe().state, "unknown")

    def test_regions_and_error_thresholds_are_bounded(self):
        cases = [
            {"regions": []}, {"regions": [[-1, 4, 8, 8]]},
            {"regions": [[4, 4, 0, 8]]}, {"regions": [[4, 4, 80, 8]]},
            {"regions": [[4, 4, True, 8]]}, {"regions": [[4, 4, 8]]},
            {"max_error": 1.0}, {"max_error": -1}, {"max_error": True},
            {"max_error": float("nan")}, {"state": "unknown"},
        ]
        for modification in cases:
            with self.subTest(modification=modification):
                calibration = copy.deepcopy(self.calibration)
                calibration["templates"][0].update(modification)
                self.assertEqual(self.observe(calibration=calibration).state, "unknown")

    def test_normalized_rgb_error_is_reported_as_similarity(self):
        current = Image.new("RGB", (80, 40), "black")
        ImageDraw.Draw(current).rectangle((8, 8, 23, 23), fill=(249, 0, 0))
        current.save(self.current)
        observed = self.observe()
        self.assertEqual(observed.state, "cast_ready")
        self.assertAlmostEqual(observed.evidence[0]["maximum_error"], 6 / (3 * 255))
        self.assertAlmostEqual(observed.confidence, 1 - 6 / (3 * 255))


if __name__ == "__main__":
    unittest.main()
