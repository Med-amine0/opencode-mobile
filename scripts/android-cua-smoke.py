#!/usr/bin/env python3
"""
Android Computer-Use Agent (CUA) smoke test for OpenCode Mobile.

Drives an Android emulator via ADB using an LLM vision loop:
  screenshot → vision model → action → repeat

Inspired by:
- openai/openai-cua-sample-app (browser CUA pattern)
- X-PLUG/MobileAgent (ADB + VLM loop)
- TencentQQGYLab/AppAgent (multimodal smartphone agent)

Requirements:
  pip install openai
  ADB in PATH with a connected device/emulator.

Usage:
  # OpenAI
  export OPENAI_API_KEY=sk-...
  python scripts/android-cua-smoke.py

  # Azure OpenAI (with deployment)
  export OPENAI_API_KEY=<key>
  export OPENAI_BASE_URL=https://<resource>.openai.azure.com/openai/deployments/<deployment>/
  python scripts/android-cua-smoke.py --model gpt-4o

  # Google Gemini (via OpenAI-compat)
  export GEMINI_API_KEY=AIza...
  python scripts/android-cua-smoke.py

  # xAI Grok
  export XAI_API_KEY=xai-...
  python scripts/android-cua-smoke.py

  # Any OpenAI-compatible endpoint (LiteLLM, Ollama, etc.)
  export OPENAI_API_KEY=dummy
  export OPENAI_BASE_URL=http://localhost:4000/v1
  python scripts/android-cua-smoke.py --model gpt-4o

  # Custom goal
  python scripts/android-cua-smoke.py --goal "Open settings and toggle dark mode"
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    sys.exit("openai package required: pip install openai")




# ---------------------------------------------------------------------------
# ADB helpers
# ---------------------------------------------------------------------------

def adb(*args: str) -> str:
    """Run an adb command and return stdout."""
    result = subprocess.run(
        ["adb", *args],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0 and "Error" in result.stderr:
        raise RuntimeError(f"adb {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def screenshot_b64() -> str:
    """Capture emulator screenshot and return as base64 PNG."""
    # Use exec-out for direct binary pipe (fastest, no intermediate file on device)
    result = subprocess.run(
        ["adb", "exec-out", "screencap", "-p"],
        capture_output=True, timeout=10,
    )
    if result.returncode == 0 and len(result.stdout) > 100:
        return base64.b64encode(result.stdout).decode()
    # Fallback: screencap on device then pull
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        path = f.name
    try:
        subprocess.run(["adb", "shell", "screencap", "-p", "/sdcard/_cua_screen.png"],
                       capture_output=True, timeout=10)
        subprocess.run(["adb", "pull", "/sdcard/_cua_screen.png", path],
                       capture_output=True, timeout=10)
        return base64.b64encode(Path(path).read_bytes()).decode()
    finally:
        Path(path).unlink(missing_ok=True)


def ui_dump() -> str:
    """Dump UI hierarchy XML and return as string (optional context for LLM)."""
    adb("shell", "uiautomator", "dump", "/sdcard/_cua_ui.xml")
    result = subprocess.run(
        ["adb", "pull", "/sdcard/_cua_ui.xml", "/tmp/_cua_ui.xml"],
        capture_output=True, timeout=10,
    )
    if result.returncode == 0:
        return Path("/tmp/_cua_ui.xml").read_text(errors="replace")
    return ""


def execute_action(action: dict) -> str:
    """Execute an action dict returned by the LLM. Returns status string."""
    act = action.get("type", "")

    if act == "tap":
        x, y = int(action["x"]), int(action["y"])
        adb("shell", "input", "tap", str(x), str(y))
        return f"tapped ({x}, {y})"

    elif act == "type":
        text = action.get("text", "")
        # ADB input text needs escaping
        escaped = text.replace(" ", "%s").replace("&", "\\&").replace(";", "\\;")
        adb("shell", "input", "text", escaped)
        return f"typed '{text}'"

    elif act == "key":
        key = action.get("key", "")
        key_map = {
            "enter": "66", "back": "4", "home": "3",
            "delete": "67", "tab": "61",
        }
        code = key_map.get(key.lower(), key)
        adb("shell", "input", "keyevent", code)
        return f"pressed key {key}"

    elif act == "swipe":
        x1, y1 = int(action["x1"]), int(action["y1"])
        x2, y2 = int(action["x2"]), int(action["y2"])
        duration = int(action.get("duration", 300))
        adb("shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration))
        return f"swiped ({x1},{y1})->({x2},{y2})"

    elif act == "wait":
        secs = float(action.get("seconds", 2))
        time.sleep(secs)
        return f"waited {secs}s"

    elif act == "done":
        return "DONE"

    elif act == "fail":
        return "FAIL: " + action.get("reason", "unknown")

    else:
        return f"unknown action: {act}"


# ---------------------------------------------------------------------------
# LLM CUA loop
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an Android phone automation agent. You control the device by issuing actions.

On each turn you receive a screenshot of the current screen.
Respond with a JSON object for ONE action to take next.

Available actions:
  {"type": "tap", "x": <int>, "y": <int>}
  {"type": "type", "text": "<string>"}
  {"type": "key", "key": "enter|back|home|delete|tab"}
  {"type": "swipe", "x1": <int>, "y1": <int>, "x2": <int>, "y2": <int>, "duration": <ms>}
  {"type": "wait", "seconds": <float>}
  {"type": "done", "summary": "<what was accomplished>"}
  {"type": "fail", "reason": "<why the goal cannot be achieved>"}

Rules:
- Issue exactly ONE action per turn as a JSON object. No markdown, no explanation outside JSON.
- Coordinates are in pixels relative to the screenshot dimensions.
- After typing text, you may need to dismiss the keyboard (tap elsewhere or press back) before tapping buttons.
- When the goal is fully achieved, respond with {"type": "done", ...}.
- If stuck after several attempts, respond with {"type": "fail", ...}.
"""


