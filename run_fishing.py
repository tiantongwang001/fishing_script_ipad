#!/usr/bin/env python3
"""Launch the fixed clicker with this project's existing Python environment.

Running this file starts the configured bounded fishing loop.
Start on the Failure/Continue screen or CAST (with no popup).
Use --preview to inspect the schedule without connecting to the iPad.
"""
from __future__ import annotations

import os
import json
import tempfile
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent

# 手动启动后最多钓鱼几轮（1–1000）；最后一轮出售后停止，不再点击继续或修理。
MAX_ROUNDS = 100
# 总输入时限（1–86400秒；3600秒=1小时），与轮数上限任一先到即停止；Ctrl+C也可停止。
MAX_RUN_SECONDS = 3600

# CAST抛竿调用返回后，等待多少秒再HIT提竿（0–60秒）。
HOOK_WAIT_SECONDS = 7.80

# 每轮上半鱼轮按压次数与单次时长（秒）；0.55秒已单轮钓获，0.56可能失败，0.58秒见失败，非通用安全阈值。
REEL_PULSES = 10
#总按压20轮
REEL_HOLD_SECONDS = 0.55
# 原生按压返回后额外等待；真实间隔还包含串行调用、前台和尺寸检查。
REEL_RELEASE_SECONDS = 0.0
# 每完成多少轮尝试修理一次（成功失败均计数）；0关闭，每次启动重新计数。
AUTO_REPAIR = 51
# 继续调用返回→开始CAST的总目标间隔（秒），包含修理/其它前置动作耗时。
# 前置动作超时则立即CAST；0表示不补等待，不能强行压缩修理耗时。
CONTINUE_TO_CAST_SECONDS = 0.0
# 修理每步按压返回后的等待；缩短后需在真实修理弹窗验证。
REPAIR_STEP_WAIT_SECONDS = 0.1


def prepare_default_recipe() -> Path:
    """Snapshot the editable settings so logs and approvals bind actual inputs."""
    if isinstance(REEL_PULSES, bool) or not isinstance(REEL_PULSES, int) or not 1 <= REEL_PULSES <= 120:
        raise ValueError("REEL_PULSES must be an integer between 1 and 120")
    if isinstance(MAX_ROUNDS, bool) or not isinstance(MAX_ROUNDS, int) or not 1 <= MAX_ROUNDS <= 1000:
        raise ValueError("MAX_ROUNDS must be an integer between 1 and 1000")
    if isinstance(AUTO_REPAIR, bool) or not isinstance(AUTO_REPAIR, int) or not 0 <= AUTO_REPAIR <= 1000:
        raise ValueError("AUTO_REPAIR must be an integer between 0 and 1000")
    payload = json.loads((PROJECT_ROOT / "clicker_recipe.json").read_text())
    payload["settings"]["reel_pulses"] = REEL_PULSES
    payload["settings"]["cycles"] = MAX_ROUNDS
    payload["settings"]["max_elapsed_seconds"] = MAX_RUN_SECONDS
    payload["settings"]["hook_wait_seconds"] = HOOK_WAIT_SECONDS
    payload["settings"]["reel_release_seconds"] = REEL_RELEASE_SECONDS
    payload["settings"]["continue_to_cast_seconds"] = CONTINUE_TO_CAST_SECONDS
    payload["settings"]["repair_step_wait_seconds"] = REPAIR_STEP_WAIT_SECONDS
    payload["settings"]["repair_every_rounds"] = AUTO_REPAIR
    payload["actions"]["reel_upper"]["press_seconds"] = REEL_HOLD_SECONDS
    if not AUTO_REPAIR:
        for name in ("repair_open", "repair_all", "repair_confirm"):
            payload["actions"].pop(name, None)
            payload.get("button_geometry", {}).get("action_controls", {}).pop(name, None)
    runs = PROJECT_ROOT / "runs"
    runs.mkdir(exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="launcher_", dir=runs))
    path = folder / "recipe.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    preview = "--preview" in arguments
    arguments = [argument for argument in arguments if argument != "--preview"]
    if preview and "--execute" in arguments:
        print("--preview cannot be combined with --execute", file=sys.stderr)
        return 2
    python = PROJECT_ROOT / ".venv" / "bin" / "python"
    if not python.is_file():
        print(f"Project Python environment is missing: {python}", file=sys.stderr)
        return 2
    if not any(argument == "--recipe" or argument.startswith("--recipe=") for argument in arguments):
        arguments = ["--recipe", str(prepare_default_recipe()), *arguments]
    if not preview and not any(argument in ("--help", "-h") for argument in arguments):
        # The user explicitly requested direct manual startup of these approved actions.
        for flag in ("--execute", "--armed", "--safe-target-confirmed"):
            if flag not in arguments:
                arguments.append(flag)
    os.chdir(PROJECT_ROOT)
    os.execv(str(python), [str(python), "-m", "fishing_probe", "clicker", *arguments])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
