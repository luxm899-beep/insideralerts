# Insider & Congress Buy Alerts

Pushes a notification to your phone when:

- an **officer of a public company buys their own stock** on the open market (SEC Form 4, transaction code `P`), or
- a **member of Congress files a stock transaction report** (House Clerk + Senate eFD).

Runs itself on GitHub's servers. No computer to leave on, no server to pay for, $0/month.

---

## Setup (about 15 minutes)

### 1. Get the push app

Install **ntfy** (free, open source, no account):

- iPhone: App Store → "ntfy"
- Android: Play Store → "ntfy"

Open it, tap **+**, and subscribe to a topic. Pick something long and random —
anyone who guesses your topic name can read your alerts. Example:

```
insider-buys-k7f3q9xr2m
```

Write that topic name down.

### 2. Put this repo on GitHub

Create a **public** repo (public repos get unlimited free Actions minutes;
private ones are capped at 2,000/month, which this would blow through).
Nothing sensitive goes in the repo — your topic name lives in Secrets.

Upload `monitor.py`, `.github/workflows/alerts.yml`, and this README.

### 3. Add two secrets

In the repo: **Settings → Secrets and variables → Actions → New repository secret**

| Name | Value |
|---|---|
| `NTFY_TOPIC` | the topic you picked, e.g. `insider-buys-k7f3q9xr2m` |
| `SEC_USER_AGENT` | your name and email, e.g. `yourname personal-alerts you@gmail.com` |

The SEC requires that User-Agent header with a real contact email. They will
block you without it.

### 4. Start it

**Actions** tab → **Insider & Congress Alerts** → **Run workflow**.

The first run is silent on purpose — it records what's already been filed so
you don't get 300 notifications about old news. When it finishes you'll get one
"Alerts are live" push. After that it runs every 30 minutes on weekdays.

---

## Tuning

Edit the `env:` block in `.github/workflows/alerts.yml`:

| Setting | Default | What it does |
|---|---|---|
| `MIN_PURCHASE_USD` | `100000` | Ignore insider buys smaller than this. Raise to `1000000` if you only want big convictions. |
| `INCLUDE_DIRECTORS` | `false` | `true` also alerts on board members, not just officers. Much noisier. |
| `TITLE_KEYWORDS` | CEO, CFO, President, Chairman, COO | Comma-separated titles to watch. Set to just `chief executive,ceo` for CEOs only. |
| `SEC_PAGES` | `3` | Pages of 100 recent Form 4s scanned per run. Bump to `5` on heavy filing days. |
| `RUN_CONGRESS` | `auto` | `auto` checks disclosures twice a day. `true` checks every run. |

To change how often it runs, edit the `cron:` line. `"0 * * * 1-5"` = hourly.

---

## Things worth knowing

- **Form 4s are fast.** Executives must file within 2 business days of the trade,
  and they usually file after the close. You'll typically see these within an
  hour of them hitting EDGAR.
- **Congressional filings are slow.** The STOCK Act gives members up to 45 days
  to disclose. You are learning about a trade made weeks ago — nobody gets these
  faster, including paid services.
- **Congress alerts are filing-level.** The House and Senate publish PTRs as
  PDFs; the alert tells you who filed and links the document. Extracting the
  actual tickers requires PDF parsing that breaks constantly, which is why the
  alert links you to the source instead of guessing.
- **Buys are the signal, sells aren't.** This deliberately only watches code `P`
  (open-market purchase). It ignores code `S` (sale), `A` (grants), and `M`
  (option exercises), which are mostly compensation noise.
- **GitHub pauses scheduled jobs** in repos with 60 days of no human activity.
  You'll get an email; click the button to re-enable, or push any commit
  occasionally.

## Source data

Everything here comes from free public government endpoints:

- `sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4` — live Form 4 feed
- `disclosures-clerk.house.gov/public_disc/financial-pdfs/<year>FD.zip` — House index
- `efdsearch.senate.gov` — Senate disclosures

No API keys, no subscriptions.
