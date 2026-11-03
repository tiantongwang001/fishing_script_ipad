"""Bounded W3C repeat tests, with no device access."""
from dataclasses import replace
import unittest
from fishing_probe.gestures import press_batch
from fishing_probe.clicker import load_recipe, build_steps, run_schedule, ClickerSettings


class Driver:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail
        self.bounds = 0

    def get_window_size(self):
        self.bounds += 1
        return {'width': 1366, 'height': 1024}

    def execute(self, command, params=None):
        self.calls.append((command, params))
        if self.fail and command == 'actions':
            raise RuntimeError('uncertain delivery')


class TestPressBatch(unittest.TestCase):
    def test_one_request_contains_three_distinct_contacts_and_two_gaps(self):
        driver = Driver()
        press_batch(driver, 1161, 740, .55, 3, .05)
        self.assertEqual(driver.bounds, 1)
        self.assertEqual(len(driver.calls), 1)
        name, params = driver.calls[0]
        self.assertEqual(name, 'actions')
        actions = params['actions'][0]['actions']
        self.assertEqual(sum(a['type'] == 'pointerDown' for a in actions), 3)
        self.assertEqual(sum(a['type'] == 'pointerUp' for a in actions), 3)
        self.assertEqual([a['duration'] for a in actions if a['type'] == 'pause'],
                         [550, 50, 550, 50, 550])
        self.assertEqual(actions[-1]['type'], 'pointerUp')
        self.assertTrue(all(a['duration'] == 0 for a in actions if a['type'] == 'pointerMove'))

    def test_invalid_batch_rejected_and_changed_bounds_send_nothing(self):
        for count, gap in ((0,.05),(4,.05),(True,.05),(2,0),(2,float('nan'))):
            driver = Driver()
            with self.assertRaises(ValueError):
                press_batch(driver, 1161, 740, .55, count, gap)
            self.assertFalse(driver.calls)
        driver = Driver()
        with self.assertRaises(ValueError):
            press_batch(driver, 1161, 740, .55, 3, .05,
                        expected_window={'width': 1024, 'height': 1366})
        self.assertFalse(driver.calls)

    def test_unknown_delivery_releases_without_retry(self):
        driver = Driver(fail=True)
        with self.assertRaisesRegex(RuntimeError, 'uncertain delivery'):
            press_batch(driver, 1161, 740, .55, 3, .05)
        self.assertEqual([c[0] for c in driver.calls], ['actions', 'clearActionState'])

    def test_deadline_guard_runs_after_bounds_before_dispatch(self):
        driver = Driver()
        def stop():
            self.assertEqual(driver.bounds, 1)
            raise RuntimeError('deadline')
        with self.assertRaisesRegex(RuntimeError, 'deadline'):
            press_batch(driver, 1161, 740, .55, 3, .05, before_dispatch=stop)
        self.assertFalse(driver.calls)

    def test_schedule_preserves_remainder_and_budget_includes_whole_batch(self):
        recipe = load_recipe('clicker_recipe.json')
        recipe = replace(recipe, settings=replace(recipe.settings,
                         cycles=1, reel_pulses=8, reel_batch_size=3, reel_batch_gap_seconds=.05))
        batches = [s for s in build_steps(recipe) if s.kind == 'batch']
        self.assertEqual([s.count for s in batches], [3,3,2])
        self.assertEqual([s.pulse for s in batches], [1,4,7])
        hold = recipe.payload['actions']['reel_upper']['press_seconds']
        self.assertAlmostEqual(batches[0].seconds, 3*hold+.1)
        from unittest.mock import patch
        executed = []
        short = replace(recipe, settings=replace(recipe.settings, max_elapsed_seconds=1))
        with patch('fishing_probe.clicker.build_steps', return_value=(batches[0],)):
            result = run_schedule(short, executed.append, clock=lambda:0)
        self.assertEqual(result['reason'], 'elapsed_limit')
        self.assertFalse(executed)

    def test_settings_limits(self):
        for value in (0,4,True,2.5):
            with self.assertRaises(ValueError):
                ClickerSettings(reel_batch_size=value)
