"""Fact-check brief.json against packet.json. Errors block the brief; warnings go to the reviewer and the run log.

The orchestrator runs this itself after the strategist writes the brief: a check the model says it ran
is not a check. On success the brief's sha256 is recorded, and the renderer refuses any other file.
"""

import datetime as dt
import hashlib
import json
import os
import re
import unicodedata

from nf_errors import EXIT_OK, EXIT_CHECK_FAILED, EXIT_BLOCKED, read_bytes, read_json

MOVE_TYPES = ["email", "call", "coffee", "meeting", "intro-request", "follow-up", "send-materials",
              "light-touch", "prepare", "learn-more"]
HOLD_OK_MOVES = {"prepare", "light-touch"}
WEAK_TIE_MOVES = {"intro-request", "learn-more"}
# Character caps. tests/test_validate_and_render.py proves a brief at every cap still prints on one page.
LIMITS = {"headline": 110, "why": 170, "action": 150, "risk": 110, "opening_line": 220,
          "dormant_why": 130, "dormant_action": 120, "reason": 100, "question": 120, "quote": 200}
EVIDENCE_FIELDS = ["name", "role", "company", "how_known", "channel", "summary", "tags", "notes"]

# Words that may be capitalised in prose without being a person or organisation from the roster.
COMMON = set("""
a an and as ask at be book but by call can check confirm coffee draft email follow for from get give her his how if in
introduce invite is it keep let lunch mention monday tuesday wednesday thursday friday saturday sunday january february
march april may june july august september october november december next no not now of offer on or our propose reach
reconnect reopen reply schedule send set share she suggest tell thank thanks that the their them then this to update use
we week what when who why will with without write you your before after during this weekend today tomorrow also both
one two three four five once quick short brief warm cold full new loop circle note open close name hire hiring raise
series round seed deck pitch board advisor advisors investor investors partner partners founder founders cto ceo vp
ai ml eng engineering safety research researcher researchers team teams talent candidate candidates intro intros
""".split())


