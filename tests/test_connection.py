import unittest
from unittest.mock import patch

from fishing_probe.config import Config
from fishing_probe.connection import connect


class TestConnection(unittest.TestCase):
    @patch("fishing_probe.connection.webdriver.Remote")
    def test_preserves_existing_game_and_wda(self, remote):
        connect(Config(udid="measured-device", derived_data_path="runs/wda-derived-data"))
        caps = remote.call_args.kwargs["options"].to_capabilities()
        self.assertTrue(caps["appium:noReset"])
        for name in ("fullReset", "shouldTerminateApp", "forceAppLaunch", "useNewWDA"):
            self.assertFalse(caps["appium:" + name])
        for name in ("app", "bundleId", "usePreinstalledWDA"):
            self.assertNotIn("appium:" + name, caps)
        self.assertTrue(caps["appium:derivedDataPath"].endswith("/runs/wda-derived-data"))

    @patch("fishing_probe.connection.webdriver.Remote")
    def test_preserves_explicit_empty_preinstalled_suffix(self, remote):
        connect(Config(udid="measured-device", wda_mode="preinstalled",
                       wda_bundle_id="verified.example.Runner.xctrunner", wda_bundle_id_suffix=""))
        caps = remote.call_args.kwargs["options"].to_capabilities()
        self.assertTrue(caps["appium:usePreinstalledWDA"])
        self.assertEqual(caps["appium:updatedWDABundleIdSuffix"], "")

    @patch("fishing_probe.connection.webdriver.Remote")
    def test_missing_device_never_connects(self, remote):
        with self.assertRaises(ValueError):
            connect(Config(udid=""))
        remote.assert_not_called()

    @patch("fishing_probe.connection.webdriver.Remote")
    def test_preinstalled_requires_verified_bundle(self, remote):
        with self.assertRaises(ValueError):
            connect(Config(udid="measured-device", wda_mode="preinstalled"))
        remote.assert_not_called()
