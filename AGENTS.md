# Local iPad Fishing Superstars automation — instructions for Codex

This repository is operated from the user's **local** MacBook Pro and controls an **actual** USB-connected iPad by Appium XCUITest. Do not execute iPad/Xcode commands on a Remote-SSH host, remote Codex sandbox, or simulated device.

## Known environment (reported by the user; re-check locally)
- Mac: Apple M2 Pro; macOS 26.6.2; Xcode 27.2
- Node.js 26.13.0, npm 11.20.0, Python 3.11.5
- Appium 3; installed `xcuitest@12.22.8`
- iPadOS 26.6.2; FREE Apple Account / Personal Team
- Target: independently installed third-party iPad game **Fishing Superstars**; bundle identifier not verified

## Rules
- Work iteratively: DETECT → PREPARE → WDA_SIGN → INSPECT → one TOUCH_PROBE → REPORT.
- Prefer inspecting and reusing the existing setup; never reinstall or downgrade Xcode/Appium/driver without evidence and authorization.
- The initial WDA signature must be associated with a valid Personal Team provisioning profile. Paid developer automatic-provisioning instructions may not work for this free account.
- Never ask for Apple password, 2FA code, signing private keys, certificates, or export of keychain secrets. Let the user interact with Apple's native Xcode/iPad dialogs when required.
- Do not modify or erase the target game, its local data, saves, purchases, network configuration or account. Never invoke `clearApp`, `removeApp`, reset, or restore.
- No unbounded game automation. First screenshot and verify the target. For single gesture test, request the user's explicit approval of safe coordinates; max three gesture repeats even with approval.
- Store operational logs/screenshots only under `runs/`; device identifiers and account data should not be included in the final external report. Never upload gameplay screenshots to third-party sites.
- Use official Appium driver v12.22 and Apple documentation as references. Check real error logs rather than guessing.
- If blocked by a physical action or Apple login/trust, state exactly **one** concrete user action and await it; keep all completed outputs and continue once resolved.
- Status categories: `PASS`, `BLOCKED_USER_ACTION`, `FAILED`, `NOT_RUN`. Do not claim a gesture worked merely because Appium returned without an exception; its in-game effect must be visually observed.

## Commands (from the project root)
```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m fishing_probe doctor
cp config.example.json config.json   # Fill using measured local facts, not guesses
python -m fishing_probe inspect
# Only with user-approved safe test coordinates:
python -m fishing_probe gesture --kind hold --x 300 --y 400 --seconds 0.9 --armed --safe-target-confirmed
```

## Official docs
- https://appium.github.io/appium-xcuitest-driver/12.22/getting-started/provisioning-profile/
- https://appium.github.io/appium-xcuitest-driver/12.22/getting-started/provisioning-profile/full-manual-config/
- https://appium.github.io/appium-xcuitest-driver/12.22/guides/run-preinstalled-wda/
- https://appium.github.io/appium-xcuitest-driver/12.22/reference/execute-methods/
- https://appium.github.io/appium-xcuitest-driver/12.22/guides/gestures/
- https://developer.apple.com/documentation/xcode/enabling-developer-mode-on-a-device
