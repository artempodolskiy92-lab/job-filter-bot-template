# Telegram job filter bot

Scans a Telegram folder of vacancy channels/groups once a day, filters
vacancies in two stages (cheap keyword match, then an LLM fit-score call
against your profile), and sends matches to your own Telegram Saved Messages
("Избранное"). Runs for free on a daily schedule via GitHub Actions.

## How it works

1. You put the Telegram channels/groups you want scanned into one Telegram
   folder (the folder name is config — no separate channel list to maintain).
2. Every message from the last `LOOKBACK_HOURS` gets a cheap keyword
   pre-filter (`KEYWORDS` in `.env`), plus built-in filters that skip resumes,
   weekly digests, and auto-generated "we found N vacancies" catalog posts —
   all before anything costs an LLM call.
3. Whatever survives goes to an LLM (via OpenRouter) with the full contents
   of `profile.md` as context, and gets a yes/no fit judgment plus a short
   comment.
4. Matches get sent to your own Saved Messages as one daily digest.

There's no web UI, no database, no separate channel config — the Telegram
folder and `profile.md` are the only two things you maintain day to day.

## Setup

### 1. Telegram API credentials

Go to [my.telegram.org](https://my.telegram.org) → log in with your phone
number → **API development tools** → create an application (any name).
You'll get an `api_id` and `api_hash`.

> If the form errors out with a generic "ERROR" on creation, it's usually
> because the app's **Short name** contains "bot" — Telegram reserves that
> for BotFather bots. Use a short name without "bot" in it.

### 2. OpenRouter API key

Go to [openrouter.ai](https://openrouter.ai) → sign up → add a few dollars
of credit → **Keys** → create a key. `google/gemini-2.5-flash-lite` (the
default model here) is cheap enough that a few dollars lasts a long time.

### 3. Group your vacancy channels into a Telegram folder

In Telegram: create a folder, add the channels/groups you want scanned to
it. Note the folder's exact name — that's your `FOLDER_NAME`.

### 4. Local setup + generate a session string

```bash
git clone <this-repo-url>
cd <repo-dir>
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Fill in `TG_API_ID`, `TG_API_HASH`, `OPENROUTER_API_KEY`, `FOLDER_NAME`,
`KEYWORDS` in `.env`. Then generate the session string (one-time interactive
login — Telegram will text/message you a login code to enter):

```bash
./venv/bin/python3 -c "
import os
from dotenv import load_dotenv
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
load_dotenv()

with TelegramClient(StringSession(), int(os.environ['TG_API_ID']), os.environ['TG_API_HASH']) as client:
    s = StringSession()
    s.set_dc(client.session.dc_id, client.session.server_address, client.session.port)
    s.auth_key = client.session.auth_key
    print(s.save())
"
```

Paste the printed string into `.env` as `TG_SESSION_STRING`. This is what
lets the bot run headlessly in GitHub Actions without ever prompting for a
login code again.

### 5. Write your profile

Edit `profile.md` — this is the only place that defines what counts as a
fit. See the template comments in the file for guidance, including an
optional "hard-gap screening" pattern if you find the bot letting through
too many title-matches-but-actually-wrong-role vacancies.

### 6. Test locally

```bash
./venv/bin/python3 main.py
```

Check your Telegram Saved Messages for the result (or `No matching
vacancies found` in the terminal if nothing matched in the lookback window).

### 7. Deploy: GitHub Actions (free, no server needed)

```bash
gh repo create <your-repo-name> --private --source=. --push
```

(Or public if you don't mind the code — but keep it private if you'll edit
`profile.md` with real personal details, and note the workflow itself
already has no secrets baked in.)

Push your `.env` values to GitHub Secrets (never commit `.env` itself):

```bash
gh auth refresh -s workflow   # one-time: lets gh push workflow file changes
./venv/bin/python3 -c "
import subprocess
from dotenv import dotenv_values
vals = dotenv_values('.env')
for k, v in vals.items():
    subprocess.run(['gh', 'secret', 'set', k, '--body', v], check=True)
"
```

The included `.github/workflows/daily.yml` runs on a daily cron
(`0 3 * * *` UTC by default — edit that line for your own timezone) plus
`workflow_dispatch` for manual runs:

```bash
gh workflow run daily.yml           # run it right now
gh run list --workflow=daily.yml    # check recent runs
gh run view <run-id> --log          # see per-channel scan counts
```

Every run checks out the repo fresh and installs dependencies — a
`git push` to `main` *is* the deploy, there's no separate build step.

## Notes

- **No push notifications**: messages sent to your own Saved Messages via
  the API don't trigger a phone notification — you check manually, or rely
  on the daily cadence. A real push would require a separate bot via
  BotFather sending to you instead of (or in addition to) Saved Messages.
- **Errors get reported to you too**: if the script crashes, it posts the
  error (truncated) to Saved Messages before failing the GitHub Actions run,
  so you don't have to go check logs to notice something broke.
- If you change `.env` values later, re-run the `gh secret set` loop above —
  GitHub Secrets don't update themselves. If you add a new required env var,
  add it as a Secret *and* reference it in `daily.yml`'s `env:` block before
  relying on it — a var referenced in the workflow but missing from Secrets
  becomes an *empty string* at runtime, not unset, which can silently break
  a `os.environ.get(KEY, default)` fallback in `main.py`.
