# Local-First Job Application Browser Agent

A cautious Python MVP for finding public job postings, grounding drafts in a supplied
CV, retaining a local browser login session, and optionally auto-submitting simple
applications after private standing authorization.

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
- `--auto-submit` is disabled by default and requires a private ignored
  `data/profiles/autopilot.json` opt-in file with standing authorization.
- Autopilot submits only simple forms where required fields are answerable from
  CV/profile/preferences. It blocks unknown required fields, required checkboxes,
  file uploads, payment/destructive actions, and non-allowlisted hosts.
- Email delivery is off unless SMTP environment variables are explicitly configured,
  and still requires the typed phrase `SEND UPDATE`.
- Destructive and payment actions are blocked by policy.

The autopilot guardrails are intentional: an unrestricted browser-agent submit run
cannot be intercepted action-by-action with enough confidence for applications
containing unknown personal facts.

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

Use `candidate_user_confirmed_facts` for corrections that should override imperfect
CV extraction. For example, if the extracted email is wrong, set:

```json
{
  "candidate_user_confirmed_facts": {
    "profile_reviewed": true,
    "contact_email": "your-confirmed-email@example.com"
  }
}
```

`profile_reviewed` is intentionally required for autopilot by default. This prevents
standing auto-submit from using a misread CV name, email, phone number, or other
identity fact before you have checked the private profile data.

Generate a private checklist of extracted and user-confirmed facts:

```bash
.venv/bin/python -m app.main profile-review
```

Review `data/profiles/profile_review.md`, plus the source files it points to. After
you have corrected any mistakes, mark the facts reviewed:

```bash
.venv/bin/python -m app.main confirm-profile
```

The confirmation command requires typing `CONFIRM PROFILE`. This unlocks only the
profile-review gate; autopilot still needs its separate private standing
authorization and all other safety checks.

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
- If private autopilot is enabled, attempts at most `max_submissions_per_run`
  guarded submissions per cycle.
- Generates `data/applications/daily_update.md`.
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

The default watchlist keeps raw Google/DuckDuckGo browser scraping off because
search engines served anti-automation interstitials from this host during
testing.

## Local Web Search

For actual web search without exposing a public service, run the bundled local
SearXNG broker. It binds only to `127.0.0.1:8099`; the Python app then calls the
local JSON endpoint and opens only allowlisted job/ATS result hosts.

```bash
scripts/start_local_search.sh
.venv/bin/python -m app.main web-search "AI product engineer Switzerland sponsorship" --max-results 10
.venv/bin/python -m app.main search-jobs --query "AI product engineer" --location "Switzerland sponsorship" --max-results 10
```

Stop it with:

```bash
scripts/stop_local_search.sh
```

Security notes:

- SearXNG is published only on `127.0.0.1`, not `0.0.0.0`.
- Its config lives in ignored private data under `data/search/searxng/`.
- The container runs with `no-new-privileges`, dropped capabilities, read-only
  filesystem, and a small tmpfs.
- Do not change `JOB_AGENT_SEARXNG_URL` to a public host; the app rejects
  non-loopback search URLs.

## Private Autopilot

Autopilot is local-only and off by default. Initialize the private template:

```bash
.venv/bin/python -m app.main init-autopilot
chmod 600 data/profiles/autopilot.json
```

Edit `data/profiles/autopilot.json` locally. To allow guarded auto-submit attempts,
set:

```json
{
  "enabled": true,
  "submit_without_per_job_confirmation": true,
  "standing_authorization": "I AUTHORIZE LOCAL AUTOPILOT SUBMISSIONS",
  "min_match_score": 80,
  "max_submissions_per_run": 1,
  "resume_path": "/absolute/path/to/resume.pdf",
  "require_profile_review": true,
  "block_file_uploads": false,
  "allowed_submit_hosts": ["jobs.example.com"]
}
```

That file is ignored by git. Add only hosts you are comfortable allowing the agent
to submit on. Autopilot will still block a posting if the page has required custom
questions, required checkboxes, missing or disabled resume upload configuration,
required select boxes, missing candidate identity, unknown salary/work authorization
fields, an unreviewed candidate profile, or no real application form on the page.

## Manual Login Session

Run this from the remote graphical desktop, or an SSH session capable of opening its
display:

```bash
.venv/bin/python -m app.main login-session
```

A visible Chrome window uses `data/sessions/browser-profile`. Log in yourself, decline
any browser prompt to save raw passwords, and return to the terminal when done. Treat
that directory like a credential store because it retains authenticated cookies.

If you are on a different device or a plain SSH terminal with no graphical display,
use the private challenge browser instead. It starts a virtual display and a noVNC
web client bound to `127.0.0.1` on the remote machine:

