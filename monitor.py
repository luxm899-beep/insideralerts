#!/usr/bin/env python3
"""
Insider & Congress Buy Alerts
-----------------------------
Watches two public sources and pushes a notification to your phone:

  1. SEC Form 4 filings  -> open-market PURCHASES (transaction code "P")
                            by officers/directors of public companies.
  2. Congressional PTRs  -> new Periodic Transaction Reports filed by
                            House members (Clerk bulk XML) and Senators
                            (Senate eFD search).

Push delivery is via ntfy.sh (free, no account needed).
State is kept in seen.json so you never get the same alert twice.
"""

import io
import json
import os
import re
import sys
import time
import zipfile
import datetime as dt
import xml.etree.ElementTree as ET

import requests

# ----------------------------------------------------------------------------
# Config (all overridable with environment variables)
# ----------------------------------------------------------------------------

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")

# The SEC REQUIRES a descriptive User-Agent with a contact email.
# Example: "jane-personal-alerts jane@example.com"
SEC_UA = os.environ.get("SEC_USER_AGENT", "").strip()

# Only alert on insider buys worth at least this much (USD). Set to 0 for all.
MIN_PURCHASE_USD = float(os.environ.get("MIN_PURCHASE_USD", "100000"))

# Which insider titles you care about. Substring match, case-insensitive.
TITLE_KEYWORDS = [
    t.strip().lower()
    for t in os.environ.get(
        "TITLE_KEYWORDS",
        "chief executive,ceo,president,chief financial,cfo,chairman,chief operating,coo",
    ).split(",")
    if t.strip()
]

# Alert on plain directors too (no officer title)? Usually noisier.
INCLUDE_DIRECTORS = os.environ.get("INCLUDE_DIRECTORS", "false").lower() == "true"

# "auto" = only run the congress check a couple times a day (PTRs are slow).
RUN_CONGRESS = os.environ.get("RUN_CONGRESS", "auto").lower()
CONGRESS_HOURS_UTC = {13, 21}  # ~9am and ~5pm US Eastern

# How many pages of 100 recent Form 4s to scan each run.
SEC_PAGES = int(os.environ.get("SEC_PAGES", "3"))

STATE_FILE = os.environ.get("STATE_FILE", "seen.json")
MAX_STATE = 8000

SEC_THROTTLE = 0.15  # seconds between SEC requests (their limit is 10/sec)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def log(*a):
    print(*a, flush=True)


def load_state():
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data.setdefault("sec", [])
    data.setdefault("house", [])
    data.setdefault("senate", [])
    return data


def save_state(state):
    for k in ("sec", "house", "senate"):
        state[k] = state[k][-MAX_STATE:]
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=1)


QUIET = False  # set on the very first run so the baseline doesn't spam you


def notify(title, message, click=None, tags="chart_with_upwards_trend", priority="default"):
    if QUIET:
        log("   (first run, suppressed):", title)
        return
    if not NTFY_TOPIC:
        log("!! NTFY_TOPIC not set -- would have sent:", title, "|", message)
        return
    headers = {
        "Title": title.encode("ascii", "ignore").decode(),
        "Tags": tags,
        "Priority": priority,
    }
    if click:
        headers["Click"] = click
    try:
        r = requests.post(
            f"{NTFY_SERVER}/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers=headers,
            timeout=20,
        )
        if r.status_code >= 300:
            log("ntfy error", r.status_code, r.text[:200])
    except Exception as e:
        log("ntfy failed:", e)


def money(n):
    if n is None:
        return "amount n/a"
    if n >= 1_000_000:
        return f"${n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"${n/1_000:.0f}K"
    return f"${n:,.0f}"


# ----------------------------------------------------------------------------
# Part 1 - SEC Form 4 insider purchases
# ----------------------------------------------------------------------------

ATOM = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4"
    "&company=&dateb=&owner=include&count=100&start={start}&output=atom"
)


def sec_get(url):
    time.sleep(SEC_THROTTLE)
    r = requests.get(url, headers={"User-Agent": SEC_UA, "Accept-Encoding": "gzip, deflate"}, timeout=30)
    r.raise_for_status()
    return r.text


