"""Turn the roster and goals into packet.json: row IDs, goal matches, flags and plain-English checks.

Everything here is deterministic. The model reads the packet; it never reads the spreadsheet.
"""

import datetime as dt
import os
import re

from nf_errors import NFError

DORMANT_DAYS = 60
NEAR_DORMANT_DAYS = 53          # within a week of going dormant
MAX_DATED_AGE = 30              # a dated roster older than this stops the run (NF-04)
WEAK_SIGNAL_CAVEAT_DAYS = 14    # undated roster saved longer ago than this gets a header caveat
GOALS_REVIEW_DAYS = 100

# Stated timing windows: contacting now would ignore what the person said.
TIMING_HOLD = re.compile(
    r"(let'?s\s+revisit|revisit\s+in|window\s+opens|open\s+in\s+(a|one|\d+)\s+(year|month|months|quarter)"
    r"|open\s+to\s+[\w\s]{0,25}?\s+in\s+(a|one|\d+)\s+(year|month|months|quarter)"
    r"|(may|might)\s+be\s+open\s+in|circle\s+back\s+in|not\s+until\s+(next|after))",
    re.I,
)

# An organisation: capitalised words, internal dots allowed ("a16z.com"), never a sentence-ending period.
ORG = r"([A-Z][\w&\-]*(?:\.[\w&\-]+)*(?:\s+[A-Z][\w&\-]*(?:\.[\w&\-]+)*)*)"
# "intro from Jordan at Kestrel Capital", "via Sam Lee at Index"
REF_INTRO = re.compile(
    r"\b(?:from|via|by|through|thanks\s+to)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:at|from)\s+" + ORG)
# "Jordan at Kestrel" when Jordan is a first name in the roster
REF_AT = re.compile(r"\b([A-Z][a-z]+)\s+at\s+" + ORG)
NOT_NAMES = {"met", "coffee", "saw", "brief", "chat", "lunch", "dinner", "call", "intro", "meeting", "panel",
             "talk", "event", "spoke", "connected", "worked", "works", "working", "based", "invested", "presented"}

PERSONAL_TAGS = {"personal", "friend", "family"}


def words(text):
    return len(re.findall(r"[A-Za-z0-9']+", text or ""))


def monday_of(day):
    return day - dt.timedelta(days=day.weekday())


def parse_int(text):
    t = (text or "").strip().replace(",", "")
    if re.fullmatch(r"-?\d+(\.0+)?", t):
        return int(float(t))
    return None


def parse_date_cell(text):
    t = (text or "").strip()
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", t)  # US style m/d/yyyy
        if not m:
            serial = parse_int(t)  # Excel serial date
            if serial and 20000 < serial < 80000:
                return dt.date(1899, 12, 30) + dt.timedelta(days=serial)
            return None
        mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return dt.date(y, mo, d)
    except ValueError:
        return None


def date_in_filename(name):
    m = re.search(r"(20\d{2})[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])", name)
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def fmt_date(d):
    return "%d %s %d" % (d.day, d.strftime("%b"), d.year)


def org_matches(org_alias, company):
    if not org_alias or not company:
        return False
    return re.search(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(org_alias.strip()), company, re.I) is not None


# ---------------------------------------------------------------- freshness

def decide_freshness(roster, today, as_of=None, use_as_is=False):
    """Strong signals (argument, date in file name) adjust days. Weak signals (file metadata) never do."""
    has_dates = "last_contact_date" in roster["columns_found"] and "days_since" not in roster["columns_found"]
    if has_dates:
        return {"status": "computed", "adjust_days": 0, "as_of": today.isoformat(), "source": "contact dates",
                "message": "Days since last contact were computed from the contact dates in the roster."}
    strong, source = None, None
    if as_of:
        strong, source = as_of, "the date you gave"
    else:
        named = date_in_filename(roster["file"])
        if named:
            strong, source = named, "the file name"
    if strong:
        age = (today - strong).days
        if age < 0:
            return {"status": "adjusted", "adjust_days": 0, "as_of": strong.isoformat(), "source": source,
                    "message": "The roster is dated %s, which is after today; figures used as provided." % fmt_date(strong)}
        if use_as_is:
            return {"status": "as-is", "adjust_days": 0, "as_of": strong.isoformat(), "source": source,
                    "message": "Roster dated %s (%d days ago); you chose to use the figures as provided." % (fmt_date(strong), age)}
        if age > MAX_DATED_AGE:
            raise NFError("NF-04",
                          "This roster is dated %s, %d days ago, so every 'days since last contact' figure is at least %d days out of date."
                          % (fmt_date(strong), age, age),
                          "Export a fresh roster and run again. If you really want to use this one, run "
                          "/network-focus:brief use-as-is")
        return {"status": "adjusted" if age else "current", "adjust_days": age, "as_of": strong.isoformat(), "source": source,
                "message": ("Roster dated %s (from %s): %d days added to every 'days since last contact' figure."
                            % (fmt_date(strong), source, age)) if age else "Roster dated today."}
    saved = roster.get("saved")
    saved_source = "workbook metadata"
    if saved is None:
        saved = dt.date.fromtimestamp(os.path.getmtime(roster["path"]))
        saved_source = "file date"
    age = (today - saved).days
    if age > WEAK_SIGNAL_CAVEAT_DAYS and not use_as_is:
        return {"status": "unconfirmed", "adjust_days": 0, "as_of": None, "source": saved_source,
                "file_saved": saved.isoformat(), "file_age_days": age,
                "message": "Freshness unconfirmed: file last saved %s (%d days ago); days since contact used as provided."
                           % (fmt_date(saved), age)}
    return {"status": "as-is" if use_as_is else "current", "adjust_days": 0, "as_of": None, "source": saved_source,
            "file_saved": saved.isoformat(), "file_age_days": age,
            "message": "File last saved %s; days since last contact used as provided." % fmt_date(saved)}


