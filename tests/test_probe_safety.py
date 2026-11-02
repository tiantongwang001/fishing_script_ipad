import argparse
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fishing_probe.apps import normalize_app_list, select_fishing_game
from fishing_probe.cli import capture_screen, inspect_current, probe
from fishing_probe.config import Config, load


class FakeDriver:
    """Offline fixture: never connects to Appium or a device."""

    def __init__(self, active_bundles=None, save_ok=True):
        self.session_id = "offline-test-session"
        self.capabilities = {"platformName": "iOS", "testOnly": True}
        self.calls = []
        self.active_bundles = list(active_bundles or ["test.fishing"])
        self.save_ok = save_ok
        self.quit_called = False

    def get_window_size(self):
        return {"width": 100, "height": 200}

    def execute_script(self, method, args):
        self.calls.append((method, args))
        if method == "mobile: listApps":
            return {"test.fishing": {"CFBundleDisplayName": "Fishing Superstars"}}
        if method == "mobile: activeAppInfo":
            bundle = self.active_bundles[0]
            if len(self.active_bundles) > 1:
                self.active_bundles.pop(0)
            return {"bundleId": bundle}

    def save_screenshot(self, path):
        if self.save_ok:
            from PIL import Image
            Image.new("RGB", (200, 400)).save(path)
        return self.save_ok

    def quit(self):
        self.quit_called = True


def gesture_args(**overrides):
    values = dict(command="gesture", kind="hold", armed=True,
                  safe_target_confirmed=True, x=20, y=30, to_x=None,
                  to_y=None, seconds=0.8, radius=None, turns=1,
                  step_seconds=0.05)
    values.update(overrides)
    return argparse.Namespace(**values)


class TestProbeSafety(unittest.TestCase):
    def setUp(self):
        Path("runs").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="offline_safety_", dir="runs")
        self.directory = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_local_url_validation_rejects_remote_and_userinfo_bypasses(self):
        for url in (
            "http://127.0.0.1:4723@remote.example", "http://localhost:4723@remote.example:4723",
            "http://user@localhost:4723", "http://localhost.evil:4723",
            "http://0.0.0.0:4723", "http://localhost", "http://localhost:0",
            "http://localhost:65536", "http://localhost:invalid", "http://[::1",
            "http://localhost:4723?remote=example", "http://localhost:4723#fragment",
            "http://local\nhost:4723", " http://localhost:4723", "https://localhost:4723",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                Config(udid="offline-device", appium_url=url)

    def test_config_preserves_empty_wda_suffix_and_local_base_path(self):
        path = self.directory / "config.json"
        path.write_text(json.dumps({
            "udid": "offline-device", "appium_url": "http://localhost:4723/wd/hub",
            "derived_data_path": "runs/derived_data", "wda_bundle_id_suffix": "",
        }))
        cfg = load(path)
        self.assertEqual(cfg.wda_bundle_id_suffix, "")
        self.assertEqual(cfg.derived_data_path, "runs/derived_data")
        self.assertIsNone(Config(udid="offline-device").wda_bundle_id_suffix)

    def test_old_app_metadata_list_uses_measured_bundle_identifier(self):
        apps = normalize_app_list([{
            "CFBundleIdentifier": "test.fishing", "CFBundleName": "Fishing Superstars",
        }])
        self.assertEqual(select_fishing_game(apps)[0], "test.fishing")

    def test_ambiguous_game_names_require_explicit_selection(self):
        apps = {name: {"CFBundleDisplayName": "Fishing Superstars"}
                for name in ("test.first", "test.second")}
        with self.assertRaises(LookupError):
            select_fishing_game(apps)
        self.assertEqual(select_fishing_game(apps, "test.second")[0], "test.second")

    def test_permission_and_parameters_fail_before_loading_config_or_connecting(self):
        for overrides, error in (
            ({"armed": False}, PermissionError),
            ({"safe_target_confirmed": False}, PermissionError),
            ({"x": None}, ValueError),
            ({"seconds": float("nan")}, ValueError),
            ({"kind": "drag", "to_x": 30}, ValueError),
            ({"kind": "circle", "radius": 5, "step_seconds": 100}, ValueError),
        ):
            with self.subTest(overrides=overrides), \
                    patch("fishing_probe.cli.make_run_dir", return_value=self.directory), \
                    patch("fishing_probe.cli.load") as load_config, \
                    contextlib.redirect_stdout(io.StringIO()), self.assertRaises(error):
                probe(gesture_args(**overrides))
            load_config.assert_not_called()

    def test_capture_records_pixel_to_point_scale(self):
        facts = capture_screen(FakeDriver(), self.directory / "screen.png")
        self.assertEqual(facts["logical_window_size"], {"width": 100, "height": 200})
        self.assertEqual(facts["screenshot_pixels"], [200, 400])
        self.assertEqual(facts["pixels_per_point"], {"x": 2, "y": 2})

    def test_screenshot_failure_is_not_reported_as_capture(self):
        with self.assertRaises(RuntimeError):
            capture_screen(FakeDriver(save_ok=False), self.directory / "missing.png")

    def test_inspection_keeps_diagnostic_capture_when_game_is_not_frontmost(self):
        driver = FakeDriver(active_bundles=["test.other"])
        with patch("fishing_probe.cli.time.sleep"), self.assertRaises(RuntimeError):
            inspect_current(driver, self.directory)
        self.assertTrue((self.directory / "before.png").exists())
        self.assertTrue((self.directory / "facts.json").exists())
        self.assertFalse(any("touch" in method.lower() for method, _ in driver.calls))

    def test_frontmost_app_is_rechecked_before_any_gesture(self):
        driver = FakeDriver(active_bundles=["test.fishing", "test.other"])
        with patch("fishing_probe.cli.make_run_dir", return_value=self.directory), \
                patch("fishing_probe.cli.load", return_value=Config(udid="offline-device")), \
                patch("fishing_probe.connection.connect", return_value=driver), \
                patch("fishing_probe.cli.time.sleep"), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
            probe(gesture_args())
        self.assertTrue(driver.quit_called)
        self.assertTrue((self.directory / "session.json").exists())
        self.assertFalse(any(method == "mobile: touchAndHold" for method, _ in driver.calls))

    def test_armed_probe_sends_one_request_and_leaves_effect_unverified(self):
        driver = FakeDriver()
        with patch("fishing_probe.cli.make_run_dir", return_value=self.directory), \
                patch("fishing_probe.cli.load", return_value=Config(udid="offline-device")), \
                patch("fishing_probe.connection.connect", return_value=driver), \
                patch("fishing_probe.cli.time.sleep"), contextlib.redirect_stdout(io.StringIO()):
            probe(gesture_args())
        touches = [(method, payload) for method, payload in driver.calls if method == "mobile: touchAndHold"]
        self.assertEqual(len(touches), 1)
        self.assertTrue(driver.quit_called)
        result = json.loads((self.directory / "gesture_result.json").read_text())
        self.assertTrue(result["api_returned"])
        self.assertEqual(result["visual_verification"], "NOT_RUN")
        self.assertTrue((self.directory / "after.png").exists())


if __name__ == "__main__":
    unittest.main()