def recent_form4_accessions():
    """Return list of (accession_dashed, folder_url) for recent Form 4 filings."""
    out, seen = [], set()
    for page in range(SEC_PAGES):
        try:
            xml = sec_get(ATOM.format(start=page * 100))
        except Exception as e:
            log("EDGAR feed page", page, "failed:", e)
            break
        for m in re.finditer(r'href="([^"]*/Archives/edgar/data/[^"]*-index\.htm)"', xml):
            link = m.group(1)
            if link.startswith("/"):
                link = "https://www.sec.gov" + link
            folder = link.rsplit("/", 1)[0]
            acc = re.search(r"(\d{10}-\d{2}-\d{6})", link)
            if not acc:
                continue
            acc = acc.group(1)
            if acc not in seen:
                seen.add(acc)
                out.append((acc, folder))
    return out


def txt(node, path):
    if node is None:
        return ""
    el = node.find(path)
    return (el.text or "").strip() if el is not None and el.text else ""


def parse_form4(raw, acc, folder):
    """Pull qualifying open-market purchases out of a Form 4 submission."""
    m = re.search(r"<ownershipDocument>.*?</ownershipDocument>", raw, re.S)
    if not m:
        return []
    try:
        doc = ET.fromstring(m.group(0))
    except ET.ParseError:
        return []

    issuer = doc.find("issuer")
    company = txt(issuer, "issuerName") or "Unknown company"
    ticker = txt(issuer, "issuerTradingSymbol") or "?"

    # Who filed, and do we care about their role?
    people = []
    for owner in doc.findall("reportingOwner"):
        name = txt(owner, "reportingOwnerId/rptOwnerName")
        rel = owner.find("reportingOwnerRelationship")
        title = txt(rel, "officerTitle")
        is_officer = txt(rel, "isOfficer") in ("1", "true")
        is_director = txt(rel, "isDirector") in ("1", "true")
        low = title.lower()
        match = is_officer and any(k in low for k in TITLE_KEYWORDS)
        if not match and INCLUDE_DIRECTORS and is_director:
            match, title = True, title or "Director"
        if match:
            people.append((name.title(), title or "Officer"))
    if not people:
        return []

    # Sum up open-market purchases (code P, shares acquired).
    shares = 0.0
    value = 0.0
    priced = True
    date = ""
    for t in doc.findall("nonDerivativeTable/nonDerivativeTransaction"):
        if txt(t, "transactionCoding/transactionCode") != "P":
            continue
        if txt(t, "transactionAmounts/transactionAcquiredDisposedCode/value") != "A":
            continue
        try:
            n = float(txt(t, "transactionAmounts/transactionShares/value") or 0)
        except ValueError:
            n = 0.0
        try:
            px = float(txt(t, "transactionAmounts/transactionPricePerShare/value") or 0)
        except ValueError:
            px = 0.0
        if px <= 0:
            priced = False
        shares += n
        value += n * px
        date = date or txt(t, "transactionDate/value")

    if shares <= 0:
        return []
    total = value if priced and value > 0 else None
    if total is not None and total < MIN_PURCHASE_USD:
        return []

    url = f"{folder}/{acc}-index.htm"
    hits = []
    for name, title in people:
        hits.append({
            "acc": acc,
            "title": f"{ticker} - {title} bought",
            "body": (
                f"{name} ({title}) bought {shares:,.0f} shares of {company} "
                f"[{ticker}] for {money(total)} on {date or 'recent date'}."
            ),
            "url": url,
        })
    return hits


def check_sec(state):
    if not SEC_UA:
        log("!! SEC_USER_AGENT not set; skipping SEC check.")
        return
    seen = set(state["sec"])
    filings = recent_form4_accessions()
    log(f"SEC: scanned {len(filings)} recent Form 4 filings")
    new = 0
    for acc, folder in filings:
        if acc in seen:
            continue
        seen.add(acc)
        state["sec"].append(acc)
        try:
            raw = sec_get(f"{folder}/{acc}.txt")
        except Exception as e:
            log("  fetch failed", acc, e)
            continue
        for hit in parse_form4(raw, acc, folder):
            new += 1
            log("  ALERT:", hit["body"])
            notify(hit["title"], hit["body"], click=hit["url"], tags="moneybag")
    log(f"SEC: {new} new insider-buy alerts")


# ----------------------------------------------------------------------------
# Part 2 - Congressional periodic transaction reports
# ----------------------------------------------------------------------------

HOUSE_ZIP = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
HOUSE_PDF = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc}.pdf"


