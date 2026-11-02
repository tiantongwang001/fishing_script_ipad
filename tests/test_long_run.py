import unittest
from dataclasses import replace
from fishing_probe.clicker import ClickerSettings, load_recipe, run_schedule
from test_clicker import Clock

class LongRunTests(unittest.TestCase):
    def test_larger_finite_limits(self):
        settings=ClickerSettings(cycles=1000,max_elapsed_seconds=86400)
        self.assertEqual(settings.cycles,1000)
        for values in ({'cycles':1001},{'max_elapsed_seconds':86401},
                       {'max_elapsed_seconds':float('inf')}):
            with self.assertRaises(ValueError):
                ClickerSettings(**values)

    def test_one_hour_deadline_still_stops_long_schedule(self):
        recipe=load_recipe('clicker_recipe.json')
        recipe=replace(recipe,settings=replace(recipe.settings,cycles=1000,max_elapsed_seconds=3600))
        clock=Clock()
        report=run_schedule(recipe,lambda step: clock.sleep(step.seconds+.5),
                            clock=clock,sleep=clock.sleep)
        self.assertEqual(report['reason'],'elapsed_limit')
        self.assertLess(report['cycles_api_completed'],1000)
        # Stops slightly early if the next touch cannot fit before the deadline.
        self.assertGreaterEqual(report['elapsed_seconds'],3599)
        self.assertLess(report['elapsed_seconds'],3602)
