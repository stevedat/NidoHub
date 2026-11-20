---
name: ubuntu-nido-agent
description: >-
  Inspect Ubuntu-Nido AI readiness, local capabilities, and OS skills, or run
  structured NidoAgent tasks with file attachments and Office report export via
  nidoagent and nidoai CLIs.
---

# Ubuntu-Nido Agent & Capabilities (`nidoagent` / `nidoai`)

Use this skill to discover what native tools and AI backends are ready on the
local Ubuntu-Nido system, sync OS skills across installed agent CLIs, or
delegate document-aware queries through `nidoagent`.

## Preflight & Capability Discovery

Check local tools, installed agent CLIs (`agy`, `codex`, `claude`), and OS skills
(zero network calls, works even when AI gateway is stopped):

```bash
nidoagent capabilities --json
```

Check full AI gateway + signed-in agent readiness:

```bash
nidoagent status
nidoai doctor
```

## Managing Ubuntu-Nido OS Skills

List bundled OS skills:

```bash
nidoagent skills list --json
```

Sync OS skill symlinks into user agent directories (`~/.agents/skills`,
`~/.gemini/config/skills`, `~/.claude/skills`, `~/.codex/skills`) without
overwriting user-created custom skills:

```bash
nidoagent skills sync --json
```

## Running Structured Tasks (`nidoagent ask`)

Ask a question or analyze attached Office/text files and get structured JSON:

```bash
nidoagent ask --json --file /path/to/contract.docx "Tom tat dieu khoan chinh"
```

Generate a Word or Excel report directly from the response:

```bash
nidoagent ask --json --file /path/to/input.docx --out /path/to/summary.xlsx "Lap bang tong hop"
```

Returns JSON:

```json
{
  "ok": true,
  "version": "0.2.0",
  "backend": "gateway",
  "model": "...",
  "reply": "...",
  "attachedFiles": ["/path/to/input.docx"],
  "outPath": "/path/to/summary.xlsx"
}
```

## Guardrails

- Respect user AI policy: if `aiEnabled` is `false` in `nidoagent status`, do not bypass it; direct the user to `nido-hub --onboarding`.
- Never print or log raw API keys or `NIDOAI_CLIENT_TOKEN` from `~/.nidoai/.env.local`.
