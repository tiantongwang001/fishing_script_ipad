"""Appium XCUITest driver setup: real device; no game data reset."""
from __future__ import annotations

from pathlib import Path
import math

from appium import webdriver
from appium.options.ios import XCUITestOptions

from fishing_probe.config import Config


def connect(cfg: Config, *, request_timeout_seconds: float | None = None,
            session_timeout_seconds: float = 180):
    if not cfg.udid.strip():
        raise ValueError("A real measured iPad UDID is required in config.json")
    options = XCUITestOptions()
    options.platform_name = "iOS"
    options.automation_name = "XCUITest"
    options.udid = cfg.udid
    options.no_reset = True
    options.full_reset = False
    options.set_capability("appium:shouldTerminateApp", False)
    options.set_capability("appium:forceAppLaunch", False)
    options.set_capability("appium:useNewWDA", False)
    options.set_capability("appium:showXcodeLog", True)
    options.set_capability("appium:newCommandTimeout", 180)
    # Without app/bundleId the XCUITest session starts at the Home screen.
    # First enumerate installed apps and identify the game by real metadata.
    if cfg.wda_bundle_id:
        options.set_capability("appium:updatedWDABundleId", cfg.wda_bundle_id)
    if cfg.derived_data_path:
        options.set_capability("appium:derivedDataPath", str(Path(cfg.derived_data_path).resolve()))
    if cfg.wda_mode == "preinstalled":
        if not cfg.wda_bundle_id:
            raise ValueError("Preinstalled WDA requires its verified bundle ID")
        options.set_capability("appium:usePreinstalledWDA", True)
        if cfg.wda_bundle_id_suffix is not None:
            options.set_capability("appium:updatedWDABundleIdSuffix", cfg.wda_bundle_id_suffix)
    if (isinstance(session_timeout_seconds, bool)
            or not isinstance(session_timeout_seconds, (int, float))
            or not math.isfinite(session_timeout_seconds)
            or not 1 <= session_timeout_seconds <= 300):
        raise ValueError("Session timeout must be finite seconds between 1 and 300")
    kwargs = {}
    if request_timeout_seconds is not None:
        if (isinstance(request_timeout_seconds, bool)
                or not isinstance(request_timeout_seconds, (int, float))
                or not math.isfinite(request_timeout_seconds)
                or not 1 <= request_timeout_seconds <= 300):
            raise ValueError("Request timeout must be finite seconds between 1 and 300")
        from appium.webdriver.client_config import AppiumClientConfig
        # Explicitly disable HTTP retries: delivery may be uncertain after error.
        kwargs["client_config"] = AppiumClientConfig(
            cfg.appium_url, timeout=max(session_timeout_seconds, request_timeout_seconds),
            init_args_for_pool_manager={"init_args_for_pool_manager": {"retries": False}},
        )
    # Session creation may build/launch WDA; it is not a timed game input.
    driver = webdriver.Remote(cfg.appium_url, options=options, **kwargs)
    if request_timeout_seconds is not None:
        # The connection retains this instance; subsequent requests use the
        # bounded action timeout. Never retry an uncertain input or session.
        kwargs["client_config"].timeout = request_timeout_seconds
    return driver