def check_house(state):
    year = dt.date.today().year
    try:
        r = requests.get(HOUSE_ZIP.format(year=year), headers={"User-Agent": SEC_UA or "personal-alerts"}, timeout=90)
        r.raise_for_status()
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        name = next(n for n in zf.namelist() if n.lower().endswith(".xml"))
        root = ET.fromstring(zf.read(name))
    except Exception as e:
        log("House fetch failed:", e)
        return

    seen = set(state["house"])
    cutoff = dt.date.today() - dt.timedelta(days=10)
    new = 0
    for m in root.findall("Member"):
        def g(tag):
            el = m.find(tag)
            return (el.text or "").strip() if el is not None and el.text else ""

        if g("FilingType") != "P":  # P = Periodic Transaction Report
            continue
        doc = g("DocID")
        if not doc or doc in seen:
            continue
        seen.add(doc)
        state["house"].append(doc)

        filed = g("FilingDate")
        try:
            filed_d = dt.datetime.strptime(filed, "%m/%d/%Y").date()
            if filed_d < cutoff:
                continue  # old filing, first-run backfill - don't spam
        except ValueError:
            pass

        who = " ".join(x for x in [g("Prefix"), g("First"), g("Last"), g("Suffix")] if x)
        where = f"{g('StateDst')}" if g("StateDst") else ""
        new += 1
        notify(
            f"House PTR: {who}",
            f"Rep. {who} {where} filed a stock transaction report on {filed}. Tap to open the PDF.",
            click=HOUSE_PDF.format(year=g("Year") or year, doc=doc),
            tags="classical_building",
        )
    log(f"House: {new} new PTR alerts")


SEN_HOME = "https://efdsearch.senate.gov/search/home/"
SEN_SEARCH = "https://efdsearch.senate.gov/search/"
SEN_DATA = "https://efdsearch.senate.gov/search/report/data/"


def check_senate(state):
    try:
        s = requests.Session()
        s.headers["User-Agent"] = SEC_UA or "personal-alerts"
        home = s.get(SEN_HOME, timeout=30)
        token = re.search(r"name=['\"]csrfmiddlewaretoken['\"] value=['\"]([^'\"]+)", home.text).group(1)
        s.post(
            SEN_HOME,
            data={"prohibition_agreement": "1", "csrfmiddlewaretoken": token},
            headers={"Referer": SEN_HOME},
            timeout=30,
        )
        start = (dt.date.today() - dt.timedelta(days=10)).strftime("%m/%d/%Y 00:00:00")
        r = s.post(
            SEN_DATA,
            data={
                "start": "0",
                "length": "100",
                "report_types": "[11]",          # Periodic Transaction Report
                "filer_types": "[]",
                "submitted_start_date": start,
                "submitted_end_date": "",
                "candidate_state": "",
                "senator_state": "",
                "office_id": "",
                "first_name": "",
                "last_name": "",
                "csrfmiddlewaretoken": token,
            },
            headers={"Referer": SEN_SEARCH},
            timeout=45,
        )
        rows = r.json().get("data", [])
    except Exception as e:
        log("Senate fetch failed (site changes often):", e)
        return

    seen = set(state["senate"])
    new = 0
    for row in rows:
        first, last, office, link_html, filed = (row + ["", "", "", "", ""])[:5]
        href = re.search(r'href="([^"]+)"', link_html or "")
        url = "https://efdsearch.senate.gov" + href.group(1) if href else SEN_SEARCH
        key = url
        if key in seen:
            continue
        seen.add(key)
        state["senate"].append(key)
        who = f"{first} {last}".strip()
        new += 1
        notify(
            f"Senate PTR: {who}",
            f"Sen. {who} ({office}) filed a stock transaction report on {filed}. Tap to open it.",
            click=url,
            tags="classical_building",
        )
    log(f"Senate: {new} new PTR alerts")


# ----------------------------------------------------------------------------

def main():
    global QUIET
    state = load_state()
    first_run = not (state["sec"] or state["house"] or state["senate"])
    if first_run:
        QUIET = True
        log("First run: recording current filings as baseline, no pushes sent.")

    check_sec(state)

    hour = dt.datetime.utcnow().hour
    if first_run or RUN_CONGRESS == "true" or (RUN_CONGRESS == "auto" and hour in CONGRESS_HOURS_UTC):
        check_house(state)
        check_senate(state)
    else:
        log("Skipping congress check this run.")

    save_state(state)

    if first_run:
        QUIET = False
        notify(
            "Alerts are live",
            "Baseline recorded. From now on you'll get a push whenever an "
            "executive buys their own stock or a member of Congress files a trade report.",
            tags="white_check_mark",
        )

    log("Done.")


if __name__ == "__main__":
    sys.exit(main())
