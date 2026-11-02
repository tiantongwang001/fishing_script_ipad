"""Bounded fishing state machine; no device connection or screen recognition.

The observer must identify the current screen from fresh device evidence. The
executor maps an approved action name to already calibrated logical coordinates
and must release its touch before returning. Nothing here grants permission to
touch the game, recognizes a bobber, or establishes timing accuracy on an iPad.
"""
from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from numbers import Real


class State(str, Enum):
    CAST_READY = "cast_ready"
    WAITING_BITE = "waiting_bite"
    BITE = "bite"
    REELING = "reeling"
    LANDING = "landing"
    RESULT = "result"
    SALE_OFFER = "sale_offer"
    SALE_PENDING = "sale_pending"
    RECORD_POPUP = "record_popup"
    BUFF_POPUP = "buff_popup"
    UNKNOWN = "unknown"


ACTION_NAMES = frozenset({
    "cast", "hook", "reel_upper", "continue", "confirm_record", "confirm_buff", "sell",
})


@dataclass(frozen=True)
class Observation:
    state: State
    evidence: str = ""
    unambiguous: bool = True


def _integer(value: int, minimum: int, maximum: int, name: str) -> None:
    if (isinstance(value, bool) or not isinstance(value, int)
            or not minimum <= value <= maximum):
        raise ValueError(f"{name} must be an integer in {minimum}–{maximum}")


def _seconds(value: float, minimum: float, maximum: float, name: str) -> None:
    if (isinstance(value, bool) or not isinstance(value, Real)
            or not math.isfinite(value) or not minimum <= value <= maximum):
        raise ValueError(f"{name} must be finite seconds in {minimum}–{maximum}")


@dataclass(frozen=True)
class WorkflowConfig:
    max_cycles: int = 1
    max_elapsed_seconds: float = 90
    max_reel_pulses: int = 10
    poll_seconds: float = 0.2
    reel_press_seconds: float = 0.5
    reel_release_seconds: float = 0.05
    finish_result_confirmation: bool = True
    sell_catches: bool = False
    transition_grace_seconds: float = 5
    max_popup_actions: int = 3
    max_observations: int = 600

    def __post_init__(self) -> None:
        _integer(self.max_cycles, 1, 10, "max_cycles")
        _integer(self.max_reel_pulses, 1, 120, "max_reel_pulses")
        _integer(self.max_popup_actions, 0, 20, "max_popup_actions")
        _integer(self.max_observations, 1, 6000, "max_observations")
        _seconds(self.max_elapsed_seconds, 1, 600, "max_elapsed_seconds")
        _seconds(self.poll_seconds, 0.02, 2, "poll_seconds")
        _seconds(self.reel_press_seconds, 0.02, 1, "reel_press_seconds")
        _seconds(self.reel_release_seconds, 0.02, 2, "reel_release_seconds")
        _seconds(self.transition_grace_seconds, 0, 10, "transition_grace_seconds")
        if not isinstance(self.finish_result_confirmation, bool):
            raise ValueError("finish_result_confirmation must be boolean")
        if not isinstance(self.sell_catches, bool):
            raise ValueError("sell_catches must be boolean")
        if self.sell_catches and self.max_cycles != 1:
            raise ValueError("Sale permission is limited to this one round; max_cycles must be 1")


@dataclass(frozen=True)
class Approval:
    """Run-specific approval supplied only after the user approves exact targets.

    The caller remains responsible for recording the actual user approval and
    binding these names to that reviewed coordinate mapping. This object is a
    guard, not independent evidence of consent. Broader limits need new explicit
    approval. Ten pulses is the user's requested workflow cap; it does not
    authorize execution or change the separate initial single-probe allowance.
    """

    allowed_actions: frozenset[str]
    max_cycles: int = 1
    max_reel_pulses: int = 10

    def __post_init__(self) -> None:
        if (not isinstance(self.allowed_actions, frozenset)
                or not self.allowed_actions <= ACTION_NAMES):
            raise ValueError("allowed_actions must be a frozenset of known action names")
        _integer(self.max_cycles, 1, 10, "approved max_cycles")
        _integer(self.max_reel_pulses, 1, 120, "approved max_reel_pulses")


@dataclass(frozen=True)
class ActionRequest:
    name: str
    press_seconds: float = 0


