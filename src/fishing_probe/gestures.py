"""Bounded touch probes in logical iPad points, never screenshot pixels.

Callers must obtain approval for the complete target/path before invoking these
functions. An API response does not prove that the game accepted the input.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from numbers import Real


def _duration(value: float, minimum: float, maximum: float, name: str) -> None:
    if (isinstance(value, bool) or not isinstance(value, Real)
            or not math.isfinite(value) or not minimum <= value <= maximum):
        raise ValueError(f"{name} must be a finite number in {minimum}–{maximum} seconds")


def _coordinate(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("Coordinates must be nonnegative integer logical points")


def _validate_coordinates(points: Sequence[tuple[int, int]]) -> None:
    for point in points:
        if (not isinstance(point, Sequence) or isinstance(point, (str, bytes))
                or len(point) != 2):
            raise ValueError("Each path point must contain exactly x and y")
        for coordinate in point:
            _coordinate(coordinate)


def _validate_bounds(driver, points: Sequence[tuple[int, int]],
                     expected_window: Mapping[str, int] | None = None) -> None:
    window = driver.get_window_size()
    for key in ("width", "height"):
        value = window.get(key)
        if (isinstance(value, bool) or not isinstance(value, Real)
                or not math.isfinite(value) or value <= 0):
            raise ValueError("Cannot validate touch bounds: invalid logical window size")
    if expected_window is not None and window != expected_window:
        raise ValueError("Logical window changed immediately before input")
    for x, y in points:
        if x >= window["width"] or y >= window["height"]:
            raise ValueError(f"Touch ({x}, {y}) is outside the logical window {window}")


def validate_hold(x: int, y: int, seconds: float = 0.8) -> None:
    """Validate parameters offline, without opening a device session."""
    _duration(seconds, 0.5, 5, "Hold duration")
    _validate_coordinates([(x, y)])


def validate_press(x: int, y: int, seconds: float = 0.5) -> None:
    """Validate a bounded short press; actual game timing requires calibration."""
    _duration(seconds, 0.02, 1, "Press duration")
    _validate_coordinates([(x, y)])


def press(driver, x: int, y: int, seconds: float = 0.5, *,
          before_dispatch: Callable[[], None] | None = None,
          expected_window: Mapping[str, int] | None = None) -> None:
    """Send one native press/release without retrying uncertain delivery.

    An optional synchronous guard runs after the bounds request and immediately
    before native dispatch, so a delayed bounds response cannot bypass a caller's
    elapsed-time limit. The guard must raise to refuse dispatch. expected_window
    checks exact approved geometry using this same single bounds request.
    """
    validate_press(x, y, seconds)
    if expected_window is not None and (
        not isinstance(expected_window, Mapping) or set(expected_window) != {"width", "height"}
        or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0
               for value in expected_window.values())
    ):
        raise ValueError("expected_window must contain positive integer width and height")
    _validate_bounds(driver, [(x, y)], expected_window)
    if before_dispatch is not None:
        before_dispatch()
    driver.execute_script("mobile: touchAndHold", {"x": x, "y": y, "duration": seconds})


def hold(driver, x: int, y: int, seconds: float = 0.8) -> None:
    """Send one long press, restricted to 0.5–5 seconds for initial probes."""
    validate_hold(x, y, seconds)
    _validate_bounds(driver, [(x, y)])
    driver.execute_script("mobile: touchAndHold", {"x": x, "y": y, "duration": seconds})


def validate_drag(x1: int, y1: int, x2: int, y2: int, hold_seconds: float = 0.7) -> None:
    """Validate coordinates and initial hold duration without device calls."""
    _duration(hold_seconds, 0.5, 5, "Initial drag hold")
    _validate_coordinates([(x1, y1), (x2, y2)])


def drag(driver, x1: int, y1: int, x2: int, y2: int, hold_seconds: float = 0.7) -> None:
    """Drag once; hold_seconds is the initial press, not movement duration.

    XCUITest chooses the movement duration for dragFromToForDuration. Use a
    two-point continuous_path when a parameterized movement time is required.
    """
    validate_drag(x1, y1, x2, y2, hold_seconds)
    _validate_bounds(driver, [(x1, y1), (x2, y2)])
    driver.execute_script("mobile: dragFromToForDuration", {
        "fromX": x1, "fromY": y1, "toX": x2, "toY": y2,
        "duration": hold_seconds,
    })


def circle_points(cx: int, cy: int, radius: int, turns: int = 1, samples_per_turn: int = 24) -> list[tuple[int, int]]:
    """Build a bounded closed path; this function does not send input."""
    _coordinate(cx)
    _coordinate(cy)
    if (any(isinstance(v, bool) or not isinstance(v, int)
            for v in (radius, turns, samples_per_turn))
            or not (1 <= turns <= 3 and 8 <= samples_per_turn <= 36 and 5 <= radius <= 200)):
        raise ValueError("Unsafe or unsupported path parameters")
    if cx < radius or cy < radius:
        raise ValueError("Circle would cross a negative logical coordinate")
    return [(round(cx + radius * math.cos(2 * math.pi * i / samples_per_turn)),
             round(cy + radius * math.sin(2 * math.pi * i / samples_per_turn)))
            for i in range(turns * samples_per_turn + 1)]


def validate_path(points: Sequence[tuple[int, int]], step_seconds: float = 0.05,
                  initial_hold: float = 0.1) -> None:
    """Validate every path point and duration before connecting or touching."""
    if (not isinstance(points, Sequence) or isinstance(points, (str, bytes))
            or not 2 <= len(points) <= 109):
        raise ValueError("Continuous path length must be between 2 and 109 points")
    _duration(step_seconds, 0.02, 0.25, "step_seconds")
    _duration(initial_hold, 0, 1, "initial_hold")
    step_ms = round(step_seconds * 1000)
    hold_ms = round(initial_hold * 1000)
    if (step_seconds * (len(points) - 1) + initial_hold > 10
            or step_ms * (len(points) - 1) + hold_ms > 10000):
        raise ValueError("Path too long for initial test")
    _validate_coordinates(points)


def continuous_path(driver, points: Sequence[tuple[int, int]], step_seconds: float = 0.05,
                    initial_hold: float = 0.1) -> None:
    """Send one W3C touch pointer with one down/up across the complete path.

    Each segment uses step_seconds, rounded to milliseconds; total contact time
    is capped at ten seconds. Errors are never retried because input may already
    have been delivered. Best-effort release on failure preserves the original
    error and cannot establish whether the game reacted.
    """
    validate_path(points, step_seconds, initial_hold)
    _validate_bounds(driver, points)
    step_ms = round(step_seconds * 1000)
    hold_ms = round(initial_hold * 1000)

    from selenium.webdriver.common.actions import interaction
    from selenium.webdriver.common.actions.action_builder import ActionBuilder
    from selenium.webdriver.common.actions.pointer_input import PointerInput

    finger = PointerInput(interaction.POINTER_TOUCH, "finger")
    action = ActionBuilder(driver, mouse=finger)
    finger.create_pointer_move(duration=0, x=points[0][0], y=points[0][1], origin="viewport")
    finger.create_pointer_down(button=0)
    if hold_ms:
        finger.add_action({"type": "pause", "duration": hold_ms})
    for x, y in points[1:]:
        finger.create_pointer_move(duration=step_ms, x=x, y=y, origin="viewport")
    finger.create_pointer_up(button=0)
    try:
        action.perform()
    except Exception as exc:
        try:
            action.clear_actions()
        except Exception as cleanup_error:
            exc.add_note(f"Best-effort pointer release also failed: {cleanup_error}")
        raise
