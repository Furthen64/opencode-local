#!/usr/bin/env python3
"""
opencode-local setup utility
Interactively generates ~/.config/opencode/opencode.json and
~/.local/share/opencode/auth.json for a locally-hosted OpenAI-compatible LLM.

Requirements: Python 3.11+, no third-party libraries.
Platform: Ubuntu / Linux
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OPENCODE_CONFIG_DIR = Path.home() / ".config" / "opencode"
OPENCODE_DATA_DIR = Path.home() / ".local" / "share" / "opencode"
CONFIG_PATH = OPENCODE_CONFIG_DIR / "opencode.json"
AUTH_PATH = OPENCODE_DATA_DIR / "auth.json"

SCHEMA_URL = "https://opencode.ai/config.json"

PRESET_URLS: dict[str, str] = {
    "1": ("Ollama", "http://localhost:11434/v1"),
    "2": ("LM Studio", "http://127.0.0.1:1234/v1"),
    "3": ("Jan", "http://localhost:1337/v1"),
    "4": ("vLLM (default port)", "http://localhost:8000/v1"),
    "5": ("Custom", ""),
}

CONNECT_TIMEOUT = 5  # seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _print_banner() -> None:
    print(
        "\n"
        "╔══════════════════════════════════════════════════════╗\n"
        "║         opencode-local — quick setup wizard          ║\n"
        "╚══════════════════════════════════════════════════════╝\n"
        "\n"
        "This wizard will:\n"
        "  1. Ask a few questions about your local LLM server\n"
        "  2. Fetch the list of available models from that server\n"
        "  3. Back up your existing opencode config (if any)\n"
        "  4. Write a new opencode.json and auth.json\n"
    )


def _prompt(message: str, default: str = "") -> str:
    """Prompt the user, returning the default if they press Enter."""
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{message}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        sys.exit(1)
    return value if value else default


def _prompt_secret(message: str, default: str = "") -> str:
    """Prompt for a potentially sensitive value (no masking needed — local key)."""
    return _prompt(message, default)


def _choose_base_url() -> str:
    """Ask the user to pick a preset or enter a custom URL."""
    print("Choose your local LLM server:")
    for key, (name, url) in PRESET_URLS.items():
        suffix = f"  ({url})" if url else ""
        print(f"  {key}) {name}{suffix}")
    print()

    while True:
        choice = _prompt("Enter number or type a URL directly", "1").strip()
        if choice in PRESET_URLS:
            _, url = PRESET_URLS[choice]
            if url:
                return url
            # Custom
            return _prompt("Enter the base URL (include /v1)", "http://localhost:8080/v1")
        # User typed a URL directly
        if choice.startswith("http://") or choice.startswith("https://"):
            return choice.rstrip("/")
        print("  Please enter a number from the list or a full URL starting with http(s)://")


def _fetch_models(base_url: str, api_key: str) -> list[str]:
    """
    Try to fetch the model list from <base_url>/models.
    Returns a (possibly empty) list of model IDs on success.
    Raises urllib.error.URLError / OSError on connection failure.
    """
    url = f"{base_url.rstrip('/')}/models"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=CONNECT_TIMEOUT) as resp:
        body = resp.read().decode()
    data = json.loads(body)
    # OpenAI /v1/models returns {"object":"list","data":[{"id":...},...]}.
    # Some servers omit the envelope.
    if isinstance(data, list):
        models = data
    else:
        models = data.get("data", [])
    return sorted(item.get("id", "") for item in models if item.get("id"))


def _select_model(models: list[str]) -> str:
    """Let the user pick a model from a numbered list or type one manually."""
    if models:
        print(f"\nFound {len(models)} model(s) on the server:")
        for idx, m in enumerate(models, 1):
            print(f"  {idx}) {m}")
        print()
        while True:
            choice = _prompt(
                "Pick a model number or type the model name", "1" if models else ""
            )
            if choice.isdigit():
                idx = int(choice)
                if 1 <= idx <= len(models):
                    return models[idx - 1]
                print(f"  Please enter a number between 1 and {len(models)}.")
            elif choice:
                return choice
    else:
        return _prompt("No models found — enter the model name manually", "")


def _backup_config() -> None:
    """Backup existing opencode.json with a timestamp suffix."""
    if not CONFIG_PATH.exists():
        return
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = CONFIG_PATH.with_suffix(f".{ts}.bak")
    shutil.copy2(CONFIG_PATH, backup)
    print(f"  Backed up existing config → {backup}")


def _slugify(text: str) -> str:
    """Turn an arbitrary string into a simple identifier (lowercase, hyphens)."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "localllm"