@dataclass(frozen=True)
class Event:
    kind: str
    value: str
    elapsed_seconds: float
    evidence: str = ""


@dataclass(frozen=True)
class Outcome:
    status: str
    reason: str
    cycles_completed: int
    reel_pulses: int
    actions: tuple[ActionRequest, ...]
    events: tuple[Event, ...]
    detail: str = ""


@dataclass
class _Progress:
    cycles: int = 0
    pulses: int = 0
    popups: int = 0
    phase: str = "ready"
    last_popup: State | None = None
    transition_until: float = 0
    actions: list[ActionRequest] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)


def run_workflow(
    observe: Callable[[], Observation],
    execute: Callable[[ActionRequest], None],
    *,
    config: WorkflowConfig | None = None,
    approval: Approval | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    cancelled: Callable[[], bool] = lambda: False,
) -> Outcome:
    """Run only within approved bounds and stop on uncertain observations.

    Cast and hook are never repeated on an unchanged screen. Hook requires an
    observed BITE. A short REELING press always returns with contact released;
    the next pulse requires a new REELING observation. Release time is a minimum
    and includes additional observation/transport latency, not a real-time claim.

    A cycle counts only when RESULT or SALE_OFFER is observed after this run's
    hook. Selling additionally requires sell_catches=True and separate approval
    of the sell action, and is limited to one round. A sale is sent only once;
    only the subsequent RESULT/Continue label permits confirmation. By default
    completion also requires an approved Continue Fishing action, confirmation
    of any recognized record/buff popups, and observation of CAST_READY. No new
    cast is sent at the cycle limit. If Continue Fishing starts a new cast itself,
    that unexpected transition stops the run. Set finish_result_confirmation to
    False to stop at the final RESULT without confirmation. Each unchanged popup
    is acted on only once. Other popup/screens must be reported as UNKNOWN.

    The elapsed limit is checked around observer/executor calls. The injected
    functions must enforce their own transport timeouts: Python cannot interrupt
    an indefinitely blocked Appium request here. Errors are never retried.
    After cast/hook/sell/continue or first recognized LANDING, an unmatched animation may be observed without
    input for transition_grace_seconds. Ambiguity always stops. Unknown screens
    while reeling stop immediately; recognized LANDING waits without input.
    """
    config = config or WorkflowConfig()
    progress = _Progress()
    started = clock()

    def elapsed() -> float:
        return max(0.0, clock() - started)

    def finish(status: str, reason: str, detail: str = "") -> Outcome:
        return Outcome(status, reason, progress.cycles, progress.pulses,
                       tuple(progress.actions), tuple(progress.events), detail)

    def stop_requested() -> Outcome | None:
        if cancelled():
            return finish("FAILED" if progress.actions else "NOT_RUN", "cancelled")
        if elapsed() >= config.max_elapsed_seconds:
            return finish("FAILED", "elapsed_limit")
        return None

    def send(name: str) -> Outcome | None:
        if stopped := stop_requested():
            return stopped
        if name not in approval.allowed_actions:
            return finish("BLOCKED_USER_ACTION", "action_not_approved", name)
        request = ActionRequest(name, config.reel_press_seconds if name == "reel_upper" else 0)
        # Record attempted input before the call: exceptions leave delivery unknown.
        progress.actions.append(request)
        progress.events.append(Event("action_attempt", name, elapsed()))
        if name == "reel_upper":
            progress.pulses += 1
        try:
            execute(request)
        except Exception as exc:
            return finish("FAILED", "action_error", f"{type(exc).__name__}: {exc}")
        if name in {"cast", "hook", "sell", "continue"}:
            progress.transition_until = elapsed() + config.transition_grace_seconds
        return stop_requested()

    def pause(seconds: float) -> Outcome | None:
        if stopped := stop_requested():
            return stopped
        remaining = config.max_elapsed_seconds - elapsed()
        if remaining <= 0:
            return finish("FAILED", "elapsed_limit")
        sleep(min(seconds, remaining))
        return stop_requested()

    if approval is None:
        return finish("BLOCKED_USER_ACTION", "approval_required")
    if (config.max_cycles > approval.max_cycles
            or config.max_reel_pulses > approval.max_reel_pulses):
        return finish("BLOCKED_USER_ACTION", "approval_limit_exceeded")

    for _ in range(config.max_observations):
        if stopped := stop_requested():
            return stopped
        try:
            observation = observe()
        except Exception as exc:
            return finish("FAILED", "observer_error", f"{type(exc).__name__}: {exc}")
        if stopped := stop_requested():
            return stopped
        if not isinstance(observation, Observation) or not isinstance(observation.state, State):
            return finish("FAILED", "invalid_observation")
        state = observation.state
        progress.events.append(Event("observation", state.value, elapsed(), observation.evidence))
        if observation.unambiguous is not True:
            return finish("FAILED", "ambiguous_state", state.value)
        if state is State.UNKNOWN:
            if (progress.phase in {"cast_sent", "hook_sent", "landing", "sale_sent", "result_handled"}
                    and elapsed() < progress.transition_until):
                if stopped := pause(config.poll_seconds):
                    return stopped
                continue
            return finish("FAILED", "unknown_state", observation.evidence)

        if state not in {State.RECORD_POPUP, State.BUFF_POPUP}:
            progress.last_popup = None
        action = None
        delay = config.poll_seconds
        if state is State.CAST_READY:
            if progress.phase in {"ready", "result_handled"}:
                if progress.cycles >= config.max_cycles:
                    return finish("PASS", "completed")
                action = "cast"
                progress.phase = "cast_sent"
            elif progress.phase != "cast_sent":
                return finish("FAILED", "unexpected_state", f"{progress.phase}: {state.value}")
        elif state is State.WAITING_BITE:
            if progress.phase not in {"cast_sent", "waiting"}:
                return finish("FAILED", "unexpected_state", f"{progress.phase}: {state.value}")
            progress.phase = "waiting"
        elif state is State.BITE:
            if progress.phase in {"cast_sent", "waiting"}:
                action = "hook"
                progress.phase = "hook_sent"
            elif progress.phase != "hook_sent":
                return finish("FAILED", "unexpected_state", f"{progress.phase}: {state.value}")
        elif state is State.REELING:
            if progress.phase not in {"hook_sent", "reeling"}:
                return finish("FAILED", "unexpected_state", f"{progress.phase}: {state.value}")
            if progress.pulses >= config.max_reel_pulses:
                return finish("FAILED", "reel_pulse_limit")
            progress.phase = "reeling"
            action = "reel_upper"
            delay = config.reel_release_seconds
        elif state is State.LANDING:
            if progress.phase not in {"hook_sent", "reeling", "landing"}:
                return finish("FAILED", "unexpected_state", f"{progress.phase}: {state.value}")
            if progress.phase != "landing":
                progress.transition_until = elapsed() + config.transition_grace_seconds
            progress.phase = "landing"
        elif state is State.RESULT:
            if progress.phase in {"hook_sent", "reeling", "landing", "sale_sent"}:
                if progress.phase != "sale_sent":
                    progress.cycles += 1
                if (progress.cycles >= config.max_cycles
                        and not config.finish_result_confirmation):
                    return finish("PASS", "completed")
                action = "continue"
                progress.phase = "result_handled"
            elif progress.phase != "result_handled":
                return finish("FAILED", "unexpected_state", f"{progress.phase}: {state.value}")
        elif state is State.SALE_OFFER:
            if progress.phase in {"hook_sent", "reeling", "landing"}:
                progress.cycles += 1
                if not config.sell_catches:
                    return finish("BLOCKED_USER_ACTION", "sale_not_enabled")
                action = "sell"
                progress.phase = "sale_sent"
            elif progress.phase != "sale_sent":
                return finish("FAILED", "unexpected_state", f"{progress.phase}: {state.value}")
        elif state is State.SALE_PENDING:
            if progress.phase != "sale_sent":
                return finish("FAILED", "unexpected_state", f"{progress.phase}: {state.value}")
        elif state in {State.RECORD_POPUP, State.BUFF_POPUP}:
            if progress.phase != "result_handled":
                return finish("FAILED", "unexpected_state", f"{progress.phase}: {state.value}")
            if progress.last_popup is not state:
                if progress.popups >= config.max_popup_actions:
                    return finish("FAILED", "popup_action_limit")
                action = "confirm_record" if state is State.RECORD_POPUP else "confirm_buff"
                progress.popups += 1
                progress.last_popup = state

        if action is not None:
            if stopped := send(action):
                return stopped
        if stopped := pause(delay):
            return stopped

    return finish("FAILED", "observation_limit")
