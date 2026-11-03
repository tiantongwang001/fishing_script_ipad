"""Whole-round batching and optional competition confirmation: offline only."""
from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
import run_fishing
from fishing_probe.clicker import load_recipe, build_steps, run_schedule, geometry_issues, ClickerSettings
from fishing_probe.gestures import press_batch
from test_press_batch import Driver


class RoundGroupTests(unittest.TestCase):
    def recipe(self, **settings):
        recipe = load_recipe('clicker_recipe.json')
        return replace(recipe, settings=replace(recipe.settings, cycles=1, **settings))

    def test_whole_round_has_no_intermediate_wait_or_extra_request(self):
        recipe = self.recipe(loggroup=False, reel_pulses=25, reel_release_seconds=.9)
        steps = build_steps(recipe)
        batches = [s for s in steps if s.name == 'reel_upper']
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0].count, 25)
        hold = recipe.payload['actions']['reel_upper']['press_seconds']
        self.assertAlmostEqual(batches[0].seconds, 25*hold+24*.05)
        self.assertFalse(any(s.name == 'reel_release' for s in steps))
        driver = Driver()
        press_batch(driver, 1161, 740, hold, 25, .05, whole_round=True)
        self.assertEqual(driver.bounds, 1)
        self.assertEqual(len(driver.calls), 1)
        actions = driver.calls[0][1]['actions'][0]['actions']
        self.assertEqual(sum(a['type']=='pointerDown' for a in actions),25)
        self.assertEqual(sum(a['type']=='pointerUp' for a in actions),25)
        self.assertEqual(sum(a['type']=='pause' and a['duration']==50 for a in actions),24)

    def test_whole_request_must_fit_remaining_deadline(self):
        recipe = self.recipe(loggroup=False, reel_pulses=25, max_elapsed_seconds=5)
        batch = next(s for s in build_steps(recipe) if s.kind == 'batch')
        sent=[]
        with patch('fishing_probe.clicker.build_steps', return_value=(batch,)):
            result=run_schedule(recipe,sent.append,clock=lambda:0)
        self.assertEqual(result['reason'],'elapsed_limit')
        self.assertFalse(sent)

    def test_whole_round_maximum_and_strict_switches(self):
        for field in ('loggroup','competetion'):
            for invalid in (0,1,'false',None):
                with self.assertRaises(ValueError): ClickerSettings(**{field:invalid})
        with self.assertRaises(ValueError):
            press_batch(Driver(),100,100,.55,121,.05,whole_round=True)
        with self.assertRaises(ValueError):
            press_batch(Driver(),100,100,.55,25,.05)
        press_batch(Driver(),100,100,1,120,1,whole_round=True)

    def test_competition_precedes_buff_only_when_enabled(self):
        for enabled in (True,False):
            recipe=self.recipe(competetion=enabled)
            self.assertEqual(geometry_issues(recipe),[])
            steps=build_steps(recipe)
            names=[s.name for s in steps]
            if enabled:
                pos=names.index('confirm_competition')
                self.assertEqual(names[pos-1:pos+3],['result_wait','confirm_competition','competition_to_buff','confirm_popup'])
                self.assertEqual(steps[pos+1].seconds,.6)
            else:
                self.assertNotIn('confirm_competition',names)
                self.assertNotIn('competition_to_buff',names)

    def test_missing_competition_coordinate_fails_loading(self):
        payload=json.loads(Path('clicker_recipe.json').read_text())
        payload['settings']['competetion']=True
        del payload['actions']['confirm_competition']
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'recipe.json';p.write_text(json.dumps(payload))
            with self.assertRaisesRegex(ValueError,'requires confirm_competition'):load_recipe(p)

    def test_launcher_forwards_both_switches(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'clicker_recipe.json').write_bytes(Path('clicker_recipe.json').read_bytes())
            for grouped in (True,False):
                for competition in (True,False):
                    with patch.object(run_fishing,'PROJECT_ROOT',root), patch.object(run_fishing,'loggroup',grouped), patch.object(run_fishing,'competetion',competition):
                        recipe=load_recipe(run_fishing.prepare_default_recipe())
                    self.assertEqual(recipe.settings.loggroup,grouped)
                    self.assertEqual(recipe.settings.competetion,competition)
                    self.assertEqual('confirm_competition' in recipe.payload['actions'],competition)
                    self.assertEqual(geometry_issues(recipe),[])
