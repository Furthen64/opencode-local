#!/usr/bin/env python3
"""Configure OpenCode providers without owning credentials or secrets."""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(os.environ.get("OPENCODE_LOCAL_CONFIG_DIR", Path.home() / ".config" / "opencode"))
CONFIG_PATH = CONFIG_DIR / "opencode.json"
METADATA_PATH = CONFIG_DIR / "opencode-local.json"
SCHEMA_URL = "https://opencode.ai/config.json"
VERSION = 1
LOCAL_FREE, REMOTE_PAID = "local-free", "remote-paid"
PRESETS = {
    "1": ("Ollama", "http://localhost:11434/v1"),
    "2": ("LM Studio", "http://127.0.0.1:1234/v1"),
    "3": ("Jan", "http://localhost:1337/v1"),
    "4": ("vLLM", "http://localhost:8000/v1"),
    "5": ("llama.cpp", "http://localhost:8080/v1"),
    "6": ("Custom", ""),
}


def prompt(message: str, default: str = "") -> str:
    try:
        value = input(f"{message}{f' [{default}]' if default else ''}: ").strip()
    except (EOFError, KeyboardInterrupt):
        sys.exit("\nAborted.")
    return value or default


def read(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return dict(default or {})
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(result, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return result


def write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def metadata() -> dict[str, Any]:
    data = read(METADATA_PATH, {"version": VERSION, "providers": {}})
    if data.get("version") != VERSION or not isinstance(data.get("providers"), dict):
        raise ValueError(f"Unsupported or invalid metadata in {METADATA_PATH}")
    return data


def provider_meta(data: dict[str, Any], provider_id: str) -> dict[str, Any]:
    item = data["providers"].setdefault(provider_id, {})
    item.setdefault("availability", "enabled")
    item.setdefault("models", {})
    return item


def model_meta(data: dict[str, Any], provider_id: str, model_id: str) -> dict[str, Any]:
    item = provider_meta(data, provider_id)["models"].setdefault(model_id, {})
    item.setdefault("availability", "enabled")
    return item


def credential_status(provider: dict[str, Any]) -> tuple[str, str | None]:
    credential = provider.get("credential", {})
    if not credential.get("required", False):
        return "NOT REQUIRED", None
    variable = credential.get("environment_variable")
    return ("PRESENT" if variable and os.environ.get(variable) else "MISSING", variable)


def validate_classification(value: str) -> str:
    aliases = {"local": LOCAL_FREE, "free": LOCAL_FREE, "remote": REMOTE_PAID, "paid": REMOTE_PAID}
    value = aliases.get(value.lower(), value.lower())
    if value not in {LOCAL_FREE, REMOTE_PAID}:
        raise ValueError("classification must be local-free or remote-paid")
    return value


def set_credential_reference(config_provider: dict[str, Any], item: dict[str, Any], variable: str | None) -> None:
    """Store only an OpenCode environment placeholder, never a credential."""
    options = config_provider.setdefault("options", {})
    prior = item.get("applied_api_key_reference")
    if variable:
        options["apiKey"] = f"{{env:{variable}}}"
        item["credential"] = {"required": True, "source": "environment", "environment_variable": variable}
        item["applied_api_key_reference"] = variable
    else:
        if prior and options.get("apiKey") == f"{{env:{prior}}}":
            options.pop("apiKey")
        if not options:
            config_provider.pop("options", None)
        item["credential"] = {"required": False}
        item.pop("applied_api_key_reference", None)


def adopt(data: dict[str, Any], config: dict[str, Any]) -> bool:
    """Adopt existing definitions; do not infer cost or credential requirements."""
    changed = False
    for provider_id, provider in config.get("provider", {}).items():
        if not isinstance(provider, dict):
            continue
        if provider_id not in data["providers"]:
            provider_meta(data, provider_id)
            changed = True
        item = provider_meta(data, provider_id)
        if "display_name" not in item and provider.get("name"):
            item["display_name"] = provider["name"]
            changed = True
        for model_id in provider.get("models", {}):
            if model_id not in item["models"]:
                model_meta(data, provider_id, model_id)
                changed = True
    return changed


def has_meaningful_configuration(config: dict[str, Any]) -> bool:
    """Conservatively decide whether OpenCode already has user configuration."""
    for key in ("provider", "providers"):
        providers = config.get(key)
        if isinstance(providers, dict) and providers:
            return True
    return bool(config.get("model") or config.get("small_model"))


def startup_flow() -> str:
    """Return the startup flow without creating or changing any files."""
    if not CONFIG_PATH.exists():
        return "fresh"
    try:
        return "existing" if has_meaningful_configuration(read(CONFIG_PATH)) else "fresh"
    except ValueError:
        # A config we cannot confidently classify is not a fresh configuration.
        return "existing"


def sync() -> None:
    """Apply metadata with documented OpenCode blacklist/disabled_providers fields."""
    config, data = read(CONFIG_PATH), metadata()
    adopt(data, config)
    providers = config.get("provider", {})
    if not isinstance(providers, dict):
        raise ValueError("opencode.json has an invalid provider map")
    disabled_providers = {pid for pid, item in data["providers"].items() if item.get("availability") == "disabled"}
    old = set(data.get("applied_disabled_providers", []))
    user_entries = set(config.get("disabled_providers", [])) - old
    if user_entries or disabled_providers:
        config["disabled_providers"] = sorted(user_entries | disabled_providers)
    else:
        config.pop("disabled_providers", None)
    data["applied_disabled_providers"] = sorted(disabled_providers)
    for provider_id, provider in providers.items():
        if not isinstance(provider, dict):
            continue
        item = provider_meta(data, provider_id)
        disabled_models = {mid for mid, model in item["models"].items() if model.get("availability") == "disabled"}
        old = set(item.get("applied_disabled_models", []))
        user_entries = set(provider.get("blacklist", [])) - old
        if user_entries or disabled_models:
            provider["blacklist"] = sorted(user_entries | disabled_models)
        else:
            provider.pop("blacklist", None)
        item["applied_disabled_models"] = sorted(disabled_models)
    write(CONFIG_PATH, config)
    write(METADATA_PATH, data)


def choose_url() -> str:
    print("Choose your OpenAI-compatible LLM server:")
    for key, (name, url) in PRESETS.items():
        print(f"  {key}) {name}" + (f"  ({url})" if url else ""))
    while True:
        choice = prompt("Enter number or type a URL directly", "1")
        if choice in PRESETS:
            return PRESETS[choice][1] or prompt("Enter base URL (include /v1)", "http://localhost:8080/v1")
        if choice.startswith(("http://", "https://")):
            return choice.rstrip("/")
        print("  Enter a preset number or an http(s) URL.")


def fetch_models(url: str, credential_variable: str | None) -> list[str]:
    headers = {"Accept": "application/json"}
    if credential_variable and os.environ.get(credential_variable):
        headers["Authorization"] = f"Bearer {os.environ[credential_variable]}"
    request = urllib.request.Request(f"{url.rstrip('/')}/models", headers=headers)
    with urllib.request.urlopen(request, timeout=5) as response:
        payload = json.loads(response.read().decode())
    items = payload if isinstance(payload, list) else payload.get("data", [])
    return sorted(item.get("id", "") for item in items if item.get("id"))


def choose_classification() -> str:
    while True:
        choice = prompt("Cost classification: 1) LOCAL / FREE  2) REMOTE / PAID", "1")
        try:
            return validate_classification({"1": LOCAL_FREE, "2": REMOTE_PAID}.get(choice, choice))
        except ValueError:
            print("  Choose 1 (LOCAL / FREE) or 2 (REMOTE / PAID).")


def choose_credential() -> str | None:
    required = prompt("Does this provider require a credential? [y/N]", "n").lower()
    if required not in {"y", "yes"}:
        return None
    while True:
        variable = prompt("Credential environment variable (value is never stored)")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", variable):
            return variable
        print("  Enter a valid environment variable name.")


def wizard(initial_category: str | None = None) -> None:
    print("\nopencode-local — provider setup\n")
    name = prompt("Provider display name", "Local LLM")
    provider_id = prompt("OpenCode provider ID", re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "localllm")
    config = read(CONFIG_PATH, {"$schema": SCHEMA_URL})
    configuring_first_provider = not has_meaningful_configuration(config)
    if provider_id in config.get("provider", {}):
        print(f"Provider '{provider_id}' already exists. Use Edit provider or choose a different provider ID.")
        return
    category, url, variable = initial_category or choose_classification(), choose_url(), choose_credential()
    try:
        models = fetch_models(url, variable)
        print(f"Connected — {len(models)} model(s) returned.")
    except (urllib.error.URLError, OSError, json.JSONDecodeError, socket.timeout) as exc:
        print(f"Could not reach server: {exc}")
        models = []
    for number, model in enumerate(models, 1):
        print(f"  {number}) {model}")
    choice = prompt("Pick model number or type model name", "1" if models else "")
    model_id = models[int(choice) - 1] if choice.isdigit() and 1 <= int(choice) <= len(models) else choice
    while not model_id:
        model_id = prompt("A model name is required")
    config.setdefault("$schema", SCHEMA_URL)
    provider = {"npm": "@ai-sdk/openai-compatible", "name": name, "options": {"baseURL": url}, "models": {model_id: {"name": model_id}}}
    print("\nSummary")
    print(f"  Provider: {name} [{provider_id}]")
    print(f"  Category: {category.upper().replace('-', ' / ')}")
    print(f"  Endpoint: {url}")
    print(f"  Model: {model_id}")
    print(f"  Credential: {variable or 'NOT REQUIRED'}")
    if prompt("Save this provider? [Y/n]", "y").lower() not in {"y", "yes"}:
        print("Nothing was written.")
        return
    config.setdefault("provider", {})[provider_id] = provider
    if configuring_first_provider:
        config["model"] = config["small_model"] = f"{provider_id}/{model_id}"
    data = metadata()
    item = provider_meta(data, provider_id)
    item.update({"display_name": name, "availability": "enabled", "classification": category})
    model_meta(data, provider_id, model_id)["availability"] = "enabled"
    set_credential_reference(provider, item, variable)
    write(CONFIG_PATH, config)
    write(METADATA_PATH, data)
    sync()
    print(f"Wrote {CONFIG_PATH} and {METADATA_PATH}. No credentials or backups were written.")


def tint(value: str, colour: str) -> str:
    return f"\033[{colour}m{value}\033[0m" if sys.stdout.isatty() else value


def status(read_only: bool = False) -> None:
    config, data = read(CONFIG_PATH), metadata()
    if adopt(data, config) and not read_only:
        write(METADATA_PATH, data)
    groups: dict[str, list[tuple[str, dict[str, Any], dict[str, Any]]]] = {LOCAL_FREE: [], REMOTE_PAID: [], "unclassified": []}
    for provider_id, config_provider in config.get("provider", {}).items():
        item = provider_meta(data, provider_id)
        groups.setdefault(item.get("classification", "unclassified"), []).append((provider_id, config_provider, item))
    for category, heading, colour in ((LOCAL_FREE, "LOCAL / FREE", "1;32"), (REMOTE_PAID, "REMOTE / PAID", "1;33"), ("unclassified", "UNCLASSIFIED — SET EXPLICITLY", "1;37")):
        print("\n" + tint(heading, colour))
        if not groups[category]:
            print("  (none)")
        for provider_id, config_provider, item in sorted(groups[category]):
            state = item.get("availability", "enabled").upper()
            disabled = "2" if state == "DISABLED" else colour
            name = item.get("display_name", config_provider.get("name", provider_id))
            credential, variable = credential_status(item)
            print("  " + tint(f"{name} [{provider_id}]", disabled))
            print("    State: {0}".format(tint(state, disabled)))
            endpoint = config_provider.get("options", {}).get("baseURL")
            if endpoint:
                print(f"    Endpoint: {endpoint}")
            print(f"    Credential: {variable or 'NOT REQUIRED'}")
            print("    Credential status: " + tint(credential, "32" if credential in {"PRESENT", "NOT REQUIRED"} else "1;33"))
            if item.get("notes"):
                print(f"    Notes: {item['notes']}")
            for model_id in config_provider.get("models", {}):
                model = model_meta(data, provider_id, model_id)
                model_state = model.get("availability", "enabled").upper()
                effective = " (PROVIDER DISABLED)" if state == "DISABLED" and model_state == "ENABLED" else ""
                label = f"{model_id:<30} {model_state}{effective}{'     PAID' if category == REMOTE_PAID else ''}"
                print("    " + tint(label, "2" if model_state == "DISABLED" or effective else colour))
    print(f"\nMetadata: {METADATA_PATH}")


def set_availability(target: str, availability: str) -> None:
    provider_id, slash, model_id = target.partition("/")
    config, data = read(CONFIG_PATH), metadata()
    if provider_id not in config.get("provider", {}):
        raise ValueError(f"Unknown provider: {provider_id}")
    if slash:
        if model_id not in config["provider"][provider_id].get("models", {}):
            raise ValueError(f"Unknown model: {target}")
        model_meta(data, provider_id, model_id)["availability"] = availability
    else:
        provider_meta(data, provider_id)["availability"] = availability
    write(METADATA_PATH, data)
    sync()
    print(f"{target} is now {availability.upper()}.")


def classify(target: str, classification: str) -> None:
    provider_id, slash, model_id = target.partition("/")
    config, data = read(CONFIG_PATH), metadata()
    if provider_id not in config.get("provider", {}):
        raise ValueError(f"Unknown provider: {provider_id}")
    if slash:
        if model_id not in config["provider"][provider_id].get("models", {}):
            raise ValueError(f"Unknown model: {target}")
        model_meta(data, provider_id, model_id)["classification"] = validate_classification(classification)
    else:
        provider_meta(data, provider_id)["classification"] = validate_classification(classification)
    write(METADATA_PATH, data)
    print(f"{target} is classified as {validate_classification(classification).upper().replace('-', ' / ')}.")


def edit_provider(args: argparse.Namespace) -> None:
    config, data = read(CONFIG_PATH), metadata()
    providers = config.get("provider", {})
    old_id, new_id = args.provider_id, args.new_id or args.provider_id
    if old_id not in providers:
        raise ValueError(f"Unknown provider: {old_id}")
    if new_id != old_id and new_id in providers:
        raise ValueError(f"Provider already exists: {new_id}")
    if args.credential_env is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.credential_env):
        raise ValueError("credential environment variable must be a valid variable name")
    provider = providers.pop(old_id)
    item = data["providers"].pop(old_id, provider_meta(data, old_id))
    providers[new_id] = provider
    data["providers"][new_id] = item
    if args.name:
        provider["name"] = args.name
        item["display_name"] = args.name
    if args.base_url:
        provider.setdefault("options", {})["baseURL"] = args.base_url
    if args.classification:
        item["classification"] = validate_classification(args.classification)
    if args.credential_env is not None:
        set_credential_reference(provider, item, args.credential_env)
    if args.no_credential:
        set_credential_reference(provider, item, None)
    if args.notes is not None:
        if args.notes:
            item["notes"] = args.notes
        else:
            item.pop("notes", None)
    if args.enabled:
        item["availability"] = "enabled"
    if args.disabled:
        item["availability"] = "disabled"
    if new_id != old_id:
        for key in ("model", "small_model"):
            if config.get(key, "").startswith(old_id + "/"):
                config[key] = new_id + config[key][len(old_id):]
        data["applied_disabled_providers"] = [new_id if value == old_id else value for value in data.get("applied_disabled_providers", [])]
        if "disabled_providers" in config:
            config["disabled_providers"] = [new_id if value == old_id else value for value in config["disabled_providers"]]
    write(CONFIG_PATH, config)
    write(METADATA_PATH, data)
    sync()
    print(f"Updated provider {new_id}.")


def choose_provider_kind() -> str | None:
    print("\n1) Local / free model server\n2) Remote / paid API provider\nq) Cancel")
    while True:
        choice = prompt("Choose provider type", "q").lower()
        if choice == "1":
            return LOCAL_FREE
        if choice == "2":
            return REMOTE_PAID
        if choice in {"q", "quit"}:
            return None
        print("  Choose 1, 2, or q.")


def interactive_edit_provider() -> None:
    provider_id = prompt("Provider ID to edit")
    if not provider_id:
        return
    print("Leave fields blank to keep their current value.")
    credential = prompt("Credential environment variable (or '-' for not required)")
    notes = prompt("Notes")
    args = argparse.Namespace(
        provider_id=provider_id,
        new_id=prompt("New provider ID"),
        name=prompt("Display name"),
        base_url=prompt("Base URL"),
        classification=prompt("Classification (local-free/remote-paid)"),
        credential_env=credential or None,
        no_credential=credential == "-",
        notes=notes or None,
        enabled=False,
        disabled=False,
    )
    if credential == "-":
        args.credential_env = None
    edit_provider(args)


def inventory_summary() -> None:
    """Print the short, read-only inventory intended for the home screen."""
    config, data = read(CONFIG_PATH), metadata()
    groups: dict[str, list[tuple[str, dict[str, Any], dict[str, Any]]]] = {LOCAL_FREE: [], REMOTE_PAID: [], "unclassified": []}
    for provider_id, config_provider in config.get("provider", {}).items():
        item = provider_meta(data, provider_id)
        groups.setdefault(item.get("classification", "unclassified"), []).append((provider_id, config_provider, item))
    for category, heading, colour in (
        (LOCAL_FREE, "LOCAL / FREE", "1;32"),
        (REMOTE_PAID, "REMOTE / PAID", "1;33"),
        ("unclassified", "UNCLASSIFIED — SET EXPLICITLY", "1;37"),
    ):
        entries = groups[category]
        if not entries:
            continue
        print(tint(heading, colour))
        for provider_id, config_provider, item in sorted(entries):
            name = item.get("display_name", config_provider.get("name", provider_id))
            model_count = len(config_provider.get("models", {}))
            disabled_models = sum(
                model.get("availability") == "disabled" for model in item.get("models", {}).values()
            )
            state = item.get("availability", "enabled").upper()
            suffix = f"{model_count} model{'s' if model_count != 1 else ''}"
            if disabled_models:
                suffix += f", {disabled_models} disabled"
            if state == "DISABLED":
                suffix += ", provider disabled"
            print("  " + tint(f"{name:<28} {suffix}", "2" if state == "DISABLED" else colour))
        print()


def home() -> None:
    """Interactive, state-aware entry point. Reading this screen never writes."""
    print("\nopencode-local\n")
    if startup_flow() == "fresh":
        print("No existing OpenCode provider configuration was found.\n\nLet's set up your first provider.")
        kind = choose_provider_kind()
        if kind:
            wizard(kind)
        return

    print("Existing OpenCode configuration found.\n")
    inventory_summary()
    print("What do you want to do?\n\n1) View provider details\n2) Add provider\n3) Edit provider\n4) Enable / disable provider or model\n5) Sync\nq) Quit")
    choice = prompt("Choose an action", "q").lower()
    if choice in {"q", "quit"}:
        return
    if choice == "1":
        status(read_only=True)
    elif choice == "2":
        kind = choose_provider_kind()
        if kind:
            wizard(kind)
    elif choice == "3":
        interactive_edit_provider()
    elif choice == "4":
        target = prompt("Provider or provider/model")
        action = prompt("Enable or disable", "disable").lower()
        if action not in {"enable", "disable"}:
            print("No change made; enter enable or disable.")
        else:
            set_availability(target, action + "d")
    elif choice == "5":
        sync()
        print("OpenCode availability settings synchronized.")
    else:
        print("No change made; choose an item from the menu.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure OpenCode availability and credential references.")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("setup", help="interactively add a provider")
    sub.add_parser("status", help="show provider, credential, and model status")
    sub.add_parser("sync", help="apply availability metadata")
    for command in ("enable", "disable"):
        sub.add_parser(command, help=f"{command} a provider or model").add_argument("target", help="provider or provider/model")
    classify_parser = sub.add_parser("classify", help="set explicit local/free or remote/paid classification")
    classify_parser.add_argument("target", help="provider or provider/model")
    classify_parser.add_argument("classification", help="local-free or remote-paid")
    edit = sub.add_parser("edit-provider", help="edit provider metadata and OpenCode-safe settings")
    edit.add_argument("provider_id")
    edit.add_argument("--id", dest="new_id")
    edit.add_argument("--name")
    edit.add_argument("--base-url")
    edit.add_argument("--classification")
    edit.add_argument("--credential-env", metavar="VARIABLE")
    edit.add_argument("--no-credential", action="store_true")
    edit.add_argument("--notes")
    state = edit.add_mutually_exclusive_group()
    state.add_argument("--enabled", action="store_true")
    state.add_argument("--disabled", action="store_true")
    args = parser.parse_args()
    try:
        if args.command is None:
            home()
        elif args.command == "setup":
            kind = choose_provider_kind()
            if kind:
                wizard(kind)
        elif args.command == "status":
            status()
        elif args.command == "sync":
            sync()
            print("OpenCode availability settings synchronized.")
        elif args.command in {"enable", "disable"}:
            set_availability(args.target, args.command + "d")
        elif args.command == "classify":
            classify(args.target, args.classification)
        else:
            edit_provider(args)
    except ValueError as exc:
        sys.exit(f"Error: {exc}")


if __name__ == "__main__":
    if sys.version_info < (3, 11):
        sys.exit("Python 3.11 or newer is required.")
    main()
