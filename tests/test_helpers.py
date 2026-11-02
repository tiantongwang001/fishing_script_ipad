import unittest
from fishing_probe.apps import normalize_app_list, select_fishing_game
from fishing_probe.config import Config
from fishing_probe.gestures import circle_points, drag, hold


class DummyDriver:
    def __init__(self):
        self.calls = []

    def execute_script(self, method, payload):
        self.calls.append((method, payload))

    def get_window_size(self):
        return {"width": 1024, "height": 768}


class TestHelpers(unittest.TestCase):
    def test_app_list_documented_map(self):
        data = {"com.example.fishing": {"CFBundleDisplayName": "Fishing Superstars"}}
        found = normalize_app_list(data)
        self.assertEqual(select_fishing_game(found)[0], "com.example.fishing")

    def test_app_list_older_list_form(self):
        data = [{"com.example.fishing": {"CFBundleName": "Fishing Superstars"}}]
        self.assertEqual(len(normalize_app_list(data)), 1)

    def test_circle_has_closure(self):
        points = circle_points(100, 100, 25)
        self.assertEqual(points[0], points[-1])
        self.assertEqual(len(points), 25)

    def test_drag_duration_is_initial_hold(self):
        d = DummyDriver()
        drag(d, 1, 2, 3, 4, 0.7)
        self.assertEqual(d.calls[0][1]["duration"], 0.7)

    def test_hold(self):
        d = DummyDriver()
        hold(d, 3, 4, 0.6)
        self.assertEqual(d.calls[0][0], "mobile: touchAndHold")

    def test_rejects_nonlocalhost(self):
        with self.assertRaises(ValueError):
            Config(udid="device", appium_url="http://0.0.0.0:4723")


if __name__ == "__main__":
    unittest.main()
