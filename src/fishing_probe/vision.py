"""Conservative, local screenshot comparison for explicitly calibrated states.

This is a template comparator, not a general game recognizer. It does not learn
states, locate buttons, infer a bite from a timer, or control a device. A human
must verify the screenshot geometry and each distinctive, static region before
enabling calibration. Uncalibrated screens produce ``unknown``.

State names describe reference images only. In particular, this stateless
comparison cannot establish bobber motion or that a fish has just bitten; the
presence of a HIT button is not independent evidence of a bite. A controller
must separately verify its trigger before using a ``bite`` match for input.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import io
import math
from numbers import Real
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageStat


STATES = frozenset({
    "cast_ready", "waiting_bite", "bite", "reeling", "landing", "result",
    "record_popup", "buff_popup", "sale_offer", "sale_pending", "unknown",
})


@dataclass(frozen=True)
class Observation:
    state: str
    confidence: float
    reason: str
    evidence: tuple[dict[str, Any], ...] = ()


def _size(value: Any, name: str) -> tuple[int, int]:
    if isinstance(value, Mapping):
        value = (value.get("width"), value.get("height"))
    if (not isinstance(value, Sequence) or isinstance(value, (str, bytes))
            or len(value) != 2
            or any(isinstance(n, bool) or not isinstance(n, int) or n <= 0 for n in value)):
        raise ValueError(f"{name} must contain two positive integer dimensions")
    return value[0], value[1]


def _orientation(size: tuple[int, int]) -> str:
    if size[0] == size[1]:
        raise ValueError("Square images do not establish device orientation")
    return "landscape" if size[0] > size[1] else "portrait"


def _region(value: Any, logical_size: tuple[int, int], scale: float) -> tuple[int, int, int, int]:
    if (not isinstance(value, Sequence) or isinstance(value, (str, bytes))
            or len(value) != 4
            or any(isinstance(n, bool) or not isinstance(n, int) for n in value)):
        raise ValueError("Each region must be [x, y, width, height] in integer logical points")
    x, y, width, height = value
    if (x < 0 or y < 0 or width <= 0 or height <= 0
            or x + width > logical_size[0] or y + height > logical_size[1]):
        raise ValueError("Template region is outside the logical window")
    bounds = (x * scale, y * scale, (x + width) * scale, (y + height) * scale)
    if any(not math.isclose(n, round(n), abs_tol=1e-6) for n in bounds):
        raise ValueError("Template region boundaries must map to exact screenshot pixels")
    return tuple(round(n) for n in bounds)


class PreparedObserver:
    """Validate and freeze reference ROI pixels once; compare decoded frames.

    Each region must independently satisfy its template's normalized RGB error
    threshold (0 to 0.1). Multiple matching states remain unknown. Hashes describe
    the exact reference file bytes loaded for this run; later file edits cannot
    change the cached pixels. These hashes alone do not establish user approval.
    """

    def __init__(self, *, logical_size, calibration, base_dir="."):
        if not isinstance(calibration, Mapping) or calibration.get("manually_verified") is not True:
            raise ValueError("Screenshot calibration has not been manually verified")
        self.logical_size = _size(calibration.get("logical_size"), "calibrated logical_size")
        self.screenshot_size = _size(calibration.get("screenshot_size"), "calibrated screenshot_size")
        actual_logical = _size(logical_size, "current logical_size")
        if actual_logical != self.logical_size:
            raise ValueError("Current logical window differs from calibration")
        orientation = _orientation(self.logical_size)
        if (_orientation(self.screenshot_size) != orientation
                or calibration.get("orientation") != orientation):
            raise ValueError("Calibrated logical/pixel dimensions and orientation disagree")
        scale = self.screenshot_size[0] / self.logical_size[0]
        if not math.isclose(scale, self.screenshot_size[1] / self.logical_size[1], rel_tol=0, abs_tol=1e-6):
            raise ValueError("Screenshot pixel-to-point scale is not uniform")
        templates = calibration.get("templates")
        if not isinstance(templates, list) or not templates:
            raise ValueError("No manually calibrated state templates are available")
        self._templates = []
        for template in templates:
            if not isinstance(template, Mapping) or template.get("manually_verified") is not True:
                raise ValueError("A template has not been manually verified")
            state = template.get("state")
            if not isinstance(state, str) or state not in STATES - {"unknown"}:
                raise ValueError("Template state is unsupported")
            threshold = template.get("max_error")
            if (isinstance(threshold, bool) or not isinstance(threshold, Real)
                    or not math.isfinite(threshold) or not 0 <= threshold <= 0.1):
                raise ValueError("Template max_error must be finite and between 0 and 0.1")
            reference = template.get("reference")
            if not isinstance(reference, str) or not reference.strip():
                raise ValueError("Template reference must identify a local screenshot file")
            regions = template.get("regions")
            if not isinstance(regions, list) or not regions:
                raise ValueError("Template needs at least one calibrated static region")
            bounds = tuple(_region(region, self.logical_size, scale) for region in regions)
            reference_path = Path(reference)
            if not reference_path.is_absolute():
                reference_path = Path(base_dir) / reference_path
            raw = reference_path.read_bytes()
            with Image.open(io.BytesIO(raw)) as source:
                if source.size != self.screenshot_size:
                    raise ValueError("Reference screenshot resolution/orientation differs from calibration")
                reference_image = source.convert("RGB")
            self._templates.append({
                "state": state, "reference": str(reference_path),
                "reference_sha256": hashlib.sha256(raw).hexdigest(),
                "regions": tuple(tuple(region) for region in regions), "bounds": bounds,
                "reference_regions": tuple(reference_image.crop(box) for box in bounds),
                "max_error": threshold,
            })

    def reference_manifest(self) -> list[dict[str, Any]]:
        return [{key: template[key] for key in (
            "state", "reference", "reference_sha256", "regions", "max_error",
        )} for template in self._templates]

    def observe_image(self, image: Image.Image, *, logical_size) -> Observation:
        evidence: list[dict[str, Any]] = []
        try:
            if _size(logical_size, "current logical_size") != self.logical_size:
                raise ValueError("Current logical window differs from calibration")
            if not isinstance(image, Image.Image) or image.size != self.screenshot_size:
                raise ValueError("Current screenshot resolution/orientation differs from calibration")
            current = image if image.mode == "RGB" else image.convert("RGB")
            matches = []
            for template in self._templates:
                errors = [
                    sum(ImageStat.Stat(ImageChops.difference(current.crop(box), reference)).mean) / (3 * 255)
                    for box, reference in zip(template["bounds"], template["reference_regions"])
                ]
                maximum_error = max(errors)
                detail = {
                    **{key: template[key] for key in (
                        "state", "reference", "reference_sha256", "max_error",
                    )},
                    "regions": [list(region) for region in template["regions"]],
                    "region_errors": errors, "maximum_error": maximum_error,
                    "matched": maximum_error <= template["max_error"],
                }
                evidence.append(detail)
                if detail["matched"]:
                    matches.append(detail)
            states = {match["state"] for match in matches}
            if not states:
                return Observation("unknown", 0.0, "No calibrated template matched", tuple(evidence))
            if len(states) != 1:
                return Observation("unknown", 0.0, "Ambiguous match to multiple states", tuple(evidence))
            return Observation(
                states.pop(), 1 - min(match["maximum_error"] for match in matches),
                "All calibrated regions of a template matched", tuple(evidence),
            )
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return Observation("unknown", 0.0, str(exc), tuple(evidence))


def observe_screenshot(
    screenshot: str | Path,
    *,
    logical_size: Sequence[int] | Mapping[str, int],
    calibration: Mapping[str, Any] | None,
    base_dir: str | Path = ".",
) -> Observation:
    """One-off file comparison; use PreparedObserver for repeated live frames.

    Calibration contains manually_verified, logical_size, screenshot_size,
    orientation, and templates with state/reference/regions/max_error plus each
    template's manually_verified flag. Regions use integer logical coordinates.
    Relative references resolve against base_dir. Invalid calibration, ambiguity,
    or geometry mismatch returns unknown; no resizing or inference is performed.
    """
    try:
        observer = PreparedObserver(logical_size=logical_size, calibration=calibration, base_dir=base_dir)
        with Image.open(screenshot) as image:
            return observer.observe_image(image, logical_size=logical_size)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return Observation("unknown", 0.0, str(exc))