# ---------------------------------------------------------------- contacts

def build_contacts(roster, goals, freshness, today):
    contacts, checks = [], []
    skipped, bad = [], []
    adjust = freshness["adjust_days"]
    for rec in roster["records"]:
        name = re.sub(r"\s+", " ", rec.get("name", "")).strip()
        if not name:
            skipped.append(rec["_row"])
            continue
        days_reported = parse_int(rec.get("days_since"))
        from_date = False
        if days_reported is None and rec.get("last_contact_date"):
            d = parse_date_cell(rec["last_contact_date"])
            if d:
                days_reported = (today - d).days  # already measured from today: never add the file-age correction
                from_date = True
        if days_reported is not None and days_reported < 0:
            bad.append("%s: days since last contact is negative (%s)" % (name, rec.get("days_since")))
            days_reported = None
        if days_reported is None:
            bad.append("%s: no usable 'days since last contact' value" % name)
        strength = parse_int(rec.get("strength"))
        if strength is not None and not 1 <= strength <= 5:
            bad.append("%s: relationship strength %s is outside 1 to 5" % (name, rec.get("strength")))
            strength = None
        tags = [t.strip().lower() for t in re.split(r"[,;]", rec.get("tags", "")) if t.strip()]
        c = {
            "id": "C%02d" % (len(contacts) + 1),
            "row": rec["_row"],
            "name": name,
            "first_name": name.split(" ")[0],
            "role": rec.get("role", ""),
            "company": rec.get("company", ""),
            "how_known": rec.get("how_known", ""),
            "days_since_reported": days_reported,
            "days_since": None if days_reported is None else days_reported + (0 if from_date else adjust),
            "channel": rec.get("channel", ""),
            "summary": rec.get("summary", ""),
            "strength": strength,
            "tags": tags,
            "notes": rec.get("notes", ""),
        }
        contacts.append(c)
    if skipped:
        checks.append({"kind": "skipped-rows", "severity": "info", "ids": [],
                       "message": "Skipped %d row(s) with no name (spreadsheet rows %s)." % (len(skipped), ", ".join(map(str, skipped)))})
    if bad:
        checks.append({"kind": "bad-values", "severity": "info", "ids": [],
                       "message": "Some values could not be used: " + "; ".join(bad) + "."})

    by_full = {}
    by_first = {}
    for c in contacts:
        by_full.setdefault(c["name"].lower(), []).append(c)
        by_first.setdefault(c["first_name"].lower(), []).append(c)

    for c in contacts:
        context = (c["summary"] + " " + c["notes"]).strip()
        n_words = words(context)
        c["goal_ids"] = [g["id"] for g in goals if any(t == gt or t.startswith(gt + "-") for t in c["tags"] for gt in g["tags"])]
        hold = TIMING_HOLD.search(context)
        hold_sentence = None
        if hold:
            for sent in re.split(r"(?<=[.!?])\s+", context):
                if TIMING_HOLD.search(sent):
                    hold_sentence = sent.strip()
                    break
        personal = (c["how_known"].strip().lower() == "personal" or bool(PERSONAL_TAGS & set(c["tags"]))) and not c["goal_ids"]
        c["flags"] = {
            "thin_context": (not c["summary"].strip() and not c["notes"].strip()) or n_words < 12
                            or (n_words < 25 and c["strength"] is not None and c["strength"] <= 1),
            "dormant": c["days_since"] is not None and c["days_since"] >= DORMANT_DAYS,
            "near_dormant": c["days_since"] is not None and NEAR_DORMANT_DAYS <= c["days_since"] < DORMANT_DAYS,
            "timing_hold": hold_sentence,
            "personal": personal,
            "duplicate_name": len(by_full[c["name"].lower()]) > 1,
            "shares_first_name_with": [o["id"] for o in by_first[c["first_name"].lower()] if o is not c],
        }
    return contacts, checks


