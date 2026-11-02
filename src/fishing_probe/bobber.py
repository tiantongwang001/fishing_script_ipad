"""Offline, provisional rise-then-dip detector for a measured bobber region.

Thresholds come from one compressed reference clip and are not validated for
native iPad frames. This module never controls a device or treats HIT as a bite.
The caller must independently confirm the waiting phase, explicitly arm each
attempt, and supply capture/observation times from the same monotonic clock.
"""
from __future__ import annotations

import colorsys
from collections.abc import Sequence
from dataclasses import dataclass
import hashlib
import math
from numbers import Real
from pathlib import Path
import statistics

from PIL import Image


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


@dataclass(frozen=True)
class BobberConfig:
    """Geometry is in image pixels; ROI is half-open (left, top, right, bottom)."""

    image_size: tuple[int, int]
    roi: tuple[int, int, int, int]
    baseline_frames: int = 3
    baseline_seconds: float = 0.3
    rise_pixels: float = 10
    dip_area_fraction: float = 0.25
    rise_area_fraction: float = 0.5
    rise_to_dip_seconds: float = 0.3
    maximum_frame_age_seconds: float = 0.25
    maximum_frame_gap_seconds: float = 0.6
    minimum_yellow_area: int = 5
    hue_degrees: tuple[float, float] = (40, 75)
    minimum_saturation: float = 0.35
    minimum_value: float = 0.45

    def __post_init__(self) -> None:
        for name, count in (("image_size", 2), ("roi", 4)):
            value = getattr(self, name)
            if (not isinstance(value, Sequence) or isinstance(value, (str, bytes))
                    or len(value) != count
                    or any(isinstance(n, bool) or not isinstance(n, int) for n in value)):
                raise ValueError(f"{name} must contain {count} integer pixel coordinates/dimensions")
            object.__setattr__(self, name, tuple(value))
        width, height = self.image_size
        left, top, right, bottom = self.roi
        if width <= 0 or height <= 0 or not (0 <= left < right <= width and 0 <= top < bottom <= height):
            raise ValueError("ROI must be nonempty and within the configured image size")
        if (isinstance(self.baseline_frames, bool) or not isinstance(self.baseline_frames, int)
                or not 3 <= self.baseline_frames <= 128):
            raise ValueError("baseline_frames must be an integer between 3 and 128")
        if (isinstance(self.minimum_yellow_area, bool) or not isinstance(self.minimum_yellow_area, int)
                or not 1 <= self.minimum_yellow_area <= (right-left)*(bottom-top)):
            raise ValueError("minimum_yellow_area must be a positive integer within the ROI area")
        if not 0.3 <= _number(self.baseline_seconds, "baseline_seconds") <= 5:
            raise ValueError("baseline_seconds must be between 0.3 and 5")
        if not 0 < _number(self.rise_pixels, "rise_pixels") < height:
            raise ValueError("rise_pixels must be positive and smaller than the image height")
        dip = _number(self.dip_area_fraction, "dip_area_fraction")
        rise = _number(self.rise_area_fraction, "rise_area_fraction")
        if not 0 < dip < rise <= 1:
            raise ValueError("Require 0 < dip_area_fraction < rise_area_fraction <= 1")
        for name in ("rise_to_dip_seconds", "maximum_frame_age_seconds", "maximum_frame_gap_seconds"):
            if not 0 < _number(getattr(self, name), name) <= 5:
                raise ValueError(f"{name} must be positive and at most 5 seconds")
        if (not isinstance(self.hue_degrees, Sequence) or len(self.hue_degrees) != 2
                or not 0 <= _number(self.hue_degrees[0], "minimum hue")
                < _number(self.hue_degrees[1], "maximum hue") <= 360):
            raise ValueError("hue_degrees must define an increasing interval within 0..360")
        object.__setattr__(self, "hue_degrees", tuple(self.hue_degrees))
        for name in ("minimum_saturation", "minimum_value"):
            if not 0 <= _number(getattr(self, name), name) <= 1:
                raise ValueError(f"{name} must be between 0 and 1")


@dataclass(frozen=True)
class BobberObservation:
    event: str
    triggered: bool
    reason: str
    armed: bool
    arm_id: int
    captured_at: float | None
    yellow_area: int | None
    top_y: int | None
    baseline_area: float | None
    baseline_top_y: float | None
    baseline_samples: int


