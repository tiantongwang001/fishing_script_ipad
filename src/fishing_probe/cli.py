"""CLI: doctor, inspect and intentionally armed single-gesture probes."""
from __future__ import annotations

import argparse
import json
import math
import platform
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from fishing_probe.apps import normalize_app_list, select_fishing_game
from fishing_probe.config import load
from fishing_probe.gestures import (
    circle_points, continuous_path, drag, hold,
    validate_drag, validate_hold, validate_path,
)


def run_command(args: list[str], timeout: int = 30) -> dict:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return {"exit_code": p.returncode, "stdout": p.stdout, "stderr": p.stderr}
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"error": str(exc)}


def doctor() -> None:
    output = Path("runs")
    output.mkdir(exist_ok=True)
    checks = {
        "machine": platform.platform(),
        "arch": platform.machine(),
        "commands": {
            "sw_vers": ["sw_vers"],
            "xcodebuild": ["xcodebuild", "-version"],
            "xcode-select": ["xcode-select", "-p"],
            "node": ["node", "--version"],
            "appium": ["appium", "--version"],
            "xcuitest": ["appium", "driver", "list", "--installed"],
            "devices": ["xcrun", "devicectl", "list", "devices"],
        },
    }
    checks["results"] = {label: run_command(args) for label, args in checks["commands"].items()}
    path = output / "doctor.json"
    path.write_text(json.dumps(checks, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Diagnostics written locally to {path}")
    for name, result in checks["results"].items():
        print(f"{name}: exit={result.get('exit_code', 'N/A')} {result.get('error', '')}")
    print("Sensitive IDs may appear in doctor.json; do not publish it unchanged.")


def make_run_dir() -> Path:
    directory = Path("runs") / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    directory.mkdir(parents=True, exist_ok=False)
    return directory


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def capture_screen(driver, path: Path) -> dict:
    """Require a saved, readable image before treating a capture as evidence."""
    logical = driver.get_window_size()
    for axis in ("width", "height"):
        size = logical.get(axis)
        if isinstance(size, bool) or not isinstance(size, (int, float)) or not math.isfinite(size) or size <= 0:
            raise ValueError("Driver returned invalid logical window dimensions")
    if not driver.save_screenshot(str(path)) or not path.is_file():
        raise RuntimeError(f"Screenshot was not saved: {path}")
    from PIL import Image
    with Image.open(path) as img:
        pixels = list(img.size)
        img.verify()
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "logical_window_size": logical,
        "screenshot_pixels": pixels,
        "pixels_per_point": {"x": pixels[0] / logical["width"], "y": pixels[1] / logical["height"]},
        "screenshot": str(path),
    }


def require_active_game(driver, bundle: str) -> dict:
    active = driver.execute_script("mobile: activeAppInfo", {})
    if not isinstance(active, dict) or active.get("bundleId") != bundle:
        raise RuntimeError("The expected game is not the frontmost app; inspect the local screenshot before proceeding")
    return active


def inspect_current(driver, directory: Path, configured_bundle: str = "") -> tuple[str, dict]:
    raw = driver.execute_script("mobile: listApps", {"applicationType": "User"})
    apps = normalize_app_list(raw)
    write_json(directory / "installed_apps.json", apps)
    bundle, metadata = select_fishing_game(apps, configured_bundle)
    # Safe: launch or foreground without clearing local data.
    driver.execute_script("mobile: launchApp", {"bundleId": bundle})
    time.sleep(4)
    facts = {"bundleId": bundle, "appInfo": metadata,
             **capture_screen(driver, directory / "before.png")}
    # Keep a diagnostic screenshot even if launch did not foreground the game.
    write_json(directory / "facts.json", facts)
    facts["active_app"] = require_active_game(driver, bundle)
    write_json(directory / "facts.json", facts)
    print(json.dumps({key: facts[key] for key in (
        "bundleId", "logical_window_size", "screenshot_pixels", "pixels_per_point", "screenshot")}, indent=2))
    return bundle, facts


def prepare_gesture(args) -> list[tuple[int, int]] | None:
    """Reject an unapproved or malformed request without opening a session."""
    if not (args.armed and args.safe_target_confirmed):
        raise PermissionError("A gesture requires --armed AND --safe-target-confirmed after human approval of the screenshot/coordinates")
    if args.kind == "hold":
        validate_hold(args.x, args.y, args.seconds)
    elif args.kind == "drag":
        validate_drag(args.x, args.y, args.to_x, args.to_y, args.seconds)
    elif args.kind == "circle":
        points = circle_points(args.x, args.y, args.radius, args.turns)
        validate_path(points, args.step_seconds)
        return points
    else:
        raise ValueError(f"Unsupported kind: {args.kind}")
    return None


def probe(args) -> None:
    directory = make_run_dir()
    driver = None
    failed = False
    try:
        points = prepare_gesture(args) if args.command == "gesture" else None
        cfg = load()
        from fishing_probe.connection import connect
        driver = connect(cfg)
        if not driver.session_id:
            raise RuntimeError("Appium did not return a session identifier")
        write_json(directory / "session.json", {
            "connected_at": datetime.now(timezone.utc).isoformat(),
            "session_id": driver.session_id,
            "capabilities": driver.capabilities,
            "wda_mode": cfg.wda_mode,
        })
        bundle, _ = inspect_current(driver, directory, cfg.game_bundle_id)
        if args.command == "gesture":
            # Recheck immediately before input, after capture and app inspection.
            active = require_active_game(driver, bundle)
            write_json(directory / "gesture_request.json", {
                "requested_at": datetime.now(timezone.utc).isoformat(),
                "parameters": vars(args), "active_app": active,
            })
            if args.kind == "hold":
                hold(driver, args.x, args.y, args.seconds)
            elif args.kind == "drag":
                drag(driver, args.x, args.y, args.to_x, args.to_y, args.seconds)
            elif args.kind == "circle":
                continuous_path(driver, points, step_seconds=args.step_seconds)
            write_json(directory / "gesture_result.json", {
                "api_returned": True,
                "visual_verification": "NOT_RUN",
                "note": "API completion alone does not establish an in-game effect.",
            })
            time.sleep(1)
            write_json(directory / "after_facts.json", capture_screen(driver, directory / "after.png"))
            print(f"Gesture API request completed; compare {directory / 'before.png'} and {directory / 'after.png'} and physically verify the game's reaction.")
        print("Run output:", directory)
    except Exception as exc:
        failed = True
        (directory / "error.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        print(f"FAILED ({type(exc).__name__}): {exc}; evidence: {directory}")
        raise
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception as exc:
                (directory / "cleanup_error.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
                if not failed:
                    raise


def main() -> None:
    from fishing_probe.workflow_runner import add_parser, run_from_args
    from fishing_probe.clicker import add_parser as add_clicker_parser, run_from_args as run_clicker
    parser = argparse.ArgumentParser(description="Safe Fishing Superstars real iPad test")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Inspect host environment, no iPad interaction")
    commands.add_parser("inspect", help="Connect, find game and save screenshot, NO touches")
    add_parser(commands)
    add_clicker_parser(commands)
    gesture = commands.add_parser("gesture", help="Explicitly armed, single touch gesture")
    gesture.add_argument("--kind", required=True, choices=["hold", "drag", "circle"])
    gesture.add_argument("--x", type=int)
    gesture.add_argument("--y", type=int)
    gesture.add_argument("--to-x", type=int)
    gesture.add_argument("--to-y", type=int)
    gesture.add_argument("--radius", type=int)
    gesture.add_argument("--turns", type=int, default=1)
    gesture.add_argument("--step-seconds", type=float, default=0.05)
    gesture.add_argument("--seconds", type=float, default=0.8)
    gesture.add_argument("--armed", action="store_true")
    gesture.add_argument("--safe-target-confirmed", action="store_true")
    args = parser.parse_args()
    if args.command == "doctor":
        doctor()
    elif args.command == "workflow":
        result = run_from_args(args)
        if args.execute and result["status"] != "PASS":
            raise SystemExit(1)
    elif args.command == "clicker":
        result = run_clicker(args)
        if args.execute and result["status"] != "PASS":
            raise SystemExit(1)
    else:
        probe(args)


if __name__ == "__main__":
    main()