# ---------------------------------------------------------------- checks that compare goals and roster

def describe(c):
    bits = [c["role"], c["company"]]
    return "%s (%s)" % (c["name"], ", ".join(b for b in bits if b)) if any(bits) else c["name"]


def goal_checks(goals, contacts):
    checks = []
    for g in goals:
        for org in g["known_orgs"]:
            hits = [c for c in contacts if any(org_matches(a, c["company"]) for a in org["aliases"])
                    or any(t in c["tags"] for t in org["tags"])]
            if not hits:
                checks.append({
                    "kind": "goal-org-missing", "severity": "ask", "goal": g["id"], "ids": [],
                    "message": "Goals say she already has a way into %s, but nobody in the roster works there." % org["label"],
                    "ask": "Who is her %s contact? Add them to the roster so the brief can include them." % org["label"]})
        for org in g["wanted_orgs"]:
            hits = [c for c in contacts if any(org_matches(a, c["company"]) for a in org["aliases"])
                    or any(t in c["tags"] for t in org["tags"])]
            if hits:
                checks.append({
                    "kind": "goal-org-found", "severity": "ask", "goal": g["id"], "ids": [c["id"] for c in hits],
                    "message": "Goals say she has no contact at %s yet, but the roster has %s."
                               % (org["label"], ", ".join(describe(c) for c in hits)),
                    "ask": "Is the goals text out of date, or are these not the right kind of contact?"})
            else:
                checks.append({
                    "kind": "goal-org-gap", "severity": "info", "goal": g["id"], "ids": [],
                    "message": "No one in the roster at %s, which the goals name as a first-contact target." % org["label"]})
    return checks


def reference_checks(contacts):
    """Find people mentioned in notes ("intro from Jordan at Kestrel Capital") and check they resolve to one roster row."""
    first_names = {c["first_name"].lower() for c in contacts}
    checks, seen = [], set()
    for c in contacts:
        text = c["notes"] + " " + c["summary"]
        refs = [(m.group(1), m.group(2)) for m in REF_INTRO.finditer(text)]
        refs += [(m.group(1), m.group(2)) for m in REF_AT.finditer(text)
                 if m.group(1).lower() in first_names and m.group(1).lower() not in NOT_NAMES]
        for person, org in refs:
            person = person.strip()
            org = re.sub(r"[.,;:]+$", "", org.strip())
            if person.split(" ")[0].lower() in NOT_NAMES or (c["id"], person, org) in seen:
                continue
            seen.add((c["id"], person, org))
            if " " in person:
                named = [o for o in contacts if o["name"].lower() == person.lower() and o is not c]
            else:
                named = [o for o in contacts if o["first_name"].lower() == person.lower() and o is not c]
            at_org = [o for o in contacts if org_matches(org, o["company"]) and o is not c]
            resolved = [o for o in named if o in at_org]
            if len(resolved) == 1:
                continue
            if not named:
                who = "There is no %s in the roster" % person
            else:
                who = "The only %s in the roster %s %s" % (
                    person, "is" if len(named) == 1 else "are", " and ".join(describe(o) for o in named))
            org_side = ("the only %s contact is %s" % (org, describe(at_org[0]))) if len(at_org) == 1 else (
                "the %s contacts are %s" % (org, ", ".join(describe(o) for o in at_org)) if at_org else
                "nobody in the roster works at %s" % org)
            checks.append({
                "kind": "unresolved-reference", "severity": "ask", "ids": [c["id"]] + [o["id"] for o in named + at_org],
                "message": "%s's notes mention \"%s at %s\". %s, and %s." % (c["name"], person, org, who, org_side),
                "ask": "Who is %s at %s? Check before thanking or name-dropping anyone." % (person, org)})
    return checks


