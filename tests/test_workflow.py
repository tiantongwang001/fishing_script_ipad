"""Synthetic state-machine tests; never connect to or touch a device."""
import unittest

from fishing_probe.workflow import (
    ACTION_NAMES, Approval, Observation, State, WorkflowConfig, run_workflow,
)


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class TestWorkflow(unittest.TestCase):
    def run_states(self, states, *, approval=None, config=None, execute=None, **kwargs):
        clock = FakeClock()
        observations = iter(states)
        actions = []

        def observe():
            value = next(observations)
            return value if isinstance(value, Observation) else Observation(value)

        outcome = run_workflow(
            observe, execute or actions.append,
            config=config, approval=approval or Approval(ACTION_NAMES),
            clock=clock, sleep=clock.sleep, **kwargs,
        )
        return outcome, actions, clock

    def test_one_cycle_requires_bite_and_visible_result(self):
        outcome, actions, clock = self.run_states([
            State.CAST_READY, State.CAST_READY, State.WAITING_BITE,
            State.WAITING_BITE, State.BITE, State.BITE,
            State.REELING, State.REELING, State.RESULT, State.CAST_READY,
        ])
        self.assertEqual((outcome.status, outcome.reason), ("PASS", "completed"))
        self.assertEqual([a.name for a in actions], ["cast", "hook", "reel_upper", "reel_upper", "continue"])
        self.assertEqual([a.press_seconds for a in actions[2:4]], [0.5, 0.5])
        self.assertEqual(clock.sleeps[-3:-1], [0.05, 0.05])
        self.assertEqual((outcome.cycles_completed, outcome.reel_pulses), (1, 2))

    def test_successful_api_returns_do_not_establish_catch(self):
        outcome, actions, _ = self.run_states([
            State.CAST_READY, State.BITE, State.REELING, State.REELING,
            State.REELING, State.REELING,
        ], config=WorkflowConfig(max_reel_pulses=3))
        self.assertEqual((outcome.status, outcome.reason), ("FAILED", "reel_pulse_limit"))
        self.assertEqual(outcome.cycles_completed, 0)
        self.assertEqual(len([a for a in actions if a.name == "reel_upper"]), 3)

    def test_explicit_stop_at_result_skips_continue(self):
        outcome, actions, _ = self.run_states(
            [State.CAST_READY, State.BITE, State.RESULT],
            config=WorkflowConfig(finish_result_confirmation=False),
        )
        self.assertEqual(outcome.status, "PASS")
        self.assertEqual([a.name for a in actions], ["cast", "hook"])

    def test_final_cleanup_handles_known_popups_without_new_cast(self):
        outcome, actions, _ = self.run_states([
            State.CAST_READY, State.BITE, State.RESULT,
            State.RECORD_POPUP, State.BUFF_POPUP, State.CAST_READY,
        ])
        self.assertEqual((outcome.status, outcome.cycles_completed), ("PASS", 1))
        self.assertEqual([a.name for a in actions], [
            "cast", "hook", "continue", "confirm_record", "confirm_buff",
        ])

    def test_default_completion_requires_observed_ready_screen(self):
        outcome, _, _ = self.run_states([State.CAST_READY, State.BITE, State.RESULT])
        self.assertEqual(outcome.status, "FAILED")
        self.assertEqual(outcome.cycles_completed, 1)

    def test_continue_that_auto_casts_stops_on_unexpected_state(self):
        outcome, actions, _ = self.run_states([
            State.CAST_READY, State.BITE, State.RESULT, State.WAITING_BITE,
        ])
        self.assertEqual(outcome.reason, "unexpected_state")
        self.assertEqual([a.name for a in actions], ["cast", "hook", "continue"])

    def test_two_approved_cycles_handle_known_popups_once(self):
        outcome, actions, _ = self.run_states([
            State.CAST_READY, State.BITE, State.REELING, State.RESULT,
            State.RESULT, State.RECORD_POPUP, State.RECORD_POPUP,
            State.BUFF_POPUP, State.BUFF_POPUP, State.RESULT,
            State.CAST_READY, State.WAITING_BITE, State.BITE, State.RESULT, State.CAST_READY,
        ], config=WorkflowConfig(max_cycles=2), approval=Approval(ACTION_NAMES, max_cycles=2))
        self.assertEqual((outcome.status, outcome.cycles_completed), ("PASS", 2))
        self.assertEqual([a.name for a in actions], [
            "cast", "hook", "reel_upper", "continue", "confirm_record",
            "confirm_buff", "cast", "hook", "continue",
        ])

    def test_no_approval_never_observes_or_touches(self):
        def forbidden():
            self.fail("No observation or execution should occur without approval")
        outcome = run_workflow(forbidden, forbidden)
        self.assertEqual((outcome.status, outcome.reason), ("BLOCKED_USER_ACTION", "approval_required"))

    def test_approval_limits_checked_before_any_input(self):
        for config in (WorkflowConfig(max_cycles=2), WorkflowConfig(max_reel_pulses=11)):
            with self.subTest(config=config):
                outcome, actions, _ = self.run_states([], config=config)
                self.assertEqual(outcome.reason, "approval_limit_exceeded")
                self.assertEqual(actions, [])

    def test_missing_action_approval_blocks_before_action(self):
        outcome, actions, _ = self.run_states(
            [State.CAST_READY, State.BITE], approval=Approval(frozenset({"cast"})),
        )
        self.assertEqual((outcome.status, outcome.reason, outcome.detail),
                         ("BLOCKED_USER_ACTION", "action_not_approved", "hook"))
        self.assertEqual([a.name for a in actions], ["cast"])

    def test_unknown_and_ambiguous_screens_never_receive_fallback_tap(self):
        for observation, reason in [
            (Observation(State.UNKNOWN, "unrecognized purchase prompt"), "unknown_state"),
            (Observation(State.BITE, unambiguous=False), "ambiguous_state"),
            (Observation(State.BITE, unambiguous=1), "ambiguous_state"),
        ]:
            with self.subTest(observation=observation):
                outcome, actions, _ = self.run_states(
                    [State.CAST_READY, observation], config=WorkflowConfig(transition_grace_seconds=0),
                )
                self.assertEqual(outcome.reason, reason)
                self.assertEqual([a.name for a in actions], ["cast"])

    def test_no_hook_without_bite_and_no_catch_from_old_result(self):
        for states in ([State.REELING], [State.RESULT],
                       [State.CAST_READY, State.RESULT],
                       [State.CAST_READY, State.WAITING_BITE, State.REELING]):
            with self.subTest(states=states):
                outcome, actions, _ = self.run_states(states)
                self.assertEqual(outcome.reason, "unexpected_state")
                self.assertEqual(outcome.cycles_completed, 0)
                self.assertNotIn("hook", [a.name for a in actions])

    def test_known_popup_outside_result_cleanup_is_not_dismissed(self):
        for state in (State.RECORD_POPUP, State.BUFF_POPUP):
            with self.subTest(state=state):
                outcome, actions, _ = self.run_states([state])
                self.assertEqual(outcome.reason, "unexpected_state")
                self.assertEqual(actions, [])

    def test_popup_total_limit(self):
        outcome, actions, _ = self.run_states([
            State.CAST_READY, State.BITE, State.RESULT,
            State.RECORD_POPUP, State.BUFF_POPUP,
        ], config=WorkflowConfig(max_cycles=2, max_popup_actions=1),
            approval=Approval(ACTION_NAMES, max_cycles=2))
        self.assertEqual(outcome.reason, "popup_action_limit")
        self.assertEqual(actions[-1].name, "confirm_record")

    def test_timeout_while_waiting_does_not_recast_or_hook(self):
        outcome, actions, clock = self.run_states(
            [State.CAST_READY] + [State.WAITING_BITE] * 20,
            config=WorkflowConfig(max_elapsed_seconds=1),
        )
        self.assertEqual(outcome.reason, "elapsed_limit")
        self.assertEqual([a.name for a in actions], ["cast"])
        self.assertEqual(clock.now, 1)

    def test_slow_observer_cannot_inject_input_past_deadline(self):
        clock = FakeClock()
        actions = []

        def observe():
            clock.now += 91
            return Observation(State.CAST_READY)

        outcome = run_workflow(observe, actions.append, approval=Approval(ACTION_NAMES),
                               clock=clock, sleep=clock.sleep)
        self.assertEqual(outcome.reason, "elapsed_limit")
        self.assertEqual(actions, [])

    def test_cancel_after_observation_prevents_action(self):
        cancellation = [False]
        actions = []

        def observe():
            cancellation[0] = True
            return Observation(State.CAST_READY)

        outcome = run_workflow(observe, actions.append, approval=Approval(ACTION_NAMES),
                               cancelled=lambda: cancellation[0])
        self.assertEqual(outcome.reason, "cancelled")
        self.assertEqual(actions, [])

    def test_finite_observation_limit_even_with_broken_injected_clock(self):
        outcome = run_workflow(
            lambda: Observation(State.CAST_READY), lambda action: None,
            approval=Approval(ACTION_NAMES), config=WorkflowConfig(max_observations=3),
            clock=lambda: 0, sleep=lambda seconds: None,
        )
        self.assertEqual(outcome.reason, "observation_limit")
        self.assertEqual([a.name for a in outcome.actions], ["cast"])

    def test_cancellation_after_attempted_touch_is_not_reported_not_run(self):
        cancellation = [False]

        def execute(action):
            cancellation[0] = True

        outcome, _, _ = self.run_states([State.CAST_READY], execute=execute,
                                       cancelled=lambda: cancellation[0])
        self.assertEqual((outcome.status, outcome.reason), ("FAILED", "cancelled"))
        self.assertEqual([a.name for a in outcome.actions], ["cast"])

    def test_action_exception_is_not_retried_or_counted_as_success(self):
        attempts = []

        def execute(action):
            attempts.append(action)
            raise RuntimeError("connection lost; input delivery unknown")

        outcome, _, _ = self.run_states([State.CAST_READY], execute=execute)
        self.assertEqual(outcome.reason, "action_error")
        self.assertEqual(len(attempts), 1)
        self.assertEqual(outcome.cycles_completed, 0)
        self.assertEqual(outcome.actions, tuple(attempts))

    def test_observer_exception_and_invalid_output_stop(self):
        outcome, actions, _ = self.run_states([])
        self.assertEqual(outcome.reason, "observer_error")
        self.assertEqual(actions, [])
        outcome = run_workflow(lambda: "bite", lambda action: self.fail("unexpected touch"),
                               approval=Approval(ACTION_NAMES))
        self.assertEqual(outcome.reason, "invalid_observation")

    def test_invalid_limits_and_action_names_rejected_offline(self):
        for kwargs in (
            {"max_cycles": 0}, {"max_cycles": True}, {"max_cycles": 11},
            {"max_elapsed_seconds": float("nan")}, {"max_elapsed_seconds": 601},
            {"max_reel_pulses": 0}, {"max_reel_pulses": 121},
            {"poll_seconds": 0}, {"reel_press_seconds": 1.1},
            {"reel_release_seconds": float("inf")}, {"max_popup_actions": -1},
            {"max_observations": 0},
            {"finish_result_confirmation": 1},
            {"sell_catches": 1}, {"sell_catches": True, "max_cycles": 2},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                WorkflowConfig(**kwargs)
        for actions in ({"cast"}, frozenset({"buy"}), frozenset({"repair"})):
            with self.subTest(actions=actions), self.assertRaises(ValueError):
                Approval(actions)

    def test_sale_requires_config_and_explicit_action_approval(self):
        states = [State.CAST_READY, State.BITE, State.SALE_OFFER]
        outcome, actions, _ = self.run_states(states)
        self.assertEqual(outcome.reason, "sale_not_enabled")
        self.assertEqual([a.name for a in actions], ["cast", "hook"])
        outcome, actions, _ = self.run_states(
            states, config=WorkflowConfig(sell_catches=True),
            approval=Approval(ACTION_NAMES - {"sell"}),
        )
        self.assertEqual((outcome.reason, outcome.detail), ("action_not_approved", "sell"))
        self.assertEqual([a.name for a in actions], ["cast", "hook"])

    def test_sale_is_once_and_catch_count_does_not_double(self):
        outcome, actions, _ = self.run_states([
            State.CAST_READY, State.BITE, State.REELING, State.SALE_OFFER,
            State.SALE_OFFER, State.SALE_PENDING, State.SALE_PENDING,
            State.RESULT, State.RESULT, State.CAST_READY,
        ], config=WorkflowConfig(sell_catches=True))
        self.assertEqual((outcome.status, outcome.cycles_completed), ("PASS", 1))
        self.assertEqual([a.name for a in actions], ["cast", "hook", "reel_upper", "sell", "continue"])

    def test_old_sale_offer_or_unexpected_sale_pending_cannot_sell(self):
        for state in (State.SALE_OFFER, State.SALE_PENDING):
            with self.subTest(state=state):
                outcome, actions, _ = self.run_states([state], config=WorkflowConfig(sell_catches=True))
                self.assertEqual(outcome.reason, "unexpected_state")
                self.assertEqual(actions, [])

    def test_transition_grace_observes_animation_without_more_input(self):
        outcome, actions, _ = self.run_states([
            State.CAST_READY, State.UNKNOWN, State.UNKNOWN, State.WAITING_BITE,
            State.BITE, State.UNKNOWN, State.REELING, State.LANDING,
            State.LANDING, State.RESULT, State.UNKNOWN, State.CAST_READY,
        ])
        self.assertEqual(outcome.status, "PASS")
        self.assertEqual([a.name for a in actions], ["cast", "hook", "reel_upper", "continue"])

    def test_transition_grace_expires_and_unknown_during_reeling_stops(self):
        outcome, actions, _ = self.run_states(
            [State.CAST_READY] + [State.UNKNOWN] * 10,
            config=WorkflowConfig(transition_grace_seconds=0.5),
        )
        self.assertEqual(outcome.reason, "unknown_state")
        self.assertEqual([a.name for a in actions], ["cast"])
        outcome, actions, _ = self.run_states([
            State.CAST_READY, State.BITE, State.REELING, State.UNKNOWN,
        ])
        self.assertEqual(outcome.reason, "unknown_state")
        self.assertEqual([a.name for a in actions], ["cast", "hook", "reel_upper"])

    def test_ambiguity_never_gets_animation_grace(self):
        outcome, actions, _ = self.run_states([
            State.CAST_READY, Observation(State.UNKNOWN, unambiguous=False),
        ])
        self.assertEqual(outcome.reason, "ambiguous_state")
        self.assertEqual([a.name for a in actions], ["cast"])

    def test_default_ten_pulses_is_a_cap_and_stops_early_on_landing(self):
        self.assertEqual(WorkflowConfig().max_reel_pulses, 10)
        self.assertEqual(Approval(ACTION_NAMES).max_reel_pulses, 10)
        outcome, actions, _ = self.run_states(
            [State.CAST_READY, State.BITE] + [State.REELING] * 11,
        )
        self.assertEqual(outcome.reason, "reel_pulse_limit")
        self.assertEqual(sum(a.name == "reel_upper" for a in actions), 10)
        outcome, actions, _ = self.run_states([
            State.CAST_READY, State.BITE, State.REELING, State.LANDING,
            State.RESULT, State.CAST_READY,
        ])
        self.assertEqual(outcome.status, "PASS")
        self.assertEqual(sum(a.name == "reel_upper" for a in actions), 1)

    def test_landing_animation_grace_sends_no_input_and_does_not_extend(self):
        outcome, actions, _ = self.run_states([
            State.CAST_READY, State.BITE, State.REELING, State.LANDING,
            State.UNKNOWN, State.UNKNOWN, State.RESULT, State.CAST_READY,
        ])
        self.assertEqual(outcome.status, "PASS")
        self.assertEqual([a.name for a in actions], ["cast", "hook", "reel_upper", "continue"])
        outcome, actions, _ = self.run_states([
            State.CAST_READY, State.BITE, State.REELING, State.LANDING,
            State.LANDING, State.LANDING, State.UNKNOWN,
        ], config=WorkflowConfig(transition_grace_seconds=.5))
        self.assertEqual(outcome.reason, "unknown_state")
        self.assertEqual([a.name for a in actions], ["cast", "hook", "reel_upper"])


if __name__ == "__main__":
    unittest.main()