def call_llm(client: OpenAI, model: str, system: str, history: list) -> str:
    """Call LLM via OpenAI-compatible API with retry on rate limit."""
    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system}] + history,
                max_tokens=300,
                temperature=0,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            if "429" in str(e) and attempt < 2:
                wait = 15 * (attempt + 1)
                print(f"  [rate limited, retrying in {wait}s...]")
                time.sleep(wait)
                continue
            raise


def make_client(model: str):
    """Create OpenAI client. Supports OPENAI_API_KEY, GEMINI_API_KEY, XAI_API_KEY."""
    if os.environ.get("OPENAI_API_KEY"):
        base = os.environ.get("OPENAI_BASE_URL")
        return OpenAI(base_url=base) if base else OpenAI(), model
    if os.environ.get("XAI_API_KEY"):
        return OpenAI(
            api_key=os.environ["XAI_API_KEY"],
            base_url="https://api.x.ai/v1",
        ), "grok-2-vision-1212"
    if os.environ.get("GEMINI_API_KEY"):
        return OpenAI(
            api_key=os.environ["GEMINI_API_KEY"],
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        ), "gemini-2.0-flash"
    sys.exit("Set OPENAI_API_KEY, XAI_API_KEY, or GEMINI_API_KEY")


def run_cua(goal: str, max_steps: int = 30, model: str = "gpt-4o",
            include_ui_xml: bool = False, verbose: bool = True) -> dict:
    """Run the CUA loop until done/fail/max_steps."""
    client, model = make_client(model)
    history = []

    for step in range(1, max_steps + 1):
        # Capture screenshot
        img_b64 = screenshot_b64()

        # Build user message with screenshot
        content = [
            {"type": "text", "text": f"Step {step}. Goal: {goal}\nWhat action should I take next?"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}", "detail": "high"}},
        ]

        # Optionally include UI XML for better element identification
        if include_ui_xml:
            xml = ui_dump()
            if xml:
                # Truncate to avoid token explosion
                content.append({"type": "text", "text": f"UI hierarchy (truncated):\n{xml[:4000]}"})

        history.append({"role": "user", "content": content})

        # Call LLM
        reply = call_llm(client, model, SYSTEM_PROMPT, history)
        history.append({"role": "assistant", "content": reply})

        # Parse action
        try:
            # Handle markdown code fences if model wraps response
            if reply.startswith("```"):
                reply = reply.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            action = json.loads(reply)
        except json.JSONDecodeError:
            if verbose:
                print(f"  [step {step}] Failed to parse: {reply[:100]}")
            continue

        # Execute
        result = execute_action(action)
        if verbose:
            print(f"  [step {step}] {action.get('type', '?')} -> {result}")

        if result == "DONE":
            return {"status": "success", "steps": step, "summary": action.get("summary", "")}
        if result.startswith("FAIL"):
            return {"status": "fail", "steps": step, "reason": action.get("reason", "")}

        # Brief pause between actions for UI to settle
        time.sleep(1.0)

        # Keep history manageable: only last 6 turns (12 messages) + system
        if len(history) > 12:
            history = history[-12:]

    return {"status": "timeout", "steps": max_steps}


# ---------------------------------------------------------------------------
# Smoke test scenarios
# ---------------------------------------------------------------------------

SMOKE_SCENARIOS = [
    {
        "name": "connect_and_send",
        "goal": (
            "Open the OpenCode app (package: ai.opencode.mobile). "
            "If not already connected, tap 'Add Connection', enter IP '100.108.64.76' port '4096', "
            "and tap Connect. Then create a new session, type 'ping', and send the message. "
            "Verify that an assistant response bubble appears. Then report done."
        ),
    },
]


def main():
    parser = argparse.ArgumentParser(description="Android CUA smoke test")
    parser.add_argument("--goal", help="Custom goal (overrides built-in scenarios)")
    parser.add_argument("--model", default="gpt-4o", help="Vision model to use")
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--include-xml", action="store_true", help="Include UI XML in context")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    # Verify ADB
    try:
        devices = adb("devices")
        if "device" not in devices.split("\n", 1)[-1]:
            sys.exit("No ADB device connected. Start emulator first.")
    except FileNotFoundError:
        sys.exit("adb not found in PATH")

    scenarios = [{"name": "custom", "goal": args.goal}] if args.goal else SMOKE_SCENARIOS

    results = []
    for scenario in scenarios:
        if not args.quiet:
            print(f"\n{'='*60}")
            print(f"Scenario: {scenario['name']}")
            print(f"Goal: {scenario['goal'][:80]}...")
            print(f"{'='*60}")

        result = run_cua(
            goal=scenario["goal"],
            max_steps=args.max_steps,
            model=args.model,
            include_ui_xml=args.include_xml,
            verbose=not args.quiet,
        )
        result["scenario"] = scenario["name"]
        results.append(result)

        if not args.quiet:
            print(f"\nResult: {result['status']} in {result['steps']} steps")
            if result.get("summary"):
                print(f"Summary: {result['summary']}")
            if result.get("reason"):
                print(f"Reason: {result['reason']}")

    # Exit code: 0 if all passed
    failed = [r for r in results if r["status"] != "success"]
    if failed:
        print(f"\n{'!'*60}")
        print(f"FAILED: {len(failed)}/{len(results)} scenarios")
        sys.exit(1)
    else:
        print(f"\nAll {len(results)} scenarios passed.")


if __name__ == "__main__":
    main()
