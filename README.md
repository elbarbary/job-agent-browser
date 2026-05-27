# Local-First Job Application Browser Agent

A cautious Python MVP for finding public job postings, grounding drafts in a supplied
CV, retaining a local browser login session, and requiring human ownership of final
submission or email sending.

Browser Use is the browser execution engine. The added WebABI-style layer records
actions and risk classifications in private JSONL audit logs.

## Safety Contract

- All configured app/LLM service endpoints must be loopback addresses.
- CVs, profiles, browser cookies, application data, `.env`, and logs are git-ignored.
- The machine inventory `system_report.md` remains local and is git-ignored because
  it may contain private network and hardware metadata.
- Browser logins are visible and manual; raw passwords are never requested or stored.
- CV extraction is source truth. Missing facts are recorded as `needs_user_answer`.
- Searches are read-only and limited to public ATS result hosts.
- `--dry-run` saves a draft only and does not click submit.
- `--confirm` requires `SUBMIT <job_id>`, opens the application in your local saved
  browser session, and leaves the actual Submit click to you.
- Email delivery is off unless SMTP environment variables are explicitly configured,
  and still requires the typed phrase `SEND UPDATE`.
- Destructive and payment actions are blocked by policy.

The conservative manual final click is intentional: an unrestricted browser-agent
submit run cannot yet be intercepted action-by-action with enough confidence for
applications containing unknown personal facts.

## Layout

```text
app/                  Python CLI, policy, Browser Use engine, CV and workflow modules
app/webabi/           risk compiler, recorder, verifier, replay schema
data/cv/              private source CV files
data/profiles/        extracted CV markdown and candidate profile JSON
data/sessions/        private persistent Chrome profile
data/logs/runs/       append-only action audit JSONL logs
data/applications/    ranked jobs, drafts, approvals, daily report
scripts/              local install/runtime helpers
```

## Install

From the remote machine:

```bash
cd ~/job-agent-browser
chmod +x scripts/*.sh
scripts/install.sh
cp .env.example .env
chmod 600 .env
```

This project was set up for user-local Ollama at `~/.local/bin/ollama`. To start it:

```bash
scripts/start_ollama.sh
OLLAMA_HOST=http://127.0.0.1:11434 ~/.local/bin/ollama pull gemma4:e4b-it-q4_K_M
.venv/bin/python -m app.main llm-status --test
```

Ollama must listen only on `127.0.0.1:11434`; verify with:

```bash
ss -ltnp | grep 11434
```

## Smoke Test

This harmless test opens `https://example.com` through Browser Use, extracts visible
interactive elements, and creates a read-only audit record:

```bash
scripts/run_smoke_test.sh
```

## CV Ingestion

Place a PDF or DOCX CV in the protected CV directory and ingest it:

```bash
cp /path/to/my_cv.pdf data/cv/my_cv.pdf
chmod 600 data/cv/my_cv.pdf
.venv/bin/python -m app.main ingest-cv data/cv/my_cv.pdf
```

Outputs:

```text
data/profiles/cv_extracted.md
data/profiles/candidate_profile.json
```

Review `candidate_profile.json` before using it. PDF text extraction cannot reliably
recover scanned/image-only resumes without OCR; convert those to a text PDF or DOCX.

## Job Preferences

This repo supports a private `data/profiles/job_preferences.json` file. The committed
template is intentionally generic: each user should set their own target roles,
locations, work authorization/sponsorship requirements, availability, and salary
policy in that private file. Create or refresh the template with:

```bash
.venv/bin/python -m app.main init-preferences
```

The preference file is not committed. Fields left as `needs_user_answer` stay
unknown and are not used as application facts. Do not commit a filled-in preference
file; it may contain immigration, location, salary, or personal targeting data.

## 24/7 Worker

The background worker runs locally and safely:

- Refreshes the private watchlist every few hours.
- Pulls public job feeds from Remotive, RemoteOK, and Arbeitnow.
- Does not require a hand-approved URL list for normal discovery.
- Can also process specific source URLs if you add high-value postings manually.
- Can attempt public searches if `queries_enabled` is set to `true`, though search
  engines may block automation.
- Ranks against CV and preferences.
- Drafts only high-scoring local applications at or above `min_auto_draft_score`.
- Generates `data/applications/daily_update.md`.
- Never submits applications.
- Never sends email.

Initialize the private watchlist:

```bash
.venv/bin/python -m app.main init-watchlist
```

Run one cycle:

```bash
.venv/bin/python -m app.main worker-once
```

Install and start the user service:

```bash
scripts/install_user_service.sh
scripts/status_worker.sh
```

If `loginctl enable-linger` fails, ask an admin to enable linger for the user so the
worker survives logout:

```bash
sudo loginctl enable-linger "$USER"
```

For extra reliable polling of specific companies or postings, add public job posting
URLs to:

```text
data/profiles/watchlist.json
```

The default watchlist keeps `queries_enabled` off because Google/DuckDuckGo served
anti-automation interstitials from this host during testing.

## Manual Login Session

Run this from the remote graphical desktop, or an SSH session capable of opening its
display:

```bash
.venv/bin/python -m app.main login-session
```

A visible Chrome window uses `data/sessions/browser-profile`. Log in yourself, decline
any browser prompt to save raw passwords, and return to the terminal when done. Treat
that directory like a credential store because it retains authenticated cookies.

## Search And Draft

The always-on worker discovers jobs from public feeds without needing you to maintain
approved URLs. You can still run a one-off read-only search against public Lever,
Greenhouse, and SmartRecruiters pages:

```bash
.venv/bin/python -m app.main search-jobs --query "software engineering intern" --location "remote"
.venv/bin/python -m app.main review-jobs
.venv/bin/python -m app.main apply --job-id <id> --dry-run
```

Public search engines may present an anti-automation interstitial. In that case, use
the background feed worker or supply a public ATS posting URL directly:

```bash
.venv/bin/python -m app.main search-jobs --query "software engineering intern" \
  --location "remote" --source-url "https://jobs.lever.co/company/job-id"
```

The draft is written to `data/applications/drafts/<id>.json`. It includes known
CV-derived answers and `needs_user_answer` entries for anything absent from the CV.
Once the local Gemma model has been verified, add `--with-llm` to include advisory
matching notes; these notes are non-authoritative and cannot supply form facts.

## Approval And Submission

After reviewing the draft and answering unresolved questions:

```bash
.venv/bin/python -m app.main apply --job-id <id> --confirm
```

The CLI prints the final summary and requires:

```text
SUBMIT <job_id>
```

It then opens the approved posting in the persistent visible browser; you review and
press Submit manually. The application is never silently submitted.

## Daily Update And Optional Email

Generate a local markdown update:

```bash
.venv/bin/python -m app.main daily-update
```

To send it by SMTP, first configure SMTP values only in `.env` using an app
password/token rather than a primary mailbox password, then run:

```bash
.venv/bin/python -m app.main daily-update --send
```

Delivery requires typing `SEND UPDATE`. Passwords are read from environment variables
only and are redacted from audit records.

## Audit Logs

Every supported workflow writes private JSONL logs at `data/logs/runs/`. Review the
latest log with:

```bash
.venv/bin/python -m app.main audit-log
```

## Current Scope

This MVP omits a web dashboard and automatic final form submission in favor of the
safety gates above. It provides a working local CLI foundation for adding audited,
site-specific form adapters later.
