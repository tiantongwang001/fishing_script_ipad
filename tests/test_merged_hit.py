"""Offline HIT plus reel sequence validation; no device commands."""
from dataclasses import replace
import unittest
from unittest.mock import patch
from fishing_probe.clicker import load_recipe, build_steps, run_schedule, ClickerSettings
from fishing_probe.gestures import press_batch
from test_press_batch import Driver


class MergedHitTests(unittest.TestCase):
    def test_one_request_releases_hit_before_moving_to_reel(self):
        driver = Driver()
        press_batch(driver,1161,740,.55,20,.05,whole_round=True,
                    initial_press=(1100,740,.1),initial_gap_seconds=.05)
        self.assertEqual(driver.bounds,1)
        self.assertEqual(len(driver.calls),1)
        actions=driver.calls[0][1]['actions'][0]['actions']
        self.assertEqual([a['type'] for a in actions[:7]],
                         ['pointerMove','pointerDown','pause','pointerUp','pause','pointerMove','pointerDown'])
        self.assertEqual((actions[0]['x'],actions[0]['y']),(1100,740))
        self.assertEqual((actions[5]['x'],actions[5]['y']),(1161,740))
        self.assertEqual(actions[2]['duration'],100)
        self.assertEqual(actions[4]['duration'],50)
        self.assertEqual(sum(a['type']=='pointerDown' for a in actions),21)
        self.assertEqual(sum(a['type']=='pointerUp' for a in actions),21)
        self.assertEqual(sum(a.get('duration',0) for a in actions),12100)
        self.assertEqual(actions[-1]['type'],'pointerUp')

    def test_both_points_are_checked_before_any_input(self):
        driver=Driver()
        with self.assertRaises(ValueError):
            press_batch(driver,1161,740,.55,3,.05,initial_press=(1400,740,.1))
        self.assertFalse(driver.calls)
        for gap in (-1,1.01,True,float('nan')):
            with self.assertRaises(ValueError):
                press_batch(driver,1161,740,.55,3,.05,initial_press=(1100,740,.1),initial_gap_seconds=gap)
        self.assertFalse(driver.calls)

    def test_merged_schedule_count_remainder_and_fallback(self):
        base=load_recipe('clicker_recipe.json')
        for grouped,size in ((False,3),(True,3),(True,1)):
            settings=replace(base.settings,cycles=1,reel_pulses=8,merge_hit_reel=True,
                             loggroup=grouped,reel_batch_size=size,hook_settle_seconds=.9)
            recipe=replace(base,settings=settings)
            steps=build_steps(recipe)
            reels=[s for s in steps if s.name=='reel_upper']
            self.assertEqual(sum(s.count for s in reels),8)
            self.assertEqual(reels[0].kind,'hook_batch')
            self.assertEqual(sum(s.kind=='hook_batch' for s in steps),1)
            self.assertFalse(any(s.name in ('hook','hook_settle') for s in steps))
            expected=sum(s.count*base.payload['actions']['reel_upper']['press_seconds'] + (s.count-1)*.05 for s in reels)+.15
            self.assertAlmostEqual(sum(s.seconds for s in reels),expected)
            fallback=build_steps(replace(recipe,settings=replace(settings,merge_hit_reel=False)))
            self.assertEqual(sum(s.name=='hook' for s in fallback),1)
            self.assertFalse(any(s.kind=='hook_batch' for s in fallback))

    def test_deadline_includes_hit_and_transition_and_never_partially_sends(self):
        base=load_recipe('clicker_recipe.json')
        recipe=replace(base,settings=replace(base.settings,merge_hit_reel=True,loggroup=False,
                       reel_pulses=20,cycles=1,max_elapsed_seconds=10))
        batch=next(s for s in build_steps(recipe) if s.kind=='hook_batch')
        calls=[]
        with patch('fishing_probe.clicker.build_steps',return_value=(batch,)):
            result=run_schedule(recipe,calls.append,clock=lambda:0)
        self.assertEqual(result['reason'],'elapsed_limit')
        self.assertFalse(calls)

    def test_switch_and_gap_validation(self):
        for invalid in (0,1,'true',None):
            with self.assertRaises(ValueError):ClickerSettings(merge_hit_reel=invalid)
        for invalid in (-.01,1.01,True,float('inf')):
            with self.assertRaises(ValueError):ClickerSettings(hit_to_reel_seconds=invalid)
        ClickerSettings(hit_to_reel_seconds=0)
