# opencode-local

`opencode-local` is a terminal-first helper for configuring OpenCode providers
and making model availability deliberate. It has no web UI and needs only
Python's standard library.

## Starting the app

Run `./launch.sh` or `python3 setup.py`. Startup first inspects
`opencode.json` without changing it. With no meaningful provider/model
configuration, it offers guided first-provider onboarding. With any existing
provider or model configuration, it opens a control panel with a compact
LOCAL / FREE, REMOTE / PAID, and unclassified provider/model-count inventory
before asking what to do. Endpoint and credential diagnostics are available
only through **View provider details** or `python3 setup.py status`.

The home screen is read-only until you explicitly select an action. Adding a
provider refuses to replace an existing provider ID; use **Edit provider** or
choose another ID instead.

## Security model

**opencode-local manages credential references, not credentials.** It never
stores API keys, tokens, passwords, or secret values in its sidecar, repository
files, test data, or generated backups (it does not generate configuration
backups). For a credentialed provider, it records only an environment-variable
name such as `DASHSCOPE_API_KEY`; OpenCode receives the supported placeholder
`{env:DASHSCOPE_API_KEY}` in its normal provider options.

Put the actual API key in your process environment or use whichever secure
mechanism OpenCode and the provider's tooling already support. The status view
reports only `PRESENT`, `MISSING`, or `NOT REQUIRED`—never the value.

Application metadata lives in `~/.config/opencode/opencode-local.json`, not
`opencode.json`. It holds display names, explicit cost classification, provider
and model availability, credential environment-variable references, and optional
notes.

## Availability

Disabled is a soft-disable. Provider and model definitions, endpoints, limits,
reasoning settings, and credentials remain untouched and visible in
`opencode-local`.

OpenCode-supported `blacklist` entries make disabled models unavailable for
ordinary model selection; `disabled_providers` disables a whole provider without
rewriting every model. Model state remains independent: disabling and then
re-enabling a provider restores each model's prior enabled/disabled state.

```bash
# Add or update an OpenAI-compatible provider. The wizard asks for the explicit
# LOCAL / FREE or REMOTE / PAID classification and an optional credential env var.
python ./setup.py setup

# Compact inventory with visible disabled entries, credential status, endpoint,
# and orange-accented REMOTE / PAID providers in a colour terminal.
python ./setup.py status

# Deliberate, reversible availability changes.
python ./setup.py disable alibaba-cloud/qwen3.8-27b
python ./setup.py disable alibaba-cloud
python ./setup.py enable alibaba-cloud
# qwen3.8-27b remains disabled after the provider is enabled again.

# Provider editing. No credential value is accepted or written.
python ./setup.py edit-provider alibaba-cloud \
  --name "Alibaba Cloud" --base-url https://dashscope.example/v1 \
  --classification remote-paid --credential-env DASHSCOPE_API_KEY \
  --notes "Billable production account"

# Mark a provider as local/no-credential or change its OpenCode provider ID.
python ./setup.py edit-provider 117-local --no-credential --classification local-free
python ./setup.py edit-provider old-id --id new-id
```

Run `python ./setup.py sync` after manually changing `opencode.json`. New
definitions are adopted as enabled but deliberately remain unclassified and
credential-unspecified until you set those values explicitly.
