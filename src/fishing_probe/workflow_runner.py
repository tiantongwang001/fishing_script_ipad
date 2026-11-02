"""Preview calibrated fishing plans, or run a separately approved bounded plan.

Preview never loads device configuration or opens a connection. Plan digests are
SHA-256 of exact file bytes. Reference paths resolve from the working directory.
JSON approval records cannot establish that a human actually granted consent;
callers must retain the real approval of the reviewed plan and coordinates.
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

from PIL import Image

from fishing_probe.bobber import BobberConfig, BobberDetector
from fishing_probe.cli import capture_screen, make_run_dir, require_active_game, write_json
from fishing_probe.config import load
from fishing_probe.connection import connect
from fishing_probe.gestures import press, validate_press
from fishing_probe.mjpeg import MJPEGFrame, MJPEGReader
from fishing_probe.vision import PreparedObserver, observe_screenshot
from fishing_probe.workflow import (
    ACTION_NAMES, Approval, Observation, State, WorkflowConfig, run_workflow,
)


REQUIRED_STATES = frozenset({"cast_ready", "waiting_bite", "bite", "reeling", "result"})
ACTION_STATES = {
    "cast": "cast_ready", "hook": "bite", "reel_upper": "reeling",
    "continue": "result", "confirm_record": "record_popup", "confirm_buff": "buff_popup",
    "sell": "sale_offer",
}
STREAM_WDA_SETTINGS = {
    "mjpegServerFramerate": 20, "mjpegScalingFactor": 50,
    "mjpegServerScreenshotQuality": 50, "mjpegFixOrientation": True,
}


@dataclass(frozen=True)
class StreamSettings:
    url: str = "http://127.0.0.1:9100/"
    max_frame_age_seconds: float = 0.25
    frame_wait_seconds: float = 0.3
    bite_token_seconds: float = 0.2

    def __post_init__(self):
        # Constructor validates localhost without starting its network thread.
        MJPEGReader(self.url)
        for name, limit in (("max_frame_age_seconds", 0.25),
                            ("frame_wait_seconds", 1), ("bite_token_seconds", 0.2)):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not 0 < value <= limit):
                raise ValueError(f"{name} must be finite and in (0, {limit}] seconds")


@dataclass(frozen=True)
class Plan:
    payload: dict
    sha256: str
    settings: WorkflowConfig
    logical_size: tuple[int, int]
    screenshot_size: tuple[int, int]
    request_timeout_seconds: float
    observation_source: str
    stream: StreamSettings | None


def _dimensions(value, name: str) -> tuple[int, int]:
    if (not isinstance(value, list) or len(value) != 2
            or any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in value)):
        raise ValueError(f"{name} must be two positive integer dimensions")
    return tuple(value)


def load_plan(path: str | Path) -> Plan:
    raw = Path(path).read_bytes()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Workflow plan must be a JSON object")
    settings = payload.get("settings", {})
    if not isinstance(settings, dict):
        raise ValueError("settings must be an object")
    try:
        config = WorkflowConfig(**settings)
    except TypeError as exc:
        raise ValueError(f"Unsupported workflow settings: {exc}") from exc
    timeout = payload.get("request_timeout_seconds", 15)
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or not 1 <= timeout <= 30):
        raise ValueError("request_timeout_seconds must be finite seconds between 1 and 30")
    source = payload.get("observation_source", "screenshot")
    if source not in {"screenshot", "mjpeg"}:
        raise ValueError("observation_source must be screenshot or mjpeg")
    stream = None
    if source == "mjpeg":
        try:
            stream = StreamSettings(**payload.get("mjpeg", {}))
        except TypeError as exc:
            raise ValueError(f"Invalid mjpeg configuration: {exc}") from exc
    return Plan(
        payload, hashlib.sha256(raw).hexdigest(), config,
        _dimensions(payload.get("logical_size"), "logical_size"),
        _dimensions(payload.get("screenshot_size"), "screenshot_size"), timeout, source, stream,
    )


def temporal_config(plan: Plan) -> tuple[BobberConfig | None, list[str]]:
    """Read provisional reference calibration, never infer live validation."""
    if plan.observation_source != "mjpeg":
        return None, []
    calibration = plan.payload.get("calibration", {})
    raw = calibration.get("bobber") if isinstance(calibration, dict) else None
    if raw is None:
        return None, []
    if (not isinstance(raw, dict) or raw.get("provisional_reference_verified") is not True
            or not isinstance(raw.get("reference_evidence"), str)
            or not raw["reference_evidence"].strip()):
        return None, ["Temporal bobber config requires explicit provisional_reference_verified and reference_evidence"]
    try:
        config = BobberConfig(**{key: value for key, value in raw.items()
                                 if key not in {"provisional_reference_verified", "reference_evidence"}})
        if config.image_size != plan.screenshot_size:
            raise ValueError("image_size must match the stream screenshot_size")
        if config.maximum_frame_age_seconds > plan.stream.max_frame_age_seconds:
            raise ValueError("Bobber maximum_frame_age_seconds exceeds stream age limit")
    except (ValueError, TypeError) as exc:
        return None, [f"Temporal bobber config: {exc}"]
    return config, []


class TemporalBiteGate:
    """One provisional temporal event after our cast and verified waiting state."""

    def __init__(self, config: BobberConfig, token_seconds: float):
        self.detector = BobberDetector(config)
        self.token_seconds = token_seconds
        self.cast_sent = False
        self.armed_once = False
        self.token_expires_at = None

    def after_action(self, name: str) -> None:
        if name in {"cast", "hook"}:
            self.detector.reset()
            self.token_expires_at = None
            self.cast_sent = name == "cast"
            self.armed_once = False

    def observe(self, state: str, frame: MJPEGFrame, image, *, now: float) -> tuple[str, dict | None]:
        if state != "waiting_bite":
            if self.armed_once:
                self.detector.reset()
                self.token_expires_at = None
            return state, None
        if not self.cast_sent:
            return state, None
        if not self.armed_once:
            self.detector.arm(confirmed_phase="waiting_bite")
            self.armed_once = True
        result = self.detector.observe(
            image, captured_at=frame.received_at, observed_at=now, confirmed_phase="waiting_bite",
        )
        if result.triggered:
            self.token_expires_at = frame.received_at + self.token_seconds
            return "bite", asdict(result)
        if result.event in {"reset", "unarmed"}:
            return "unknown", asdict(result)
        return state, asdict(result)

    def require_hook(self, state: str, *, now: float) -> None:
        if (state != "waiting_bite" or self.token_expires_at is None
                or now > self.token_expires_at):
            raise RuntimeError("Hook needs fresh independently matched waiting state and an unexpired temporal bite token")


def required_actions(plan: Plan) -> set[str]:
    actions = {"cast", "hook", "reel_upper"}
    if plan.settings.finish_result_confirmation or plan.settings.max_cycles > 1:
        actions.add("continue")
    if plan.settings.sell_catches:
        actions.add("sell")
    return actions


def calibration_issues(plan: Plan, *, allowed_actions=None) -> list[str]:
    """Validate coordinates, references, and distinctions without a device."""
    bobber_config, issues = temporal_config(plan)
    needed = required_actions(plan) | set(allowed_actions or ())
    actions = plan.payload.get("actions")
    if not isinstance(actions, dict):
        issues.append("actions must contain calibrated action mappings")
        actions = {}
    for name in sorted(set(actions) - ACTION_NAMES):
        issues.append(f"Unsupported action: {name}")
    for name in sorted(needed):
        target = actions.get(name)
        if not isinstance(target, dict) or target.get("manually_verified") is not True:
            issues.append(f"Action {name}: exact target has not been manually verified")
            continue
        try:
            validate_press(target.get("x"), target.get("y"), target.get("press_seconds"))
            if target["x"] >= plan.logical_size[0] or target["y"] >= plan.logical_size[1]:
                raise ValueError("outside approved logical window")
            if name == "reel_upper" and target["press_seconds"] != plan.settings.reel_press_seconds:
                raise ValueError("press_seconds must match settings.reel_press_seconds")
        except (ValueError, KeyError) as exc:
            issues.append(f"Action {name}: {exc}")

    calibration = plan.payload.get("calibration")
    if not isinstance(calibration, dict) or calibration.get("manually_verified") is not True:
        issues.append("State calibration has not been manually verified")
        calibration = calibration if isinstance(calibration, dict) else {}
    bite_note = calibration.get("bite_trigger_evidence")
    if (calibration.get("bite_trigger_verified") is not True
            or not isinstance(bite_note, str) or not bite_note.strip()):
        issues.append("Bite trigger needs independently verified bobber-cue evidence; HIT label alone is not a bite signal")
    for key, expected in (("logical_size", plan.logical_size), ("screenshot_size", plan.screenshot_size)):
        if calibration.get(key) != list(expected):
            issues.append(f"State calibration {key} must match plan")
    templates = calibration.get("templates", [])
    if not isinstance(templates, list):
        issues.append("State templates must be a list")
        templates = []
    available = set()
    for index, template in enumerate(templates):
        if not isinstance(template, dict):
            issues.append(f"Template {index}: must be an object")
            continue
        state = template.get("state")
        if not isinstance(state, str):
            issues.append(f"Template {index}: missing state")
            continue
        if bobber_config is not None and state == "bite":
            issues.append("Temporal bite plans must omit static bite templates; HIT alone cannot produce BITE")
            continue
        if template.get("manually_verified") is not True:
            issues.append(f"Template {state}: not manually verified")
            continue
        if state == "result" and template.get("continue_label_verified") is not True:
            issues.append("Template result: Continue Fishing label must be verified in its regions; sale/aquarium choice is a separate state")
            continue
        if state == "sale_offer" and template.get("sell_label_verified") is not True:
            issues.append("Template sale_offer: active Sell label must be verified in its regions")
            continue
        reference = template.get("reference")
        if not isinstance(reference, str) or not reference.strip():
            issues.append(f"Template {state}: missing reference screenshot")
            continue
        observation = observe_screenshot(
            reference, logical_size=plan.logical_size, calibration=calibration,
        )
        if observation.state != state:
            issues.append(f"Template {state}: reference does not uniquely identify this state ({observation.reason})")
        else:
            available.add(state)
    needed_states = set(REQUIRED_STATES) | {
        ACTION_STATES[name] for name in needed if name in ACTION_STATES
    }
    if bobber_config is not None:
        needed_states.discard("bite")
    for state in sorted(needed_states - available):
        issues.append(f"Missing verified, distinguishable state template: {state}")
    return list(dict.fromkeys(issues))


def load_approval(path: str | Path, plan: Plan) -> Approval:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PermissionError("Approval must be a JSON object")
    digest = payload.get("plan_sha256")
    if not isinstance(digest, str) or not hmac.compare_digest(digest, plan.sha256):
        raise PermissionError("Approval digest does not match the exact current plan bytes")
    note = payload.get("human_note")
    if not isinstance(note, str) or not note.strip():
        raise PermissionError("Approval needs a human_note recording the actual user's approval")
    actions = payload.get("allowed_actions")
    if (not isinstance(actions, list) or any(not isinstance(name, str) for name in actions)
            or len(set(actions)) != len(actions)):
        raise PermissionError("Approval allowed_actions must be a list of distinct action names")
    if "max_cycles" not in payload or "max_reel_pulses" not in payload:
        raise PermissionError("Approval must explicitly state max_cycles and max_reel_pulses")
    try:
        approval = Approval(frozenset(actions), payload["max_cycles"], payload["max_reel_pulses"])
    except ValueError as exc:
        raise PermissionError(str(exc)) from exc
    if not required_actions(plan) <= approval.allowed_actions:
        raise PermissionError("Approval omits an action required for this workflow")
    if (plan.settings.max_cycles > approval.max_cycles
            or plan.settings.max_reel_pulses > approval.max_reel_pulses):
        raise PermissionError("Plan exceeds the approved cycle or reel-pulse limit")
    return approval


def _require_local_mac() -> None:
    if platform.system() != "Darwin" or any(os.environ.get(name) for name in (
        "SSH_CONNECTION", "SSH_CLIENT", "CODESPACES", "REMOTE_CONTAINERS",
    )):
        raise RuntimeError("Workflow execution requires the user's local Mac, outside SSH/remote containers")


def _execute(plan: Plan, approval: Approval, directory: Path) -> dict:
    _require_local_mac()
    cfg = load()
    if not cfg.game_bundle_id.strip():
        raise ValueError("config.json must contain the game's previously discovered bundle ID")
    driver = None
    report = None
    original_error = None
    count = 0
    deadline = None
    reader = None
    original_stream_settings = None
    latest_frame = None
    trigger_evidence = None
    bobber_config, _ = temporal_config(plan)
    bite_gate = TemporalBiteGate(bobber_config, plan.stream.bite_token_seconds) if bobber_config else None
    prepared_observer = PreparedObserver(logical_size=plan.logical_size, calibration=plan.payload["calibration"])
    write_json(directory / "reference_manifest.json", {
        "plan_sha256": plan.sha256, "references": prepared_observer.reference_manifest(),
        "note": "Exact reference bytes were hashed and their ROI pixels frozen in memory before connection.",
    })
    try:
        driver = connect(cfg, request_timeout_seconds=plan.request_timeout_seconds)
        if not driver.session_id:
            raise RuntimeError("Appium did not return a real session identifier")
        write_json(directory / "session.json", {
            "session_id": driver.session_id, "capabilities": driver.capabilities,
            "request_timeout_seconds": plan.request_timeout_seconds,
            "request_retries": False,
        })
        driver.execute_script("mobile: launchApp", {"bundleId": cfg.game_bundle_id})
        if plan.observation_source == "mjpeg":
            require_active_game(driver, cfg.game_bundle_id)
            if driver.get_window_size() != {"width": plan.logical_size[0], "height": plan.logical_size[1]}:
                raise RuntimeError("Initial logical window differs from the approved stream plan")
            current_settings = driver.get_settings()
            if not all(key in current_settings for key in STREAM_WDA_SETTINGS):
                raise RuntimeError("Cannot safely restore all required WDA stream settings")
            original_stream_settings = {key: current_settings[key] for key in STREAM_WDA_SETTINGS}
            driver.update_settings(STREAM_WDA_SETTINGS)
            confirmed = driver.get_settings()
            if any(confirmed.get(key) != value for key, value in STREAM_WDA_SETTINGS.items()):
                raise RuntimeError("WDA did not confirm requested stream settings")
            write_json(directory / "stream_settings.json", {
                "original": original_stream_settings, "requested": STREAM_WDA_SETTINGS,
                "confirmed": {key: confirmed.get(key) for key in STREAM_WDA_SETTINGS},
                "source_capture_latency": "unknown",
            })
            reader = MJPEGReader(plan.stream.url, network_timeout=1.0)
            reader.start()

        def require_recent_frame():
            if reader is not None:
                now = time.monotonic()
                if latest_frame is None or not 0 <= now - latest_frame.received_at <= plan.stream.max_frame_age_seconds:
                    raise RuntimeError("Stream evidence exceeded allowed host-receipt age before input")

        def capture(label: str):
            nonlocal count, latest_frame, trigger_evidence
            count += 1
            path = directory / f"{count:04d}_{label}.png"
            image = None
            if reader is None:
                require_active_game(driver, cfg.game_bundle_id)
                facts = capture_screen(driver, path)
                with Image.open(path) as source:
                    image = source.convert("RGB")
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("Workflow deadline reached before reading stream evidence")
                previous_frame = latest_frame
                latest_frame = reader.next_frame(
                    max_wait=min(plan.stream.frame_wait_seconds, remaining),
                    max_age=plan.stream.max_frame_age_seconds,
                    after=previous_frame, require_unique=True,
                )
                if (previous_frame is not None and (latest_frame.sequence <= previous_frame.sequence
                        or latest_frame.sha256 == previous_frame.sha256)):
                    raise RuntimeError("Stream returned duplicate or nonmonotonic evidence")
                require_recent_frame()
                image = latest_frame.to_image()
                facts = {
                    "logical_window_size": {"width": plan.logical_size[0], "height": plan.logical_size[1]},
                    "logical_size_source": "validated at session setup and before each input",
                    "screenshot_pixels": list(latest_frame.size), "screenshot": str(path),
                    "stream_sequence": latest_frame.sequence, "frame_sha256": latest_frame.sha256,
                    "received_at_monotonic": latest_frame.received_at,
                    "observed_at_monotonic": time.monotonic(),
                    "source_capture_time": None, "source_capture_latency": "unknown",
                }
            if (facts["logical_window_size"] != {
                "width": plan.logical_size[0], "height": plan.logical_size[1],
            } or facts["screenshot_pixels"] != list(plan.screenshot_size)):
                write_json(path.with_suffix(".json"), facts)
                raise RuntimeError("Current device dimensions differ from the approved plan")
            vision = prepared_observer.observe_image(image, logical_size=plan.logical_size)
            state = vision.state
            bobber_evidence = None
            if bite_gate is not None and label == "observe":
                state, bobber_evidence = bite_gate.observe(
                    vision.state, latest_frame, image, now=time.monotonic(),
                )
            if reader is not None:
                # Fast lossless evidence storage; reference PNG decoding and
                # compression must not consume the freshness/token budget.
                image.save(path, compress_level=0)
            require_recent_frame()
            write_json(path.with_suffix(".json"), {
                **facts, "observation": asdict(vision), "workflow_state": state,
                "temporal_bobber": bobber_evidence,
                "host_receipt_age_seconds": time.monotonic() - latest_frame.received_at if reader else None,
            })
            if state == "bite" and bite_gate is not None and label == "observe":
                trigger_evidence = {"vision": vision, "frame": latest_frame, "path": path}
            return vision, state

        def observe():
            vision, state = capture("observe")
            reason = "Provisional temporal bobber event" if state == "bite" and bite_gate else vision.reason
            return Observation(State(state), reason, "Ambiguous" not in vision.reason)

        def execute(action):
            nonlocal trigger_evidence
            # Hook uses its just-observed/saved trigger frame: waiting for a
            # second unique frame would exhaust the short one-shot token.
            # Other inputs still obtain new visual evidence after the app check.
            require_active_game(driver, cfg.game_bundle_id)
            reused_trigger = action.name == "hook" and bite_gate is not None
            if reused_trigger:
                if trigger_evidence is None or trigger_evidence["frame"] is not latest_frame:
                    raise RuntimeError("Hook has no current saved temporal trigger evidence")
                vision = trigger_evidence["vision"]
                evidence_path = trigger_evidence["path"]
                require_recent_frame()
            else:
                vision, _ = capture(f"before_{action.name}")
                evidence_path = directory / f"{count:04d}_before_{action.name}.png"
            if action.name == "hook" and bite_gate is not None:
                bite_gate.require_hook(vision.state, now=time.monotonic())
            elif vision.state != ACTION_STATES[action.name]:
                raise RuntimeError(f"Pre-action screen is {vision.state}; refusing {action.name}")
            if time.monotonic() >= deadline:
                raise RuntimeError("Workflow elapsed limit reached before input")
            target = plan.payload["actions"][action.name]
            seconds = action.press_seconds if action.name == "reel_upper" else target["press_seconds"]
            request_path = directory / f"{count:04d}_{action.name}_request.json"
            write_json(request_path, {
                "action": action.name, "x": target["x"], "y": target["y"],
                "press_seconds": seconds, "api_returned": False,
                "evidence_screenshot": str(evidence_path),
                "reused_temporal_trigger_frame": reused_trigger,
                "stream_sequence": latest_frame.sequence if reader is not None else None,
            })

            def require_dispatch_budget():
                # press validates bounds with one last device request. Recheck
                # after it returns, with enough budget for the configured hold.
                if time.monotonic() + seconds > deadline:
                    raise RuntimeError("Workflow dispatch deadline leaves insufficient time for this press")
                require_recent_frame()
                if action.name == "hook" and bite_gate is not None:
                    bite_gate.require_hook(vision.state, now=time.monotonic())

            press(driver, target["x"], target["y"], seconds,
                  before_dispatch=require_dispatch_budget,
                  expected_window={"width": plan.logical_size[0], "height": plan.logical_size[1]})
            if bite_gate is not None:
                bite_gate.after_action(action.name)
                if action.name in {"cast", "hook"}:
                    trigger_evidence = None
            write_json(request_path.with_name(request_path.stem.replace("_request", "_return") + ".json"), {
                "api_returned": True, "effect_verified": False,
            })

        deadline = time.monotonic() + plan.settings.max_elapsed_seconds
        outcome = run_workflow(
            observe, execute, config=plan.settings, approval=approval,
            clock=time.monotonic, sleep=time.sleep,
        )
        report = asdict(outcome)
        report["plan_sha256"] = plan.sha256
        report["observation_source"] = plan.observation_source
        if reader is not None:
            report["source_capture_latency"] = "unknown; freshness bounds host receipt only"
            report["experimental_temporal_bite"] = bite_gate is not None
        report["timing_note"] = "Configured native durations and minimum release intervals are provisional; screenshots and transport add latency."
        report["deadline_note"] = "max_elapsed_seconds limits input dispatch, with a budget for the configured press duration. In-flight transport and session cleanup may finish later; each HTTP request has a finite timeout."
        return report
    except BaseException as exc:
        original_error = exc
        report = {"status": "FAILED", "reason": "execution_error",
                  "detail": f"{type(exc).__name__}: {exc}", "plan_sha256": plan.sha256}
        raise
    finally:
        def cleanup_error(name: str, exc: Exception):
            detail = f"{type(exc).__name__}: {exc}"
            write_json(directory / f"{name}.json", {"error": detail})
            if report is not None:
                report[name] = detail
                if report.get("status") == "PASS":
                    report["workflow_status"] = "PASS"
                    report.update(status="FAILED", reason="cleanup_error")
            if original_error is not None:
                original_error.add_note(f"{name}: {detail}")

        if reader is not None:
            try:
                reader.close()
                if report is not None:
                    report["stream_closed"] = True
            except Exception as exc:
                cleanup_error("stream_close_error", exc)
        if driver is not None and original_stream_settings is not None:
            try:
                driver.update_settings(original_stream_settings)
                restored = driver.get_settings()
                if any(restored.get(key) != value for key, value in original_stream_settings.items()):
                    raise RuntimeError("WDA stream settings restoration was not confirmed")
                if report is not None:
                    report["stream_settings_restored"] = True
            except Exception as exc:
                cleanup_error("settings_restore_error", exc)
        if driver is not None:
            try:
                driver.quit()
            except Exception as exc:
                cleanup_error("cleanup_error", exc)
        if report is not None:
            write_json(directory / "workflow_result.json", report)


def run_from_args(args) -> dict:
    directory = make_run_dir()
    try:
        plan = load_plan(args.plan)
        issues = calibration_issues(plan)
        preview = {
            "status": "NOT_RUN", "mode": "preview", "plan_sha256": plan.sha256,
            "ready_for_separate_approval": not issues,
            "missing_calibration": issues,
            "logical_size": plan.logical_size, "screenshot_size": plan.screenshot_size,
            "settings": asdict(plan.settings),
            "observation_source": plan.observation_source,
            "mjpeg": asdict(plan.stream) if plan.stream else None,
            "stream_note": "Experimental stream timestamps measure host receipt only; source capture latency is unknown. Temporal calibration is provisional reference validation, not native success.",
            "actions": plan.payload.get("actions", {}),
            "timing_note": "Native press durations are provisional, not validated on the game; release intervals include screenshot and transport overhead.",
            "deadline_note": "max_elapsed_seconds is an input dispatch deadline, not a hard total-process timeout; finite in-flight transport requests and session cleanup may finish later.",
            "approval_note": "Execution needs actual human approval of this exact plan and its targets; flags and JSON do not grant permission.",
        }
        write_json(directory / "workflow_preview.json", preview)
        if not args.execute:
            print(json.dumps({**preview, "output": str(directory)}, indent=2, ensure_ascii=False))
            return preview
        if not (args.armed and args.safe_target_confirmed and args.approval_file):
            raise PermissionError("Execution requires --armed, --safe-target-confirmed and --approval-file after explicit human approval")
        approval = load_approval(args.approval_file, plan)
        issues = calibration_issues(plan, allowed_actions=approval.allowed_actions)
        if issues:
            raise PermissionError("Execution blocked before connection: " + "; ".join(issues))
        report = _execute(plan, approval, directory)
        print(f"{report['status']}: {report['reason']}; evidence: {directory}")
        return report
    except BaseException as exc:
        write_json(directory / "workflow_error.json", {
            "status": "BLOCKED_USER_ACTION" if isinstance(exc, PermissionError) else "FAILED",
            "error": f"{type(exc).__name__}: {exc}",
        })
        print(f"Workflow stopped: {exc}; evidence: {directory}")
        raise


def add_parser(commands) -> None:
    parser = commands.add_parser("workflow", help="Preview by default; separately approved bounded fishing workflow")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--armed", action="store_true")
    parser.add_argument("--safe-target-confirmed", action="store_true")
    parser.add_argument("--approval-file")
