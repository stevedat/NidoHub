---
name: ubuntu-nido-email
description: >-
  Manage, search, summarize, and draft emails on NidoOS using the preinstalled
  Himalaya CLI (IMAP/SMTP/Maildir/JMAP with JSON output) alongside the lightweight
  Geary GTK mail client. Never sends or deletes mail without explicit user approval.
---

# NidoOS AI-Ready Email (`himalaya` + `geary`)

NidoOS ships two complementary lightweight email tools instead of Thunderbird:
- **Geary (`org.gnome.Geary.desktop`)**: Native GTK mail client (~60 MB RAM) for visual inbox reading and composing.
- **Himalaya (`/usr/local/bin/himalaya`)**: Fast Rust CLI with `--output json` designed for autonomous AI agents (`agy`, `codex`, `claude`, `nidoagent`).

## Safety Rules (Mandatory)

1. **Read-only by default**: Listing envelopes, reading message bodies, searching, and saving drafts are safe.
2. **Explicit confirmation required**: NEVER run `himalaya message send`, `himalaya template send`, `himalaya message delete`, or `himalaya folder expunge` without showing the recipient, subject, and summary to the user and receiving explicit confirmation first.
3. **Never expose secrets**: Do not print raw passwords, app passwords, or OAuth tokens from `~/.config/himalaya/config.toml`.

## Preflight

Check installation and configured accounts:

```bash
himalaya --version
himalaya account list --output json
```

If no account is configured yet, guide the user to run the interactive wizard once in Terminal:

```bash
himalaya account configure
```

(Supports Gmail App Password / OAuth2, Outlook/Microsoft 365, Fastmail, and standard IMAP/SMTP servers.)

## Common Agent Workflows

### 1. List and summarize recent inbox messages

```bash
himalaya envelope list --folder INBOX --page-size 20 --output json
```

### 2. Search emails by sender, subject, or date

```bash
himalaya envelope list --folder INBOX --output json "from:boss@example.com"
himalaya envelope list --folder INBOX --output json "subject:invoice"
```

### 3. Read a specific message cleanly

```bash
himalaya message read <ID> --output plain
```

### 4. Save attachments and inspect with NidoOffice

```bash
himalaya attachment download <ID>
# Then inspect downloaded .docx / .xlsx / .pptx files with nidooffice:
nidooffice read /path/to/attachment.docx --json
```

### 5. Draft a reply safely (save to Drafts instead of sending directly)

```bash
himalaya template reply <ID>
# Or write a MIME draft and save it into Drafts:
himalaya message save --folder Drafts < draft.eml
```

After saving to Drafts, tell the user they can review and click Send in **Geary** or confirm sending via `himalaya`.