class BobberDetector:
    """An isolated experiment; no device API, timers, or automatic rearming.

    ``observe`` returns a one-shot ``triggered`` event only after explicit
    ``arm(confirmed_phase="waiting_bite")``. Invalid/stale/nonmonotonic input,
    unexpected geometry, or another phase disarms the detector. Identical image
    contents cannot supply multiple baseline samples. A phase string is an
    external assertion, not visual recognition performed by this module.
    """

    def __init__(self, config: BobberConfig):
        self.config = config
        self.arm_id = 0
        self.reset()

    def reset(self) -> None:
        self.armed = False
        self._fired = False
        self._last_capture: float | None = None
        self._last_observation: float | None = None
        self._rise_at: float | None = None
        self._baseline: list[tuple[float, int, int, str]] = []
        self._baseline_area: float | None = None
        self._baseline_top: float | None = None

    def arm(self, *, confirmed_phase: str) -> None:
        self.reset()
        if confirmed_phase != "waiting_bite":
            raise ValueError("Arming requires an independently confirmed waiting_bite phase")
        self.arm_id += 1
        self.armed = True

    def _result(self, event: str, reason: str, timestamp: float | None = None,
                area: int | None = None, top: int | None = None) -> BobberObservation:
        return BobberObservation(event, event == "triggered", reason, self.armed,
                                 self.arm_id, timestamp, area, top,
                                 self._baseline_area, self._baseline_top, len(self._baseline))

    def _fail(self, reason: str) -> BobberObservation:
        self.reset()
        return self._result("reset", reason)

    def observe(self, frame: Image.Image | str | Path, *, captured_at: float,
                observed_at: float, confirmed_phase: str) -> BobberObservation:
        """Process one frame without waiting or touching; timestamps are seconds."""
        if confirmed_phase != "waiting_bite":
            return self._fail("Phase is not independently confirmed waiting_bite")
        if not self.armed:
            return self._result("unarmed", "Explicit arm is required")
        try:
            captured_at = _number(captured_at, "captured_at")
            observed_at = _number(observed_at, "observed_at")
            if observed_at < captured_at:
                raise ValueError("Capture timestamp is in the future")
            if observed_at - captured_at > self.config.maximum_frame_age_seconds:
                raise ValueError("Frame is stale at observation time")
            if self._last_capture is not None:
                if captured_at <= self._last_capture:
                    raise ValueError("Capture timestamps must be strictly increasing")
                if observed_at < self._last_observation:
                    raise ValueError("Observation timestamps must be monotonic")
                if captured_at-self._last_capture > self.config.maximum_frame_gap_seconds:
                    raise ValueError("Frame gap exceeds the configured freshness limit")
            if isinstance(frame, Image.Image):
                image = frame.convert("RGB")
            else:
                with Image.open(frame) as source:
                    image = source.convert("RGB")
            if image.size != self.config.image_size:
                raise ValueError("Frame geometry differs from the configured image size")
            fingerprint = hashlib.sha256(image.tobytes()).hexdigest()
            area = 0
            top_y: int | None = None
            region = image.crop(self.config.roi)
            pixels = region.tobytes()
            for offset in range(region.width * region.height):
                red, green, blue = pixels[offset*3:offset*3+3]
                hue, saturation, value = colorsys.rgb_to_hsv(red/255, green/255, blue/255)
                if (self.config.hue_degrees[0] <= hue*360 <= self.config.hue_degrees[1]
                        and saturation >= self.config.minimum_saturation
                        and value >= self.config.minimum_value):
                    area += 1
                    if top_y is None:
                        top_y = self.config.roi[1] + offset // region.width
        except (OSError, ValueError, TypeError) as exc:
            return self._fail(str(exc))
        self._last_capture = captured_at
        self._last_observation = observed_at
        if self._fired:
            return self._result("already_triggered", "This arm has already emitted its one event", captured_at, area, top_y)

        if self._baseline_area is None:
            if area < self.config.minimum_yellow_area:
                self._baseline.clear()
                return self._result("collecting_baseline", "Insufficient yellow area for baseline", captured_at, area, top_y)
            if self._baseline and abs(top_y-statistics.median(row[2] for row in self._baseline)) >= self.config.rise_pixels:
                self._baseline.clear()
            if fingerprint in {row[3] for row in self._baseline}:
                return self._result("collecting_baseline", "Identical image does not count as a distinct baseline frame", captured_at, area, top_y)
            self._baseline.append((captured_at, area, top_y, fingerprint))
            self._baseline = self._baseline[-128:]
            if (len(self._baseline) < self.config.baseline_frames
                    or captured_at-self._baseline[0][0] < self.config.baseline_seconds):
                return self._result("collecting_baseline", "Need distinct baseline frames spanning the minimum duration", captured_at, area, top_y)
            self._baseline_area = statistics.median(row[1] for row in self._baseline)
            self._baseline_top = statistics.median(row[2] for row in self._baseline)
            return self._result("baseline_ready", "Provisional baseline collected", captured_at, area, top_y)

        if self._rise_at is not None:
            if captured_at-self._rise_at > self.config.rise_to_dip_seconds:
                self._rise_at = None
            elif area <= self._baseline_area*self.config.dip_area_fraction:
                self._fired = True
                return self._result("triggered", "Provisional rise followed by area drop within the configured interval", captured_at, area, top_y)
        if (top_y is not None and top_y <= self._baseline_top-self.config.rise_pixels
                and area >= self._baseline_area*self.config.rise_area_fraction):
            if self._rise_at is None:
                self._rise_at = captured_at
            return self._result("rise", "Provisional upward feature observed; waiting for area drop", captured_at, area, top_y)
        return self._result("waiting", "No complete rise-then-dip sequence", captured_at, area, top_y)
