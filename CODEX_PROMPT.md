# Codex task: Configure & verify real iPad touch automation for Fishing Superstars

You are executing LOCALLY on my MacBook Pro in the current VS Code workspace. Read AGENTS.md and README_CN.md first. I want you to **do the work with your shell/tools**, not merely explain shell commands for me to type. If a native Apple trust/login/Personal Team action truly requires my intervention, give me one short, exact instruction, then resume the rest after I complete it.

**Known facts**: Mac M2 Pro; macOS 26.6.2; Xcode 27.2 already working via `/Applications/Xcode.app/Contents/Developer`; Python 3.11.5; Node 26.13.0; Appium XCUITest driver `xcuitest@12.22.8` ALREADY INSTALLED; actual iPad uses iPadOS 26.6.2; I have a FREE Apple Account (Personal Team); target third-party game is Fishing Superstars. The iPad will be USB-connected and unlocked. You must verify facts locally, not assume connectivity or signing success.

**Objective/acceptance**:
1. Mac and iPad appear in Xcode/`xcrun devicectl list devices` with actual device identifier recorded locally.
2. WDA is genuinely signed for my free Personal Team, installed/launchable on the real iPad, or a precise blocker is reported.
3. Appium server successfully creates a Python XCUITest session on the physical iPad.
4. Find Fishing Superstars using `mobile: listApps` on the device; do not assume its bundle ID. Launch it without reinstalling or resetting, save timestamped screenshot and report window/screenshot sizes.
5. Implement parameterized long press, drag and W3C single-finger continuous path functions with unit tests. AFTER I approve safe target coordinates, run at most one touch test initially and capture before/after screenshots. Never equate a successful API return with verified in-game input acceptance.
6. Produce final `runs/STATUS.md` summarizing PASS/BLOCKED/FAILED per stage, commands actually run, paths to logs/screenshots and exactly what the next step is.

**Steps you should execute**:

E00 DETECT: Confirm this is the LOCAL Mac and not a VS Code Remote SSH window; check `uname -m`, `sw_vers`, `xcode-select -p`, `xcodebuild -version`, `node -v`, `appium --version`, `appium driver list --installed`, `appium driver doctor xcuitest`, `xcrun devicectl list devices`. Keep logs in `runs/`. If iPad not visible, identify cable/trust/Developer Mode blocker and stop at that hardware boundary instead of pretending to proceed.

E01 PREPARE: Reuse the existing Appium XCUITest driver. Create project-local venv, `python -m pip install -e .`, run local tests and `python -m fishing_probe doctor`. Use `config.example.json` to create a local git-ignored `config.json` with verified UDID, verified WDA bundle id, and correct WDA mode. Never install global packages or alter unrelated projects without authorization.

E02 WDA_SIGN: For my FREE Apple Account, follow the official MANUAL WDA provisioning instructions. Locate the actual WDA project using `appium driver run xcuitest open-wda`; inspect existing signing state, Personal Team, bundle ID and provisioning. Attempt reproducible local Xcode configuration/`xcodebuild` when feasible. If manual Apple login, Team selection, native trust confirmation or Product → Test is unavoidable, open the correct Xcode project and ask me for exactly the required click sequence. Do not request passwords/2FA in chat and do not use paid-only wildcard provisioning assumptions. First favor normal `xcodebuild`-managed WDA using the signed WDA project (`wda_mode=xcode`). When evidence indicates WDA is already installed and properly signed but rebuild fails, consider `wda_mode=preinstalled` with its actual bundle identifier and any required iOS 17+ XCTest framework constraints. Do not delete embedded frameworks or patch signed bundles without reviewing the official procedure and preserving a copy.

E03 CONNECT + INSPECT: Start an Appium server bound to 127.0.0.1:4723 with log stored in `runs/`; create a real Python session, use `mobile: listApps` and identify the game from its actual metadata, launch via `mobile: launchApp`, get `driver.get_window_size()`, save screenshot and metadata. Account for the fact that screenshot pixels and logical iPad points may differ. If a step fails, inspect the actual Appium/Xcode logs and fix the specific error. Do not reset game data.

E04 TOUCH_PROBE: Prepare and validate the included gesture commands. Show me a screenshot and a proposed safe test coordinate first. Only after I explicitly confirm that coordinate, use `python -m fishing_probe gesture ... --armed --safe-target-confirmed` for a single long-press or drag; record before/after and ask whether the game actually reacted. A continuous W3C path should be unit-tested first, not assumed compatible with the game. Never run game grinding or autonomous loops without separate approval and a bounded stop condition.

E05 REPORT: Write `runs/STATUS.md` with sections for environment/device/WDA/Appium/game capture/gesture verification, each using PASS, BLOCKED_USER_ACTION, FAILED or NOT_RUN. State the first blocking action, if any, using precise Xcode/iPad UI instructions. List modifications, logs and next runnable command. Do not claim success on incomplete stages.

**Constraints**: No `sudo` unless you ask me and it is strictly necessary. No password/2FA/key export. No reinstall, clearing data, purchasing, account switching, device reset, game cheating bypass/anti-cheat evasion, or uncontrolled repeated input. Never run on a remote server. Minimize questions: ask only for physical Apple/Xcode permission or a confirmed safe game coordinate when needed. Continue as far as you can before stopping.

Relevant official sources are linked in AGENTS.md. Be aware XCUITest driver 12.22 documents `mobile: listApps` returning a map of bundle IDs to app metadata, `mobile: dragFromToForDuration` duration being the *initial hold duration* (0.5–60 seconds), and `appium:updatedWDABundleId` may require `.xctrunner` suffix handling when starting preinstalled WDA.
