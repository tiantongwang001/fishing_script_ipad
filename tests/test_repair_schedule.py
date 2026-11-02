import json
import tempfile
from pathlib import Path
import unittest
from fishing_probe.clicker import load_recipe, build_steps, geometry_issues, REPAIR_ACTION_NAMES

class RepairScheduleTests(unittest.TestCase):
    def test_repairs_follow_every_fortieth_nonfinal_attempt(self):
        from dataclasses import replace
        recipe=load_recipe('clicker_recipe.json')
        recipe=replace(recipe,settings=replace(recipe.settings,cycles=81,repair_every_rounds=40))
        self.assertEqual(geometry_issues(recipe), [])
        steps=build_steps(recipe)
        for name in REPAIR_ACTION_NAMES:
            self.assertEqual([s.cycle for s in steps if s.name==name],[40,80])
        for cycle in (1,39,40,41,80):
            names=[s.name for s in steps if s.kind=='touch' and s.cycle==cycle]
            self.assertEqual(names[:2],['cast','hook'])
            if cycle in (40,80):
                self.assertEqual(names[-4:],['continue',*REPAIR_ACTION_NAMES])
        recipe=replace(recipe,settings=replace(recipe.settings,repair_every_rounds=0))
        self.assertFalse(any(s.name in REPAIR_ACTION_NAMES for s in build_steps(recipe)))

    def test_final_round_stops_after_sale_even_when_repair_is_due(self):
        from dataclasses import replace
        recipe = load_recipe('clicker_recipe.json')
        for cycles in (1, 40, 80):
            with self.subTest(cycles=cycles):
                configured = replace(recipe, settings=replace(
                    recipe.settings, cycles=cycles, repair_every_rounds=40))
                steps = build_steps(configured)
                final = [step for step in steps if step.cycle == cycles]
                self.assertEqual([(s.kind, s.name) for s in final[-2:]],
                                 [('touch', 'sell'), ('wait', 'final_wait')])
                self.assertFalse(any(s.name in ('continue', 'sell_to_continue',
                                               *REPAIR_ACTION_NAMES) for s in final))
                self.assertEqual(sum(s.name == 'continue' for s in steps), cycles - 1)

    def test_repair_interval_requires_integer_and_success_is_not_an_input(self):
        from fishing_probe.clicker import ClickerSettings
        for value in (-1, True, False, 1.5, 1001):
            with self.assertRaises(ValueError):
                ClickerSettings(repair_every_rounds=value)

    def test_partial_repair_sequence_rejected(self):
        payload=json.loads(Path('clicker_recipe.json').read_text())
        del payload['actions']['repair_confirm']
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'recipe.json';path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(ValueError,'all three'):
                load_recipe(path)
