import unittest
from pathlib import Path
from dataclasses import replace
from fishing_probe.clicker import load_recipe, build_steps, geometry_issues, run_schedule
from test_clicker import Clock

class StartupContinueTests(unittest.TestCase):
    def setUp(self):
        self.recipe = load_recipe(Path('clicker_recipe.json'))
        self.recipe = replace(self.recipe, settings=replace(
            self.recipe.settings, cycles=2, max_elapsed_seconds=600))

    def test_startup_once_and_not_counted_as_round(self):
        steps = build_steps(self.recipe)
        self.assertEqual((steps[0].name, steps[0].cycle), ('startup_continue', 0))
        self.assertEqual(steps[1].name, 'initial_delay')
        self.assertEqual(sum(s.name == 'startup_continue' for s in steps), 1)
        clock = Clock()
        report = run_schedule(self.recipe, lambda s: None, clock=clock, sleep=clock.sleep)
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['cycles_api_completed'], 2)

    def test_startup_is_inside_continue_and_outside_cast_with_guard(self):
        self.assertEqual(geometry_issues(self.recipe), [])
        target = self.recipe.payload['actions']['startup_continue']
        x,y = target['x'],target['y']
        controls={c['id']:c['bounds'] for c in self.recipe.payload['button_geometry']['controls']}
        b=controls['sell_continue'];cast=controls['cast_hit']
        self.assertTrue(b[0]+12 <= x <= b[2]-12 and b[1]+12 <= y <= b[3]-12)
        self.assertFalse(cast[0]-12 <= x <= cast[2]+12 and cast[1]-12 <= y <= cast[3]+12)

    def test_uncertain_startup_delivery_stops_before_cast(self):
        names=[]
        def execute(step):
            names.append(step.name)
            raise RuntimeError('delivery unknown')
        clock=Clock()
        report=run_schedule(self.recipe, execute, clock=clock, sleep=clock.sleep)
        self.assertEqual(report['status'], 'FAILED')
        self.assertEqual(names, ['startup_continue'])