```bash
cd ~/job-agent-browser
scripts/start_challenge_browser.sh
```

From your other device, open an SSH tunnel:

```bash
ssh -N -L 6080:127.0.0.1:6080 barbary@100.116.208.74
```

Then open this local URL on that other device:

```text
http://127.0.0.1:6080/vnc.html?host=127.0.0.1&port=6080&autoconnect=1
```

Use this browser to log in or solve human challenges. When finished, stop it and
restart the worker if it was paused:

```bash
scripts/stop_challenge_browser.sh
```

Do not expose ports `5901` or `6080` publicly. They are intended to be reached only
through the SSH tunnel.

## Search And Draft

The always-on worker discovers jobs from public feeds without needing you to maintain
approved URLs. You can still run a one-off read-only search against public Lever,
Greenhouse, and SmartRecruiters pages:

```bash
.venv/bin/python -m app.main search-jobs --query "software engineering intern" --location "remote"
.venv/bin/python -m app.main review-jobs
.venv/bin/python -m app.main manual-queue --limit 50
.venv/bin/python -m app.main apply --job-id <id> --dry-run
```

To let the agent fill known fields but leave final submission to you, start the
private challenge browser first, then run:

```bash
scripts/start_challenge_browser.sh
.venv/bin/python -m app.main apply --job-id <id> --prepare
```

The dashboard will mark the job as `prepared_manual_submit` and show a review link.
Open that link, review the filled browser tab, answer anything still missing, check
any required consent/terms boxes yourself, and press Submit manually. The prepare
mode never clicks the final submit/apply button.

Every ordinary draft is also in the manual submit queue. The tracker dashboard
shows direct links to the original application page plus the exact `--prepare`
and `--confirm` commands for each drafted job. `prepared_manual_submit` only means
the agent has already opened and pre-filled a live remote browser tab; it is not
the full set of jobs you can review manually.

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
press Submit manually.

For a privately authorized guarded attempt:

```bash
.venv/bin/python -m app.main apply --job-id <id> --auto-submit
```

The command writes a draft first, checks the private autopilot policy, opens the
posting with the persistent browser profile, fills only known fields, and clicks a
safe submit/apply button only if no blockers are found. Every attempt is written to
the audit log. Verified submissions also create a private local ledger entry in
`data/applications/submissions/` so the worker does not submit the same job again.

An autopilot submission record means the agent clicked the final submit/apply button
and then detected a post-submit confirmation message such as "application received"
or "thank you for applying." If it clicks a button but cannot detect confirmation,
the tracker records an `unverified_submit_click` instead of a submission.
Autopilot will not retry jobs that already have an unverified submit-click record;
review those manually before clearing or archiving the attempt.

### Universal Page Planner And Site Adapters

Autopilot now has two layers:

- a universal browser-planner fallback that opens safe "Apply" entry points,
  extracts rendered fields/buttons/options, asks the local LLM to map those fields
  to allowed CV/profile answer keys, validates that every required answer is known,
  and then fills/submits only if the page passes those checks;
- site-specific adapters for sites that need special handling because they submit
  through JavaScript and show errors without navigating.

The local LLM planner is not allowed to invent values. It can only select from
known answer keys such as `email`, `phone`, `location`, `linkedin`, `github`,
`resume_file`, or `application_terms_checkbox`. Required unknown questions still
block the submit attempt and are logged as needing user input.

The Arbeitnow adapter handles its built-in application form directly:

- fills first name, last name, email, and CV upload from known profile/config data;
- checks the Arbeitnow success message instead of relying on URL changes;
- records visible `error-*` field messages such as missing terms or invalid file;
- refuses to retry jobs with existing unverified submit-click records.

Arbeitnow requires an application terms/privacy checkbox. Autopilot will check that
box only if private `data/profiles/autopilot.json` contains:

```json
{
  "allow_application_terms_checkbox": true
}
```

Keep that setting private. It represents permission to agree to ordinary application
portal terms/privacy checkboxes during an application submit attempt.

No email confirmation is guaranteed. Some sites send no email, some delay it, some
route it to the email field used in the CV/profile, and some require backend
validation after the click.

## Gmail Check

Gmail access uses the same local persistent browser session as manual login. The app
does not ask for or store your Gmail password, and this command does not send mail:

```bash
.venv/bin/python -m app.main gmail-check \
  --query "from:(greenhouse.io OR lever.co OR workday) OR application OR interview"
```

The command opens Gmail visibly, lets you wait for search results to load, then saves
the visible page text to `data/applications/gmail_checks/` and writes a read-only
audit record. Use `login-session` first if Gmail is not already logged in.

## External LLMs

