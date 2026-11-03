"""Bounded timer-only clicker; API completion never establishes a catch.

This deliberately performs no screenshot recognition and cannot detect early
catches, missed bites, or popups. Start on the calibrated cast-ready screen.
The caller must already have the user's approval of the exact points and scope.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import platform
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from fishing_probe.cli import make_run_dir, require_active_game, write_json
from fishing_probe.config import load
from fishing_probe.connection import connect
from fishing_probe.gestures import press, press_batch, validate_press


ACTION_NAMES = frozenset({"cast", "hook", "reel_upper", "sell", "continue"})
REPAIR_ACTION_NAMES = ("repair_open", "repair_all", "repair_confirm")
OPTIONAL_ACTION_NAMES = frozenset({"confirm_popup", "confirm_competition", "startup_continue", *REPAIR_ACTION_NAMES})


def _number(value, minimum, maximum, name):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not minimum <= value <= maximum):
        raise ValueError(f"{name} must be finite and between {minimum} and {maximum}")


@dataclass(frozen=True)
class ClickerSettings:
    cycles: int = 1
    max_elapsed_seconds: float = 90
    initial_delay_seconds: float = 0.8
    next_cycle_delay_seconds: float = 0
    hook_wait_seconds: float = 8.1
    hook_settle_seconds: float = 0
    reel_pulses: int = 10
    reel_release_seconds: float = 0.05
    merge_hit_reel: bool = False
    hit_to_reel_seconds: float = .05
    loggroup: bool = True
    competetion: bool = False
    competition_to_buff_seconds: float = 0.6
    reel_batch_size: int = 1
    reel_batch_gap_seconds: float = 0.05
    result_wait_seconds: float = 2
    confirm_to_sell_seconds: float = 0.6
    sell_to_continue_seconds: float = 0.6
    final_wait_seconds: float = 0.6
    repair_step_wait_seconds: float = 0.6
    repair_every_rounds: int = 40
    continue_to_cast_seconds: float = 0

    def __post_init__(self):
        for name in ("loggroup", "competetion", "merge_hit_reel"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be a boolean")
        _number(self.hit_to_reel_seconds, 0, 1, "hit_to_reel_seconds")
        _number(self.competition_to_buff_seconds, 0, 60, "competition_to_buff_seconds")
        if isinstance(self.reel_batch_size, bool) or not isinstance(self.reel_batch_size, int) or not 1 <= self.reel_batch_size <= 3:
            raise ValueError("reel_batch_size must be an integer between 1 and 3")
        _number(self.reel_batch_gap_seconds, .001, 1, "reel_batch_gap_seconds")
        for name, maximum in (("cycles", 1000), ("reel_pulses", 120)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
                raise ValueError(f"{name} must be an integer between 1 and {maximum}")
        if isinstance(self.repair_every_rounds, bool) or not isinstance(self.repair_every_rounds, int) or not 0 <= self.repair_every_rounds <= 1000:
            raise ValueError("repair_every_rounds must be an integer between 0 and 1000")
        _number(self.max_elapsed_seconds, 1, 86400, "max_elapsed_seconds")
        for name in ("initial_delay_seconds", "next_cycle_delay_seconds", "hook_wait_seconds", "hook_settle_seconds",
                     "reel_release_seconds", "result_wait_seconds", "confirm_to_sell_seconds", "sell_to_continue_seconds",
                     "final_wait_seconds", "repair_step_wait_seconds", "continue_to_cast_seconds"):
            _number(getattr(self, name), 0, 60, name)


@dataclass(frozen=True)
class Recipe:
    payload: dict
    sha256: str
    settings: ClickerSettings
    logical_size: tuple[int, int]
    request_timeout_seconds: float


@dataclass(frozen=True)
class Step:
    kind: str
    name: str
    seconds: float
    cycle: int
    pulse: int | None = None
    count: int = 1


def load_recipe(path: str | Path) -> Recipe:
    raw = Path(path).read_bytes()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Clicker recipe must be a JSON object")
    try:
        settings = ClickerSettings(**payload.get("settings", {}))
    except TypeError as exc:
        raise ValueError(f"Invalid clicker settings: {exc}") from exc
    size = payload.get("logical_size")
    if (not isinstance(size, list) or len(size) != 2
            or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in size)):
        raise ValueError("logical_size must contain positive integer width and height")
    actions = payload.get("actions")
    if (not isinstance(actions, dict) or not ACTION_NAMES <= set(actions)
            or set(actions) - ACTION_NAMES - OPTIONAL_ACTION_NAMES):
        raise ValueError("Recipe requires cast, hook, reel_upper, sell, continue; optional actions are confirm_popup, confirm_competition, startup_continue and the complete repair sequence")
    if settings.competetion and "confirm_competition" not in actions:
        raise ValueError("competetion=True requires confirm_competition action")
    repair_present = set(actions) & set(REPAIR_ACTION_NAMES)
    if repair_present and repair_present != set(REPAIR_ACTION_NAMES):
        raise ValueError("Repair sequence requires all three repair actions")
    for name, target in actions.items():
        if not isinstance(target, dict):
            raise ValueError(f"Action {name} must be a coordinate/duration object")
        # Timings can be previewed while the exact points remain unverified.
        validate_press(0, 0, target.get("press_seconds"))
    timeout = payload.get("request_timeout_seconds", 15)
    _number(timeout, 1, 30, "request_timeout_seconds")
    return Recipe(payload, hashlib.sha256(raw).hexdigest(), settings, tuple(size), timeout)


def readiness_issues(recipe: Recipe) -> list[str]:
    issues = []
    for name, target in recipe.payload["actions"].items():
        if target.get("manually_verified") is not True:
            issues.append(f"Action {name}: exact iPad point is not verified")
        try:
            validate_press(target.get("x"), target.get("y"), target["press_seconds"])
            if target["x"] >= recipe.logical_size[0] or target["y"] >= recipe.logical_size[1]:
                raise ValueError("Point is outside the approved logical window")
        except ValueError as exc:
            issues.append(f"Action {name}: {exc}")
    issues.extend(geometry_issues(recipe))
    return issues


def geometry_issues(recipe: Recipe) -> list[str]:
    """Check optional measured visual envelopes without observing the screen.

    The smaller box must overlap by at least 80% to qualify for the user's
    heavy-overlap exception. A box map cannot establish live game state or
    detect interactive areas without visible borders.
    """
    geometry = recipe.payload.get("button_geometry")
    if geometry is None:
        return []
    try:
        if geometry["logical_size"] != list(recipe.logical_size):
            raise ValueError("geometry logical size differs from recipe")
        margin = geometry["separation_guard_points"]
        _number(margin, 6, 30, "separation_guard_points")
        controls = geometry["controls"]
        mapping = geometry["action_controls"]
        if not isinstance(controls, list) or not controls or set(mapping) != set(recipe.payload["actions"]):
            raise ValueError("geometry must cover every configured action")
        by_id = {}
        for control in controls:
            ident, b = control["id"], control["bounds"]
            if not isinstance(ident, str) or not ident or ident in by_id or len(b) != 4:
                raise ValueError("invalid or duplicate control")
            for value in b:
                _number(value, 0, max(recipe.logical_size), "control bound")
            if not (b[0] < b[2] <= recipe.logical_size[0] and b[1] < b[3] <= recipe.logical_size[1]):
                raise ValueError("control bounds outside logical window")
            by_id[ident] = control
        issues = []
        for name, target in recipe.payload["actions"].items():
            control = by_id[mapping[name]]
            b = control["bounds"]
            x, y = target["x"], target["y"]
            if not (b[0]+margin <= x <= b[2]-margin and b[1]+margin <= y <= b[3]-margin):
                issues.append(f"Action {name}: point lacks an inner margin in its visible control")
            if control.get("shape") == "ellipse":
                rx, ry = (b[2]-b[0])/2-margin, (b[3]-b[1])/2-margin
                if rx <= 0 or ry <= 0 or ((x-(b[0]+b[2])/2)/rx)**2 + ((y-(b[1]+b[3])/2)/ry)**2 > 1:
                    issues.append(f"Action {name}: point outside the inset control ellipse")
            for other_id, other in by_id.items():
                if other_id == mapping[name] or name in other.get("separation_exempt_actions", []):
                    continue
                q = other["bounds"]
                overlap = max(0,min(b[2],q[2])-max(b[0],q[0])) * max(0,min(b[3],q[3])-max(b[1],q[1]))
                smaller = min((b[2]-b[0])*(b[3]-b[1]),(q[2]-q[0])*(q[3]-q[1]))
                if overlap/smaller >= .8:
                    continue
                if q[0]-margin <= x <= q[2]+margin and q[1]-margin <= y <= q[3]+margin:
                    issues.append(f"Action {name}: point overlaps {other_id} visual box/guard")
        return issues
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        return [f"Invalid button geometry: {exc}"]


def build_steps(recipe: Recipe) -> tuple[Step, ...]:
    settings = recipe.settings
    steps = []
    if "startup_continue" in recipe.payload["actions"]:
        # One startup-only tap. Cycle zero must not count as a fishing round.
        steps.append(Step("touch", "startup_continue",
                          recipe.payload["actions"]["startup_continue"]["press_seconds"], 0))

    for cycle in range(1, settings.cycles + 1):
        def wait(name, seconds):
            steps.append(Step("wait", name, seconds, cycle))

        def touch(name, pulse=None):
            steps.append(Step("touch", name, recipe.payload["actions"][name]["press_seconds"], cycle, pulse))

        if cycle == 1:
            wait("initial_delay", settings.initial_delay_seconds)
        else:
            wait("next_cycle_delay", settings.next_cycle_delay_seconds)
        wait("continue_to_cast", settings.continue_to_cast_seconds)
        touch("cast")
        wait("wait_before_hook", settings.hook_wait_seconds)
        if not settings.merge_hit_reel:
            touch("hook")
            wait("hook_settle", settings.hook_settle_seconds)
        group_size = settings.reel_batch_size if settings.loggroup else settings.reel_pulses
        for pulse_number in range(1, settings.reel_pulses + 1, group_size):
            count = min(group_size, settings.reel_pulses - pulse_number + 1)
            merged = settings.merge_hit_reel and pulse_number == 1
            if merged or not settings.loggroup or settings.reel_batch_size > 1:
                hold_seconds = round(recipe.payload["actions"]["reel_upper"]["press_seconds"] * 1000) / 1000
                gap_seconds = round(settings.reel_batch_gap_seconds * 1000) / 1000
                duration = count * hold_seconds + (count - 1) * gap_seconds
                if merged:
                    duration += round(recipe.payload["actions"]["hook"]["press_seconds"] * 1000) / 1000
                    duration += round(settings.hit_to_reel_seconds * 1000) / 1000
                steps.append(Step("hook_batch" if merged else "batch", "reel_upper", duration,
                                  cycle, pulse_number, count))
            else:
                touch("reel_upper", pulse_number)
            if settings.loggroup:
                wait("reel_release", settings.reel_release_seconds)
        wait("result_wait", settings.result_wait_seconds)
        if settings.competetion:
            touch("confirm_competition")
            wait("competition_to_buff", settings.competition_to_buff_seconds)
        if "confirm_popup" in recipe.payload["actions"]:
            # A user-approved fixed result-screen tap, independent of images.
            # Never placed between CAST and HIT or within the reel sequence.
            touch("confirm_popup")
            wait("confirm_to_sell", settings.confirm_to_sell_seconds)
        touch("sell")
        if cycle == settings.cycles:
            # Leave the final successful catch on the Continue screen.
            wait("final_wait", settings.final_wait_seconds)
            continue
        wait("sell_to_continue", settings.sell_to_continue_seconds)
        touch("continue")
        if ("repair_open" in recipe.payload["actions"] and settings.repair_every_rounds
                and cycle % settings.repair_every_rounds == 0):
            # Count attempts, independently of unobserved success/failure.
            for repair_action in REPAIR_ACTION_NAMES:
                touch(repair_action)
                wait("repair_settle", settings.repair_step_wait_seconds)
    return tuple(steps)


class ClickerStop(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def run_schedule(recipe: Recipe, execute, *, clock=time.monotonic, sleep=time.sleep,
                 cancelled=lambda: False, started_at=None) -> dict:
    """Execute fixed timing only; input authorization belongs to the caller.

    Ten configured reel pulses always remain ten, including after an unnoticed
    early catch. Errors, cancellation, geometry/foreground failures, or the
    input deadline stop the run without retrying or guessing popup controls.
    """
    started = clock() if started_at is None else started_at
    deadline = started + recipe.settings.max_elapsed_seconds
    events = []
    attempted = 0
    returned = 0
    completed_cycles = 0
    continued_at = None

    def result(status, reason, detail=""):
        return {
            "status": status, "reason": reason, "detail": detail,
            "cycles_api_completed": completed_cycles, "action_attempts": attempted,
            "action_api_returns": returned, "elapsed_seconds": max(0, clock()-started),
            "events": events, "game_effect_verified": False, "game_outcome": "NOT_RUN",
            "screen_recognition": False,
        }

    try:
        for step in build_steps(recipe):
            if cancelled():
                raise ClickerStop("cancelled")
            now = clock()
            if now >= deadline:
                raise ClickerStop("elapsed_limit")
            if step.kind == "wait":
                seconds = step.seconds
                if step.name == "continue_to_cast":
                    seconds = (max(0, continued_at + step.seconds - now)
                               if continued_at is not None else 0)
                events.append({"kind": "wait", "name": step.name, "cycle": step.cycle,
                               "seconds": seconds, "target_seconds": step.seconds,
                               "elapsed_seconds": now-started})
                wait_until = min(now + seconds, deadline)
                while clock() < wait_until:
                    if cancelled():
                        raise ClickerStop("cancelled")
                    remaining = wait_until-clock()
                    if remaining <= 0:
                        break
                    sleep(min(0.25, remaining))
                if clock() >= deadline:
                    raise ClickerStop("elapsed_limit")
                if step.name == "final_wait":
                    completed_cycles += 1
                continue
            if now + step.seconds > deadline:
                raise ClickerStop("elapsed_limit")
            attempted += 1
            events.append({**asdict(step), "kind": "action_attempt", "elapsed_seconds": now-started})
            execute(step)
            if step.name in ("continue", "startup_continue"):
                continued_at = clock()
            returned += 1
            if step.name == "continue" and step.cycle < recipe.settings.cycles:
                completed_cycles += 1
            events.append({"kind": "api_return", "name": step.name, "cycle": step.cycle,
                           "pulse": step.pulse, "elapsed_seconds": clock()-started})
            if clock() >= deadline:
                raise ClickerStop("elapsed_limit")
        return result("PASS", "timed_sequence_api_completed")
    except ClickerStop as exc:
        return result("NOT_RUN" if exc.reason == "cancelled" and not attempted else "FAILED", exc.reason)
    except KeyboardInterrupt:
        return result("FAILED" if attempted else "NOT_RUN", "cancelled")
    except Exception as exc:
        return result("FAILED", "action_error", f"{type(exc).__name__}: {exc}")


def _verify_optional_approval(path, recipe: Recipe) -> None:
    if not path:
        return
    approval = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(approval, dict):
        raise PermissionError("Approval must be a JSON object")
    digest = approval.get("recipe_sha256")
    if (not isinstance(digest, str) or len(digest) != 64 or not digest.isascii()
            or not hmac.compare_digest(digest, recipe.sha256)):
        raise PermissionError("Approval does not match the exact recipe bytes")
    if not isinstance(approval.get("human_note"), str) or not approval["human_note"].strip():
        raise PermissionError("Approval human_note must record the actual user authorization")


def _execute(recipe: Recipe, directory: Path) -> dict:
    if platform.system() != "Darwin" or any(os.environ.get(key) for key in (
        "SSH_CONNECTION", "SSH_CLIENT", "CODESPACES", "REMOTE_CONTAINERS",
    )):
        raise RuntimeError("Clicker requires the user's local Mac, outside SSH/remote containers")
    cfg = load()
    if not cfg.game_bundle_id.strip():
        raise ValueError("config.json needs the game's previously measured bundle ID")
    driver = None
    report = None
    original_error = None
    sequence = 0
    try:
        longest_batch = max((step.seconds for step in build_steps(recipe) if step.kind in ("batch", "hook_batch")), default=0)
        request_timeout = max(recipe.request_timeout_seconds, longest_batch + 10)
        driver = connect(cfg, request_timeout_seconds=request_timeout)
        if not driver.session_id:
            raise RuntimeError("Appium did not return a session identifier")
        driver.execute_script("mobile: launchApp", {"bundleId": cfg.game_bundle_id})
        started = time.monotonic()
        deadline = started + recipe.settings.max_elapsed_seconds

        def execute(step: Step):
            nonlocal sequence
            sequence += 1
            target = recipe.payload["actions"][step.name]
            event = {"sequence": sequence, **asdict(step), "x": target["x"], "y": target["y"],
                     "requested_at_monotonic": time.monotonic(), "native_dispatch_at_monotonic": None,
                     "api_returned": False, "game_effect_verified": False}
            path = directory / f"{sequence:04d}_{step.name}.json"
            write_json(path, event)

            def before_dispatch():
                if time.monotonic() + step.seconds > deadline:
                    raise ClickerStop("elapsed_limit")
                event["native_dispatch_at_monotonic"] = time.monotonic()

            try:
                require_active_game(driver, cfg.game_bundle_id)
                kwargs = {"expected_window": {"width": recipe.logical_size[0], "height": recipe.logical_size[1]},
                          "before_dispatch": before_dispatch}
                if step.kind in ("batch", "hook_batch"):
                    if step.kind == "hook_batch":
                        hook = recipe.payload["actions"]["hook"]
                        kwargs["initial_press"] = (hook["x"], hook["y"], hook["press_seconds"])
                        kwargs["initial_gap_seconds"] = recipe.settings.hit_to_reel_seconds
                        event["hook"] = dict(hook)
                        event["hit_to_reel_seconds"] = recipe.settings.hit_to_reel_seconds
                    press_batch(driver, target["x"], target["y"], target["press_seconds"],
                                step.count, recipe.settings.reel_batch_gap_seconds,
                                whole_round=not recipe.settings.loggroup, **kwargs)
                else:
                    press(driver, target["x"], target["y"], step.seconds, **kwargs)
                event["api_returned"] = True
            except BaseException as exc:
                event["error"] = f"{type(exc).__name__}: {exc}"
                event["input_delivery_unknown"] = event["native_dispatch_at_monotonic"] is not None
                raise
            finally:
                now = time.monotonic()
                event["returned_at_monotonic"] = now
                event["action_elapsed_seconds"] = now-event["requested_at_monotonic"]
                if event["native_dispatch_at_monotonic"] is not None:
                    event["native_api_elapsed_seconds"] = now-event["native_dispatch_at_monotonic"]
                write_json(path, event)

        report = run_schedule(recipe, execute, clock=time.monotonic, sleep=time.sleep, started_at=started)
        report["recipe_sha256"] = recipe.sha256
        report["deadline_note"] = "Input dispatch is bounded; finite in-flight HTTP requests and cleanup can finish later."
        return report
    except BaseException as exc:
        original_error = exc
        report = {"status": "FAILED", "reason": "execution_error", "error": f"{type(exc).__name__}: {exc}",
                  "recipe_sha256": recipe.sha256, "game_effect_verified": False}
        raise
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                if report is not None:
                    report["cleanup_error"] = message
                    if report.get("status") == "PASS":
                        report["schedule_status"] = "PASS"
                        report.update(status="FAILED", reason="cleanup_error")
                if original_error is not None:
                    original_error.add_note(f"Session cleanup failed: {message}")
        if report is not None:
            write_json(directory / "clicker_result.json", report)


def run_from_args(args) -> dict:
    directory = make_run_dir()
    try:
        recipe = load_recipe(args.recipe)
        issues = readiness_issues(recipe)
        steps = build_steps(recipe)
        nominal_seconds = sum(step.seconds for step in steps)
        preview = {
            "status": "NOT_RUN", "mode": "preview", "recipe_sha256": recipe.sha256,
            "ready_with_existing_user_authorization": not issues, "missing_calibration": issues,
            "logical_size": recipe.logical_size, "settings": asdict(recipe.settings),
            "actions": recipe.payload["actions"], "nominal_seconds_excluding_rpc": nominal_seconds,
            "planned_touch_count": sum(step.count + (step.kind == "hook_batch") for step in steps if step.kind != "wait"),
            "planned_input_requests": sum(step.kind != "wait" for step in steps),
            "deadline_can_truncate_nominal_schedule": nominal_seconds > recipe.settings.max_elapsed_seconds,
            "screen_recognition": False, "game_effect_verified": False,
            "notes": [
                "merge_hit_reel submits HIT and the first reel batch in one request; hit_to_reel_seconds replaces the host hook_settle wait. count is reel contacts only; hook_batch adds one HIT contact.",
                "With startup_continue configured, start on Failure/Continue or CAST with popups closed; otherwise start on CAST.",
                "Exactly the configured reel pulse count is attempted, even after an unnoticed early catch.",
                "Missed bites, results and unknown popups are not detected. If configured, confirm_popup is always tapped after result_wait, whether a popup exists or not.",
                "Host waits occur after preceding API returns. W3C batch internal gaps are device-scheduled; group boundaries still include RPC overhead.",
                "loggroup=True uses batches of up to three presses; False submits all reel presses at once. Checks/logging remain before and after the submitted sequence; cancellation cannot interrupt its individual contacts. action_api_returns counts requests, not contacts.",
                "continue_to_cast is a total target since Continue returned: elapsed repair/preparation time is subtracted. Preview sums its full target conservatively; actual remaining wait is logged.",
                "initial_delay applies once; next_cycle_delay applies between rounds; final_wait applies only after the last round.",
                "Execution flags record already obtained permission; they do not grant new permission.",
            ],
        }
        write_json(directory / "clicker_preview.json", preview)
        if not args.execute:
            print(json.dumps({**preview, "output": str(directory)}, indent=2, ensure_ascii=False))
            return preview
        if not (args.armed and args.safe_target_confirmed):
            raise PermissionError("Execution requires --armed --safe-target-confirmed after actual approval of these exact points and scope")
        _verify_optional_approval(args.approval_file, recipe)
        if issues:
            raise PermissionError("Clicker blocked before connection: " + "; ".join(issues))
        result = _execute(recipe, directory)
        print(f"{result['status']}: {result['reason']}; game effect unverified; evidence: {directory}")
        return result
    except BaseException as exc:
        write_json(directory / "clicker_error.json", {
            "status": "BLOCKED_USER_ACTION" if isinstance(exc, PermissionError) else "FAILED",
            "error": f"{type(exc).__name__}: {exc}",
        })
        raise


def add_parser(commands):
    parser = commands.add_parser("clicker", help="Preview a bounded fixed-timing recipe; no screen recognition")
    parser.add_argument("--recipe", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--armed", action="store_true")
    parser.add_argument("--safe-target-confirmed", action="store_true")
    parser.add_argument("--approval-file", help="Optional existing authorization record bound to exact recipe bytes")
