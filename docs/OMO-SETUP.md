# OMO Setup Guide — WhisperDeck

Quick install for oh-my-openagent with ClinePass + OpenRouter on Windows.

## 1. Prerequisites

- [OpenCode](https://opencode.ai) installed
- ClinePass account (custom API at `api.cline.bot`)
- OpenRouter account with credits

## 2. Authenticate Providers

### ClinePass

Inside OpenCode TUI, run `/connect`, select "Custom OpenAI-compatible", enter:

```
Base URL: https://api.cline.bot/api/v1
API Key:  <your clinepass key>
```

Confirm it works: set model to `cline-pass/deepseek-v4-flash` and send a test message.

### OpenRouter

Add to `opencode.jsonc` MCP servers:

```json
"openrouter": {
  "type": "remote",
  "url": "https://mcp.openrouter.ai/mcp",
  "enabled": true
}
```

Set `OPENROUTER_API_KEY` env var or auth via the MCP handshake.

## 3. Install OMO

```powershell
bunx oh-my-openagent install
```

This registers `oh-my-openagent@latest` as an OpenCode plugin and writes default
configs to `~/.config/opencode/oh-my-openagent.json`.

## 4. Global Config (`~/.config/opencode/opencode.jsonc`)

```jsonc
{
  "plugin": [
    "oh-my-openagent@latest"
  ],
  "model": "cline-pass/deepseek-v4-pro",   // orchestrator model
  "mcp": {
    "openrouter": {
      "type": "remote",
      "url": "https://mcp.openrouter.ai/mcp",
      "enabled": true
    }
  },
  "tools": {
    "claude-mem_*": false   // only if you have claude-mem running
  }
}
```

## 5. Global OMO Config (`~/.config/opencode/oh-my-openagent.json`)

Optional. Silence telemetry + hooks:

```json
{
  "telemetry": false,
  "disabled_hooks": ["anthropic-context-window-limit-recovery"]
}
```

**Note**: The default global config ships `opencode/gpt-5-nano` as the fallback
for all agents/categories. This model slug is bogus — no provider recognizes the
`opencode/` prefix. The project-level config overrides everything, so this only
matters if you run Sisyphus outside this project.

## 6. Project Config

Clone WhisperDeck — the `.opencode/oh-my-openagent.jsonc` is already in the repo:

```
WhisperDeck/
└── .opencode/
    └── oh-my-openagent.jsonc
```

This config maps every agent and category to real models:

| Agent / Category | Model | Provider |
|---|---|---|
| Sisyphus (main) | deepseek-v4-pro | ClinePass |
| Hephaestus | deepseek-v4-pro | OpenRouter |
| Prometheus | deepseek-v4-pro | OpenRouter |
| Explore | qwen3-coder-30b-a3b | OpenRouter |
| Sisyphus-junior | deepseek-v4-flash | OpenRouter |
| quick | qwen3-coder-30b-a3b | OpenRouter |
| visual-engineering | qwen3-coder-30b-a3b | OpenRouter |
| deep | deepseek-v4-pro | OpenRouter |
| ultrabrain | deepseek-v4-pro | OpenRouter |
| writing | qwen3-coder-30b-a3b | OpenRouter |

**Why the split**: ClinePass cannot spawn sub-process agents — any `category=`
or sub-agent spawn fails with `ProviderModelNotFoundError`. Only the main
orchestrator session runs on ClinePass. Everything Sisyphus delegates runs
through OpenRouter.

## 7. Verification

Start OpenCode from the WhisperDeck repo root:

```powershell
cd C:\path\to\WhisperDeck
opencode
```

Send a test message to confirm Sisyphus is running:

> What model are you running on? Try delegating a quick task.

Check the session model reports `cline-pass/deepseek-v4-pro`. Then test delegation:

> Use explore to find the GET /api/health route and report the file + line number.

If explore returns results from `openrouter/qwen/qwen3-coder-30b-a3b-instruct`,
delegation is working.

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `ProviderModelNotFoundError: opencode/gpt-5-nano` | Project-level config not loaded. Confirm `.opencode/oh-my-openagent.jsonc` exists and match the schema. |
| `ProviderModelNotFoundError: cline-pass/deepseek-v4-pro` on sub-agent | ClinePass cross-model restriction. Use OpenRouter models for all sub-agents. |
| Sisyphus uses Flash instead of Pro | Check `model` in `opencode.jsonc`. The omo `agents.sisyphus.model` only affects sub-spawns. |
| OpenRouter models time out | Credits exhausted or API key missing. Check OpenRouter dashboard. |

## Known Limitations

- **ClinePass no `/v1/models` endpoint** — models only discoverable via OpenCode `/connect` flow. Manually cache: `deepseek-v4-flash`, `deepseek-v4-pro`, `deepseek-r1`, `kimi-k2.6`, `kimi-k2.7-code`, `kimi-k3`, `qwen3.7-plus`, `qwen3.7-max`, `qwen3-7-plus`, `mimo-v2.5`, `mimo-v2.5-pro`, `minimax-m3`, `glm-5.2`.
- **OMO `opencode/gpt-5-nano` fallback** — default global config uses a non-existent model slug. Always override via project config.
- **Superpowers plugin** — if installed from Claude Code cache, rename the `.opencode` manifest dir to disable it. Located at `~/.claude/plugins/cache/superpowers-marketplace/superpowers/5.1.0/.opencode`.