def hard_cap(limit):
    """A little over the target is a warning, not a retry: the one-page check is the real constraint."""
    return limit + max(10, limit // 10)


def norm(text):
    t = unicodedata.normalize("NFKC", str(text or ""))
    t = t.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", t).strip().lower()


def strip_outer(text):
    return text.strip(" \"'.,;:!?()")


def quote_in_field(quote, field_text):
    """True if every '...'-separated fragment of the quote appears, in order, inside this one field."""
    q, f = strip_outer(norm(quote)), norm(field_text)
    if not q:
        return False
    frags = [strip_outer(x) for x in q.split("...") if strip_outer(x)]
    if not frags:
        return False
    if len(frags) == 1 and q == strip_outer(f):
        return True
    pos = 0
    for frag in frags:
        if len(frag.split()) < 2 and frag != strip_outer(f):
            return False
        i = f.find(frag, pos)
        if i < 0:
            return False
        pos = i + len(frag)
    return True


def field_text(contact, field):
    if field == "tags":
        return ", ".join(contact.get("tags") or [])
    return contact.get(field) or ""


DAYS_PATTERNS = [
    re.compile(r"(over|more than|nearly|almost|about|around|roughly|~)?\s*(\d+)(\+)?\s*days?\s+"
               r"(?:ago|since|quiet|silent|without|of\s+silence|with\s+no|of\s+no)", re.I),
    re.compile(r"(?:quiet|silent|silence|dormant|cold|no\s+contact|not\s+spoken|since\s+(?:the\s+)?last\s+contact)"
               r"\s+(?:for\s+|of\s+|in\s+)?(over|more than|nearly|almost|about|around|roughly|~)?\s*(\d+)(\+)?\s+days", re.I),
    re.compile(r"(over|more than|nearly|almost|about|around|roughly|~)?\s*(\d+)(\+)?-day\s+(?:silence|gap|quiet)", re.I),
]


def day_claims_ok(text, contact):
    """Return a list of wrong 'N days ago' style claims in text about this contact.

    Only the freshness-adjusted days_since counts: it is the number printed on the card, so prose that repeats
    the stale spreadsheet figure would contradict the page.
    """
    actual = {contact.get("days_since")} - {None}
    if not actual:
        return []
    wrong = []
    for pat in DAYS_PATTERNS:
        for m in pat.finditer(text or ""):
            qual, n, plus = (m.group(1) or "").lower(), int(m.group(2)), m.group(3)
            if plus or qual in ("over", "more than"):
                ok = any(a >= n for a in actual)
            elif qual:
                ok = any(abs(a - n) <= max(3, n // 10) for a in actual)
            else:
                ok = n in actual
            if not ok:
                wrong.append(m.group(0).strip())
    return wrong


def corpus_words(packet):
    bits = [packet.get("company", "")]
    for g in packet["goals"]:
        bits += [g["title"], g["what"]] + g["known_orgs"] + g["wanted_orgs"] + g["tags"]
    for c in packet["contacts"]:
        bits += [c["name"], c["role"], c["company"], c["how_known"], c["channel"], c["summary"], c["notes"]] + c["tags"]
    for chk in packet["checks"]:
        bits += [chk.get("message") or "", chk.get("ask") or ""]
    return set(re.findall(r"[a-z0-9']+", " ".join(bits).lower()))


CAP_SEQ = re.compile(r"\b[A-Z][A-Za-z'\-]*(?:\s+[A-Z][A-Za-z'\-]*)+")


def _tokens(phrase):
    out = []
    for t in phrase.split():
        t = t.strip("'-").lower()
        out.append(t[:-2] if t.endswith("'s") else t)
    return out


def people_index(packet):
    """Full names, first names and last names of everyone on the roster."""
    full, first, last = set(), set(), set()
    for c in packet["contacts"]:
        parts = _tokens(c["name"])
        if len(parts) >= 2:
            full.add(" ".join(parts))
            first.add(parts[0])
            last.add(parts[-1])
    return full, first, last


def unknown_names(text, known, people):
    """Names that are not real roster entities.

    Two kinds of error: a run of two or more capitalised words that appear nowhere in the roster or goals, and a
    first name glued to another person's surname ("Avery Blake" when the roster has Avery Stone and Jordan Blake),
    which passes a word-by-word check but names nobody.
    """
    full, first, last = people
    bad = []
    for m in CAP_SEQ.finditer(text or ""):
        tokens = _tokens(m.group(0))
        unknown = [t for t in tokens if t and t not in known and t not in COMMON]
        if len(unknown) >= 2:
            bad.append(m.group(0))
            continue
        for a, b in zip(tokens, tokens[1:]):
            if a in first and b in last and "%s %s" % (a, b) not in full:
                bad.append(m.group(0))
                break
    return bad


SINGLE_CAP = re.compile(r"\b([A-Z][a-z]+)\b")


def unknown_single_names(text, known):
    """Lone capitalised words mid-sentence that are not in the roster or goals: maybe an invented person."""
    out = []
    for m in SINGLE_CAP.finditer(text or ""):
        start = m.start(1)
        before = text[:start].rstrip()
        if not before or before[-1] in ".!?:;(\"'":
            continue  # sentence start: capitalised for grammar, not because it is a name
        word = _tokens(m.group(1))[0]
        if word not in known and word not in COMMON:
            out.append(m.group(1))
    return out


def _plain(text):
    t = norm(text).replace("'s ", " ")
    return " %s " % re.sub(r"[^a-z0-9]+", " ", t).strip()


def has_anchor(text, anchor):
    """True if the anchor appears as whole words ("ARC" in "ARC's researchers", not in "search")."""
    a = _plain(anchor).strip()
    return bool(a) and (" %s " % a) in _plain(text + " ")


def check(brief, packet):
    errors, warnings = [], []
    contacts = {c["id"]: c for c in packet["contacts"]}
    goal_ids = [g["id"] for g in packet["goals"]]
    known = corpus_words(packet)
    people = people_index(packet)

    def err(where, msg):
        errors.append("%s: %s" % (where, msg))

    def warn(where, msg):
        warnings.append("%s: %s" % (where, msg))

    if not isinstance(brief, dict):
        return ["brief.json must be a JSON object."], []
    if brief.get("week_of") != packet["week_of"]:
        err("week_of", "must be %s (the Monday of this week), not %r." % (packet["week_of"], brief.get("week_of")))
    head = brief.get("headline")
    if not isinstance(head, str) or not head.strip():
        err("headline", "missing. One sentence on what matters most this week.")
    elif len(head) > hard_cap(LIMITS["headline"]):
        err("headline", "is %d characters; the limit is %d." % (len(head), LIMITS["headline"]))
    elif len(head) > LIMITS["headline"]:
        warn("headline", "is %d characters, a little over the %d target." % (len(head), LIMITS["headline"]))

    sections = {}
    for key in ("top_picks", "dormant", "holding", "ceo_questions", "uncovered_goals"):
        val = brief.get(key, [])
        if not isinstance(val, list):
            err(key, "must be a list.")
            val = []
        sections[key] = val

    seen = {}

    def label(sec, i, item):
        cid = item.get("id") if isinstance(item, dict) else None
        c = contacts.get(cid)
        return "%s[%d] (%s %s)" % (sec, i + 1, cid, c["name"] if c else "?")

    def check_text(where, key, text, limit, c=None):
        if not isinstance(text, str) or not text.strip():
            err(where, "'%s' is missing." % key)
            return
        if len(text) > hard_cap(limit):
            err(where, "'%s' is %d characters; the limit is %d. Shorten it." % (key, len(text), limit))
        elif len(text) > limit:
            warn(where, "'%s' is %d characters, a little over the %d target." % (key, len(text), limit))
        for name in unknown_names(text, known, people):
            err(where, "'%s' mentions \"%s\", which is not a person or organisation in the roster or the goals. Only name people and organisations from the packet, with their full names." % (key, name))
        for word in unknown_single_names(text, known):
            warn(where, "'%s' mentions \"%s\", which is not in the roster or the goals; if it is a person, it is not one on file." % (key, word))
        if c is not None:
            for claim in day_claims_ok(text, c):
                err(where, "'%s' says \"%s\" but the roster says %s days. Use the packet's days_since." % (key, claim, c["days_since"]))
        if "—" in text or "–" in text:
            warn(where, "'%s' uses a long dash; use a comma, colon or full stop instead." % key)

    def check_entry(sec, i, item, dormant=False):
        where = label(sec, i, item)
        if not isinstance(item, dict):
            err(where, "must be an object.")
            return None
        cid = item.get("id")
        c = contacts.get(cid)
        if c is None:
            err(where, "id %r is not a contact id in the packet (C01, C02, ...)." % cid)
            return None
        if cid in seen:
            err(where, "%s is already in %s. Each person appears once." % (c["name"], seen[cid]))
        seen[cid] = sec
        goals = item.get("goals")
        if not isinstance(goals, list) or not goals:
            err(where, "'goals' must list at least one goal id from: %s." % ", ".join(goal_ids))
            goals = []
        for g in goals:
            if g not in goal_ids:
                err(where, "goal %r is not one of: %s." % (g, ", ".join(goal_ids)))
            elif g not in c["goal_ids"]:
                warn(where, "tagged for goal '%s' but the roster tags (%s) do not say so; make sure 'why' explains the link."
                     % (g, ", ".join(c["tags"]) or "none"))
        check_text(where, "why", item.get("why"), LIMITS["dormant_why" if dormant else "why"], c)
        move = item.get("move")
        if not isinstance(move, dict):
            err(where, "'move' must be an object with 'type' and 'action'.")
            move = {}
        mtype = move.get("type")
        if mtype not in MOVE_TYPES:
            err(where, "move type %r must be one of: %s." % (mtype, ", ".join(MOVE_TYPES)))
        check_text(where, "move.action", move.get("action"), LIMITS["dormant_action" if dormant else "action"], c)
        if not dormant:
            check_text(where, "risk", item.get("risk"), LIMITS["risk"], c)
            ol = item.get("opening_line")
            if ol is not None:
                check_text(where, "opening_line", ol, LIMITS["opening_line"], c)
        if c["flags"]["timing_hold"] and mtype not in HOLD_OK_MOVES:
            err(where, "%s asked for timing: \"%s\". Use move type 'prepare' or 'light-touch', or put them in 'holding'."
                % (c["name"], c["flags"]["timing_hold"]))
        if c["flags"]["personal"]:
            err(where, "%s is a personal relationship with no link to the goals; leave them out." % c["name"])
        weak = c["flags"]["thin_context"] or (c["strength"] is not None and c["strength"] <= 1)
        allowed = WEAK_TIE_MOVES | ({"light-touch"} if dormant else set())
        if weak and mtype not in allowed:
            err(where, "%s is a weak or thin relationship (strength %s); only %s moves are allowed."
                % (c["name"], c["strength"], ", ".join("'%s'" % m for m in sorted(allowed))))
        if dormant:
            if c["days_since"] is None or c["days_since"] < packet["rules"]["dormant_days"]:
                err(where, "dormant needs %d+ days since contact; %s is at %s." % (packet["rules"]["dormant_days"], c["name"], c["days_since"]))
            if c["flags"]["thin_context"]:
                err(where, "%s has too little on file to recommend reopening." % c["name"])
        ev = item.get("evidence")
        if not isinstance(ev, list) or not ev:
            err(where, "needs 1 to 3 'evidence' quotes copied exactly from this contact's row.")
            ev = []
        if len(ev) > 3:
            err(where, "at most 3 evidence quotes.")
        for j, e in enumerate(ev):
            if not isinstance(e, dict) or not isinstance(e.get("quote"), str):
                err(where, "evidence[%d] must be {\"field\": ..., \"quote\": ...}." % (j + 1))
                continue
            q, fname = e["quote"], e.get("field")
            if len(q) > LIMITS["quote"]:
                err(where, "evidence[%d] is longer than %d characters." % (j + 1, LIMITS["quote"]))
            hits = [f for f in EVIDENCE_FIELDS if quote_in_field(q, field_text(c, f))]
            if not hits:
                err(where, "evidence quote \"%s\" is not in any single field of %s's row. Copy the exact words (use ... to skip words)."
                    % (q, c["name"]))
            elif fname and fname not in hits:
                warn(where, "evidence quote \"%s\" is in '%s', not '%s'." % (q, hits[0], fname))
        return c

    tops = sections["top_picks"]
    pool = [c for c in packet["contacts"] if not c["flags"]["thin_context"] and not c["flags"]["personal"]]
    lo, hi = packet["rules"]["top_picks"]
    if not (min(lo, len(pool)) <= len(tops) <= hi):
        err("top_picks", "has %d people; it needs %d to %d." % (len(tops), min(lo, len(pool)), hi))
    for i, item in enumerate(tops):
        check_entry("top_picks", i, item)

    dorm = sections["dormant"]
    eligible = [x for x in packet["eligible"]["dormant"] if x not in {t.get("id") for t in tops if isinstance(t, dict)}]
    lo, hi = packet["rules"]["dormant_picks"]
    if not (min(lo, len(eligible)) <= len(dorm) <= hi):
        err("dormant", "has %d people; it needs %d to %d (eligible 60+ day contacts not already in top_picks: %s)."
            % (len(dorm), min(lo, len(eligible)), hi, ", ".join(eligible) or "none"))
    for i, item in enumerate(dorm):
        check_entry("dormant", i, item, dormant=True)

    holding = sections["holding"]
    if len(holding) > packet["rules"]["holding_max"]:
        err("holding", "at most %d entries." % packet["rules"]["holding_max"])
    for i, item in enumerate(holding):
        where = label("holding", i, item)
        cid = item.get("id") if isinstance(item, dict) else None
        if cid not in contacts:
            err(where, "id %r is not a contact id in the packet." % cid)
            continue
        if cid in seen:
            err(where, "%s is already in %s." % (contacts[cid]["name"], seen[cid]))
        seen[cid] = "holding"
        check_text(where, "reason", item.get("reason"), LIMITS["reason"], contacts[cid])

    qs = sections["ceo_questions"]
    groups = packet.get("question_groups") or []
    by_gid = {g["id"]: g for g in groups}
    qmax = packet["rules"]["ceo_questions_max"]
    if len(qs) > qmax:
        err("ceo_questions", "at most %d questions." % qmax)
    if qs and not groups:
        err("ceo_questions", "no data problem needs the CEO this week (packet.question_groups is empty), so leave ceo_questions empty.")
    covered = set()
    for i, q in enumerate(qs):
        where = "ceo_questions[%d]" % (i + 1)
        if not isinstance(q, dict):
            err(where, 'must be an object: {"question": "...", "covers": ["<id from packet.question_groups>"]}.')
            continue
        check_text(where, "question", q.get("question"), LIMITS["question"])
        cov = q.get("covers")
        if not isinstance(cov, list) or not cov or not all(isinstance(x, str) for x in cov):
            err(where, "'covers' must list the packet.question_groups id(s) this question asks about.")
            continue
        if len(set(cov)) != len(cov):
            err(where, "'covers' lists the same group twice.")
        text = q.get("question") if isinstance(q.get("question"), str) else ""
        for gid in cov:
            g = by_gid.get(gid)
            if g is None:
                err(where, "'covers' has %r, which is not an id in packet.question_groups (%s)." % (gid, ", ".join(by_gid) or "none"))
                continue
            if gid in covered:
                warn(where, "group %s is already asked by another question; merge them to save a slot." % gid)
            covered.add(gid)
            for msg, anchors in zip(g["messages"], g["anchors"]):
                if anchors and not any(has_anchor(text, a) for a in anchors):
                    warn(where, "covers %s but never mentions %s; make sure it really asks: %s" % (gid, " or ".join("'%s'" % a for a in anchors[:3]), msg))
    missing = [g for g in groups[:qmax] if g["id"] not in covered]
    if missing:
        err("ceo_questions", "these data problems must be asked (they are the most important %d; merge related ones into one "
            "question if you are short of slots): %s"
            % (min(len(groups), qmax), " | ".join("%s: %s Suggested question: %s" % (g["id"], " ".join(g["messages"]), g["ask"]) for g in missing)))

    covered = set()
    for item in tops + dorm:
        if isinstance(item, dict) and isinstance(item.get("goals"), list):
            covered.update(item["goals"])
    uncovered_listed = {u.get("goal") for u in sections["uncovered_goals"] if isinstance(u, dict)}
    for g in goal_ids:
        n = len(packet["eligible"]["by_goal"].get(g, []))
        if g not in covered and n:
            err("goals", "goal '%s' has %d eligible contact(s) but no pick serves it. Every goal with eligible contacts needs at least one pick." % (g, n))
        if g in uncovered_listed and n:
            err("uncovered_goals", "'%s' has eligible contacts, so it cannot be listed as uncovered." % g)
    return errors, warnings


def main(run_dir, out):
    packet_path = os.path.join(run_dir, "packet.json")
    brief_path = os.path.join(run_dir, "brief.json")
    if not os.path.isfile(packet_path):
        out("NF-07 The brief failed the fact check\n  What happened: there is no packet.json in %s.\n"
            "  What to do: run the brief again from the start." % run_dir)
        return EXIT_BLOCKED
    packet = read_json(packet_path)
    if not os.path.isfile(brief_path):
        out("FAIL brief.json was not written to %s" % brief_path)
        return EXIT_CHECK_FAILED
    raw = read_bytes(brief_path)
    try:
        brief = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as e:
        errors, warnings, brief = ["brief.json is not valid JSON (%s). Write only the JSON object, no commentary." % e], [], None
    else:
        errors, warnings = check(brief, packet)
        if not errors:
            import nf_render
            pages = nf_render.page_count_at_floor(brief, packet)
            if pages and pages > 1:
                errors.append("TOO LONG: the brief prints to %d pages; it must fit one. Cut 'why', 'move.action' and 'risk' "
                              "by about a quarter, keep every fact and pick, and write the whole file again." % pages)

    result ={"ok": not errors, "checked_at": dt.datetime.now().isoformat(timespec="seconds"),
              "errors": errors, "warnings": warnings,
              "sha256": hashlib.sha256(raw).hexdigest() if not errors else None,
              "packet_sha256": hashlib.sha256(read_bytes(packet_path)).hexdigest() if not errors else None}
    with open(os.path.join(run_dir, "validation.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    with open(os.path.join(run_dir, "validation-history.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": result["checked_at"], "ok": result["ok"], "errors": len(errors),
                            "warnings": len(warnings)}) + "\n")
    if errors:
        out("FAIL %d error(s). Fix every one and write brief.json again:" % len(errors))
        for e in errors:
            out("  - " + e)
    else:
        out("PASS fact check (%d warning(s)). Fingerprint %s" % (len(warnings), result["sha256"][:12]))
    for w in warnings:
        out("  warning: " + w)
    return EXIT_OK if not errors else EXIT_CHECK_FAILED