def _build_opencode_json(
    provider_id: str,
    provider_name: str,
    base_url: str,
    model_id: str,
) -> dict:
    return {
        "$schema": SCHEMA_URL,
        "provider": {
            provider_id: {
                "npm": "@ai-sdk/openai-compatible",
                "name": provider_name,
                "options": {
                    "baseURL": base_url,
                },
                "models": {
                    model_id: {"name": model_id},
                },
            }
        },
        "model": f"{provider_id}/{model_id}",
        "small_model": f"{provider_id}/{model_id}",
    }


def _build_auth_json(existing: dict, provider_id: str, api_key: str) -> dict:
    updated = dict(existing)
    updated[provider_id] = {"type": "api", "key": api_key}
    return updated


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------


def main() -> None:
    _print_banner()

    # ── 1. Provider name ────────────────────────────────────────────────────
    provider_name = _prompt("Provider display name", "Local LLM")
    provider_id = _slugify(provider_name)

    # ── 2. Base URL ──────────────────────────────────────────────────────────
    base_url = _choose_base_url()

    # ── 3. API key ───────────────────────────────────────────────────────────
    print(
        "\nMost local servers accept any placeholder key.\n"
        "Leave blank to use the default placeholder 'sk-local'."
    )
    api_key = _prompt_secret("API key", "sk-local")

    # ── 4. Fetch models ───────────────────────────────────────────────────────
    print(f"\nConnecting to {base_url}/models …")
    models: list[str] = []
    try:
        models = _fetch_models(base_url, api_key)
        if models:
            print(f"  ✓ Connected — {len(models)} model(s) available.")
        else:
            print("  ✓ Connected, but no models were returned.")
    except (urllib.error.URLError, OSError, json.JSONDecodeError, socket.timeout) as exc:
        print(f"  ✗ Could not reach the server: {exc}")
        print("  You can still enter a model name manually.\n")

    # ── 5. Choose model ───────────────────────────────────────────────────────
    model_id = _select_model(models)
    while not model_id:
        print("  A model name is required.")
        model_id = _prompt("Enter the model name to use", "")

    # ── 6. Confirm ────────────────────────────────────────────────────────────
    print(
        f"\n─── Summary ───────────────────────────────────────────\n"
        f"  Provider ID  : {provider_id}\n"
        f"  Provider name: {provider_name}\n"
        f"  Base URL     : {base_url}\n"
        f"  Model        : {model_id}\n"
        f"  Config file  : {CONFIG_PATH}\n"
        f"  Auth file    : {AUTH_PATH}\n"
        f"───────────────────────────────────────────────────────\n"
    )
    confirm = _prompt("Write these files? [Y/n]", "y").lower()
    if confirm not in {"y", "yes"}:
        print("Aborted — no files were written.")
        sys.exit(0)

    # ── 7. Backup + write ─────────────────────────────────────────────────────
    print()
    _backup_config()

    opencode_data = _build_opencode_json(provider_id, provider_name, base_url, model_id)
    _write_json(CONFIG_PATH, opencode_data)
    print(f"  Wrote {CONFIG_PATH}")

    existing_auth: dict = {}
    if AUTH_PATH.exists():
        try:
            existing_auth = json.loads(AUTH_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    auth_data = _build_auth_json(existing_auth, provider_id, api_key)
    _write_json(AUTH_PATH, auth_data)
    print(f"  Wrote {AUTH_PATH}")

    print(
        "\n✓ Done!  You can now run opencode and it will use your local LLM.\n"
        "  To change settings later just re-run this script or edit the\n"
        f"  config file directly at {CONFIG_PATH}\n"
    )


if __name__ == "__main__":
    if sys.version_info < (3, 11):
        sys.exit("Python 3.11 or newer is required.")
    main()
