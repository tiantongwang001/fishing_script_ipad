import unittest
from dataclasses import replace
from fishing_probe.clicker import load_recipe, run_schedule
from test_clicker import Clock

class ContinueIntervalTests(unittest.TestCase):
    def measure(self, target, repair_cost):
        recipe=load_recipe('clicker_recipe.json')
        recipe=replace(recipe,settings=replace(recipe.settings,cycles=2,
                       max_elapsed_seconds=600,continue_to_cast_seconds=target,repair_every_rounds=1))
        clock=Clock();continued=None;gaps=[]
        def execute(step):
            nonlocal continued
            if step.name in ('continue','startup_continue'):
                clock.sleep(.1)
                continued=clock()
            elif step.name.startswith('repair_'):
                clock.sleep(repair_cost)
            elif step.name=='cast':
                gaps.append(clock()-continued)
        report=run_schedule(recipe,execute,clock=clock,sleep=clock.sleep)
        self.assertEqual(report['status'],'PASS')
        return gaps,report

    def test_total_target_includes_repairs_instead_of_adding_to_them(self):
        gaps,_=self.measure(5,.4)
        for gap in gaps:self.assertAlmostEqual(gap,5)

    def test_slow_repairs_overrun_target_without_extra_wait(self):
        gaps,report=self.measure(1,1)
        self.assertAlmostEqual(gaps[0],1)
        self.assertAlmostEqual(gaps[1],4.8)
        waits=[e for e in report['events'] if e['name']=='continue_to_cast']
        self.assertEqual(waits[1]['seconds'],0)

    def test_zero_target_keeps_existing_timing(self):
        gaps,_=self.measure(0,.4)
        self.assertAlmostEqual(gaps[0],0)
        self.assertAlmostEqual(gaps[1],3)