The default advisory model is the local Ollama/Gemma backend. Logging into ChatGPT in
the browser is not automated by this project because that can send CV and application
data to an external service and is brittle compared with an explicit API integration.
If external model review is desired, add it as an opt-in provider with a clear privacy
warning and environment-based credentials; do not make it part of unattended
submission.

## Tracker Dashboard

Check current status from the CLI:

```bash
.venv/bin/python -m app.main status
.venv/bin/python -m app.main status --write-html
```

Run the local-only dashboard:

```bash
.venv/bin/python -m app.main dashboard
```

It binds to `127.0.0.1:7860` by default and shows jobs, draft answers, questions,
verified submission records, unverified submit-click attempts, and worker state. The
generated HTML and all tracker data stay under ignored `data/applications/`.

## Telegram Notifications

Telegram is the easiest chat connector because a bot can send status messages with a
single private token. Initialize the private config:

```bash
.venv/bin/python -m app.main init-telegram
chmod 600 data/profiles/telegram.json
```

Set it up:

1. Open Telegram and message `@BotFather`.
2. Send `/newbot`, choose a name/username, and copy the bot token.
3. Open your new bot chat and send it any message, such as `hi`.
4. On the server, fetch recent updates:

```bash
TOKEN="paste_bot_token_here"
curl "https://api.telegram.org/bot${TOKEN}/getUpdates"
```

5. Find `message.chat.id` in the JSON response.
6. Edit `data/profiles/telegram.json`:

```json
{
  "enabled": true,
  "bot_token": "paste_bot_token_here",
  "chat_id": "paste_chat_id_here",
  "notify_on_worker_run": true
}
```

You can also set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
`TELEGRAM_ENABLED=true`, and `TELEGRAM_NOTIFY_ON_WORKER_RUN=true` in `.env`, but
do not commit `.env`.

Send a test status update:

```bash
.venv/bin/python -m app.main notify-status --telegram
```

The notification includes counts for jobs, drafts, submissions, recent submitted
roles, pending drafts, and worker errors. It does not send CVs, cookies, browser
sessions, raw logs, or application files.

## WhatsApp Notifications

WhatsApp support is outbound-only through the official Meta WhatsApp Cloud API.
The app does not create a public webhook, so WhatsApp cannot control the worker by
replying to messages. This keeps the system local/private while still letting it
send tracker updates to your phone.

Initialize the private config:

```bash
.venv/bin/python -m app.main init-whatsapp
chmod 600 data/profiles/whatsapp.json
```

In Meta's WhatsApp API setup, get:

- a WhatsApp Cloud API access token;
- the business phone number ID;
- your recipient phone number in international format.

Then edit `data/profiles/whatsapp.json`:

```json
{
  "enabled": true,
  "access_token": "paste_meta_access_token_here",
  "phone_number_id": "paste_phone_number_id_here",
  "recipient_phone": "+201001234567",
  "graph_api_version": "v25.0",
  "notify_on_worker_run": true,
  "send_mode": "text"
}
```

Text messages may require you to message the WhatsApp Business number first,
opening Meta's customer-service window. If you want first-contact notifications,
set `send_mode` to `template` and configure an approved template such as
`hello_world` for testing.

Send a test status update:

```bash
.venv/bin/python -m app.main notify-status --whatsapp
```

You can also set `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`,
`WHATSAPP_RECIPIENT_PHONE`, `WHATSAPP_ENABLED=true`, and
`WHATSAPP_NOTIFY_ON_WORKER_RUN=true` in `.env`, but do not commit `.env`.

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

## Browser For AI

The WebABI layer can also save a structured "browser for AI" snapshot. It is the
agent-facing equivalent of what a visual browser gives a human: page title, URL,
headings, visible text, forms, fields, labels, required flags, links, buttons,
visible errors, risk hints, and a screenshot.

```bash
.venv/bin/python -m app.main page-context \
  "https://www.arbeitnow.com/jobs/companies/example/example-role-123"
```

Outputs are private JSON files under:

```text
data/logs/page_contexts/
```

For logged-in pages, use the saved browser profile:

```bash
.venv/bin/python -m app.main page-context "https://example.com/account" --persistent
```

Use `--allow-any-url` only for read-only inspection of a page outside
`JOB_AGENT_ALLOWED_HOSTS`. This command does not click or submit anything; it only
translates the browser-rendered page into structured context so the AI can reason
about meaning on top of the same page a human would see.

## Current Scope

This MVP omits a web dashboard. Autopilot uses a conservative universal form
planner plus site-specific adapters. Some multi-step portals, CAPTCHA/login walls,
or custom assessments may still require a new adapter or manual review, but the
default path now gives the AI a rendered browser context instead of relying only on
hard-coded selectors.
