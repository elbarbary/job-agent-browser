# Claude Code Tools Integration

This project can coexist with Claude Code, but the job-application worker does not import or depend on Claude Code at runtime.

## What Was Vetted

- Official repository: `https://github.com/anthropics/claude-code`
- Local clone location used during setup: `/home/barbary/claude-code-official`
- Useful contents: official plugin examples, hook patterns, Agent SDK guidance, security guidance.

Avoid leaked, reverse-engineered, or unofficial Claude Code source repositories. They are not needed here and are unsafe to install into a system that handles CVs, browser sessions, and application data.

## What Claude Code Can Help With

- Developing this repo.
- Reviewing code changes.
- Running Claude Code plugins such as security guidance in a developer session.
- Creating Agent SDK prototypes if the user explicitly configures an Anthropic API path.

## What Claude Code Does Not Fix By Itself

- It does not bypass job-site CAPTCHAs.
- It does not guarantee form submission on arbitrary job sites.
- It does not replace Browser Use as this project’s browser automation engine.
- It should not receive CVs, cookies, session data, logs, or application drafts unless the user explicitly chooses that.

## Safe Usage Pattern

1. Keep the runtime worker local-first.
2. Keep Browser Use as the browser execution engine.
3. Use Claude Code only as an optional developer tool for this repository.
4. Never commit Claude Code auth files, API keys, CVs, sessions, logs, or applications.

## Optional Setup

Run:

```bash
bash scripts/setup_claude_code_tools.sh
```

Then start Claude Code manually from the repo if you want it:

```bash
cd ~/job-agent-browser
claude
```