def roster_checks(contacts):
    checks = []
    seen = set()
    for c in contacts:
        if c["flags"]["duplicate_name"] and c["name"].lower() not in seen:
            seen.add(c["name"].lower())
            dups = [o for o in contacts if o["name"].lower() == c["name"].lower()]
            checks.append({
                "kind": "duplicate-name", "severity": "ask", "ids": [o["id"] for o in dups],
                "message": "%d rows are named %s: %s. The brief treats them as different people and always shows the company."
                           % (len(dups), c["name"], "; ".join("%s (row %d)" % (describe(o), o["row"]) for o in dups)),
                "ask": "Are these the same person? If so, delete the older row before next week."})
    thin = [c for c in contacts if c["flags"]["thin_context"]]
    if thin:
        checks.append({"kind": "thin-context", "severity": "info", "ids": [c["id"] for c in thin],
                       "message": "Too little on file to recommend a real move: %s." % ", ".join(describe(c) for c in thin)})
    near = [c for c in contacts if c["flags"]["near_dormant"] and (c["goal_ids"] or not c["flags"]["personal"])]
    if near:
        checks.append({"kind": "near-dormant", "severity": "info", "ids": [c["id"] for c in near],
                       "message": "About to pass 60 days without contact: %s."
                                  % ", ".join("%s (%d days)" % (c["name"], c["days_since"]) for c in near)})
    holds = [c for c in contacts if c["flags"]["timing_hold"]]
    if holds:
        checks.append({"kind": "timing-hold", "severity": "info", "ids": [c["id"] for c in holds],
                       "message": "Stated timing to respect: %s."
                                  % "; ".join("%s: \"%s\"" % (c["name"], c["flags"]["timing_hold"]) for c in holds)})
    return checks


# ---------------------------------------------------------------- the packet

def build_packet(roster, goals_doc, today, as_of=None, use_as_is=False):
    freshness = decide_freshness(roster, today, as_of=as_of, use_as_is=use_as_is)
    goals = goals_doc["goals"]
    contacts, checks = build_contacts(roster, goals, freshness, today)
    if not contacts:
        raise NFError("NF-03", "The roster has no rows with a name in them.",
                      "Check you exported the roster sheet with its data, then run again.")
    checks = [{"kind": "freshness", "severity": "info" if freshness["status"] != "unconfirmed" else "ask", "ids": [],
               "message": freshness["message"],
               "ask": "If this export is older than it looks, run again with as-of=YYYY-MM-DD (the export date)."
               if freshness["status"] == "unconfirmed" else None}] + checks
    if roster["columns_missing"]:
        from nf_read import PRETTY
        checks.append({"kind": "missing-columns", "severity": "info", "ids": [],
                       "message": "Columns not in this roster (the brief works without them, with less to go on): %s."
                                  % ", ".join(PRETTY[c] for c in roster["columns_missing"])})
    checks += roster_checks(contacts) + goal_checks(goals, contacts) + reference_checks(contacts)
    if goals_doc.get("last_reviewed") and (today - goals_doc["last_reviewed"]).days > GOALS_REVIEW_DAYS:
        checks.append({"kind": "goals-stale", "severity": "ask", "ids": [],
                       "message": "goals.md was last reviewed %s." % fmt_date(goals_doc["last_reviewed"]),
                       "ask": "Are these still the CEO's goals this quarter?"})

    eligible_by_goal = {g["id"]: [c["id"] for c in contacts if g["id"] in c["goal_ids"] and not c["flags"]["thin_context"]]
                        for g in goals}
    eligible_dormant = [c["id"] for c in contacts if c["flags"]["dormant"] and not c["flags"]["thin_context"]
                        and not c["flags"]["personal"]]
    return {
        "schema": "network-focus/packet@1",
        "today": today.isoformat(),
        "week_of": monday_of(today).isoformat(),
        "company": goals_doc.get("company", ""),
        "roster": {"file": roster["file"], "sheet": roster["sheet"], "rows": len(contacts),
                   "columns_found": roster["columns_found"], "columns_missing": roster["columns_missing"],
                   "columns_unknown": roster["columns_unknown"]},
        "freshness": freshness,
        "goals": [{"id": g["id"], "title": g["title"], "label": g.get("label") or "", "what": g["what"], "tags": g["tags"],
                   "known_orgs": [o["label"] for o in g["known_orgs"]],
                   "wanted_orgs": [o["label"] for o in g["wanted_orgs"]]} for g in goals],
        "contacts": contacts,
        "checks": checks,
        "eligible": {"by_goal": eligible_by_goal, "dormant": eligible_dormant},
        "rules": {"dormant_days": DORMANT_DAYS, "top_picks": [3, 5], "dormant_picks": [2, 3], "holding_max": 2,
                  "ceo_questions_max": 3},
    }
