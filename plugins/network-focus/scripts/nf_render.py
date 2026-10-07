"""Render the validated brief to a one-page HTML and PDF, and write the plain-English run log.

Names, roles, companies, strength and days come from the packet by row ID, never from the model's text.
The renderer refuses a brief whose fingerprint differs from the one the validator approved.
"""

import datetime as dt
import glob
import hashlib
import html
import json
import os
import re
import shutil
import string
import subprocess
import sys
import tempfile
from pathlib import Path

from nf_errors import EXIT_OK, EXIT_BLOCKED, EXIT_TOO_LONG, CODES, read_bytes, read_text, read_json

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(HERE)
GOAL_LABELS = {}


def esc(text):
    return html.escape(str(text or ""), quote=True)


def find_browser():
    """Chrome or Edge (or Chromium/Brave) for headless PDF printing. NF_BROWSER overrides."""
    env = os.environ.get("NF_BROWSER")
    if env and os.path.isfile(env):
        return env
    candidates = []
    if sys.platform.startswith("win"):
        for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
            if base:
                candidates += [os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"),
                               os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                               os.path.join(base, "BraveSoftware", "Brave-Browser", "Application", "brave.exe")]
    elif sys.platform == "darwin":
        for base in ("/Applications", os.path.expanduser("~/Applications")):
            candidates += [os.path.join(base, "Google Chrome.app", "Contents", "MacOS", "Google Chrome"),
                           os.path.join(base, "Microsoft Edge.app", "Contents", "MacOS", "Microsoft Edge"),
                           os.path.join(base, "Chromium.app", "Contents", "MacOS", "Chromium"),
                           os.path.join(base, "Brave Browser.app", "Contents", "MacOS", "Brave Browser")]
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "msedge", "chrome"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def count_pages(pdf_path):
    data = read_bytes(pdf_path)
    return len(re.findall(rb"/Type\s*/Page\b", data))


CHROME_FLAGS = [
    "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--disable-extensions",
    "--no-pdf-header-footer", "--print-to-pdf-no-header", "--hide-scrollbars", "--mute-audio",
    "--disable-background-networking", "--disable-sync", "--disable-default-apps", "--disable-breakpad",
    # macOS: without these, Chrome can sit waiting on the login keychain and never print.
    "--use-mock-keychain", "--password-store=basic",
]


def _pdf_complete(path):
    if not os.path.isfile(path) or os.path.getsize(path) < 1000:
        return False
    with open(path, "rb") as f:
        f.seek(max(0, os.path.getsize(path) - 1024))
        return b"%%EOF" in f.read()


def print_pdf(browser, html_path, pdf_path, timeout=60):
    """Print with headless Chrome/Edge. Waits for a complete PDF rather than for the browser to exit:
    on some systems the browser lingers after printing, and waiting on it would look like a failure."""
    import time
    profile = tempfile.mkdtemp(prefix="nf-chrome-")
    proc = None
    try:
        if os.path.exists(pdf_path):
            os.remove(pdf_path)
        args = [browser] + CHROME_FLAGS + ["--user-data-dir=" + profile, "--print-to-pdf=" + pdf_path,
                                           Path(html_path).resolve().as_uri()]
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + timeout
        last_size = -1
        while time.time() < deadline:
            if _pdf_complete(pdf_path):
                size = os.path.getsize(pdf_path)
                if size == last_size:
                    break  # complete and no longer growing
                last_size = size
            elif proc.poll() is not None and not os.path.exists(pdf_path):
                break  # browser exited without printing
            time.sleep(0.25)
        return _pdf_complete(pdf_path)
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


def fmt_day(iso):
    d = dt.date.fromisoformat(iso)
    return "%d %s %d" % (d.day, d.strftime("%b"), d.year)


def goal_index(goal_id, goals):
    ids = [x["id"] for x in goals]
    return (ids.index(goal_id) if goal_id in ids else 0) % 5


def chips(goal_ids, goals):
    out = []
    for g in goal_ids:
        goal = next((x for x in goals if x["id"] == g), {"title": g})
        out.append('<span class="chip c%d">%s</span>' % (goal_index(g, goals), esc(short_goal(goal))))
    return " ".join(out)


def short_goal(goal):
    t = goal.get("label") or goal["title"]
    return t if len(t) <= 24 else t[:22].rstrip() + "..."


def dots(strength):
    return '<span class="dots" title="relationship strength %d of 5">%s</span>' % (
        strength, "".join('<i class="on"></i>' if i < strength else "<i></i>" for i in range(5)))


def facts(c, channel=True):
    """Strength as five dots, then recency: all from the roster row."""
    bits = []
    if c.get("strength") is not None:
        bits.append("strength" + dots(c["strength"]))
    if c.get("days_since") is not None:
        days = "%d day%s ago" % (c["days_since"], "" if c["days_since"] == 1 else "s")
        if channel and c.get("channel"):
            days += " &middot; " + esc(c["channel"])
        bits.append(days)
    return " &middot; ".join(bits)


def plugin_version():
    try:
        return read_json(os.path.join(PLUGIN_ROOT, ".claude-plugin", "plugin.json")).get("version") or ""
    except (OSError, ValueError):
        return ""


def question_text(q):
    return q.get("question", "") if isinstance(q, dict) else str(q)


def covered_groups(brief, packet):
    covered = set()
    for q in brief.get("ceo_questions") or []:
        if isinstance(q, dict) and isinstance(q.get("covers"), list):
            covered.update(x for x in q["covers"] if isinstance(x, str))
    groups = packet.get("question_groups") or []
    return [g for g in groups if g["id"] in covered], [g for g in groups if g["id"] not in covered]


def role_line(c):
    return ", ".join(x for x in (c.get("role"), c.get("company")) if x)


def as_list(unresolved):
    if not unresolved:
        return []
    return [unresolved] if isinstance(unresolved, str) else [u for u in unresolved if u]


WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
# Tried in order; the first that prints on one page wins. 9pt is the floor. The last step keeps 9pt but tightens
# spacing and drops the goal strip; it is the step the fact check's one-page test uses.
LAYOUTS = ((10.0, False), (9.5, False), (9.0, False), (9.0, True))
# The fact check runs before the reviewer, so it reserves room for the longest footer and a one-line banner.
FIT_REVIEWER = "revise, 3 issues open (see banner)"
FIT_BANNER = ["One reviewer issue to check by hand before the meeting, about a line long."]


def goal_strip(brief, packet, contacts, goals):
    """One tile per goal: who this brief recommends for it (names from the roster), and questions on the page.
    Which goal a pick serves is the strategist's judgment, so the strip says "picks", not "contacts"."""
    group_goal = {grp["id"]: grp.get("goal") for grp in packet.get("question_groups") or []}
    questions = [q for q in brief.get("ceo_questions") or [] if isinstance(q, dict) and isinstance(q.get("covers"), list)]
    tiles = []
    for g in goals:
        tops = [(i, contacts[p["id"]]["name"]) for i, p in enumerate(brief["top_picks"], start=1) if g["id"] in (p.get("goals") or [])]
        dorm = [contacts[p["id"]]["name"] for p in brief.get("dormant") or [] if g["id"] in (p.get("goals") or [])]
        # Count printed questions, not groups: one question may cover two of this goal's problems.
        asks = sum(1 for q in questions if any(group_goal.get(x) == g["id"] for x in q["covers"]))
        cls = "goal c%d" % goal_index(g["id"], goals)
        if tops:
            people = ", ".join("<b>%d</b> %s" % (i, esc(n)) for i, n in tops)
        elif dorm:
            people = "Reopen: " + ", ".join(esc(n) for n in dorm)
        else:
            cls += " none"  # the fact check only allows this when no eligible contact can serve the goal
            people = "No eligible contact this week"
        extra = []
        if tops and dorm:
            extra.append("+%d dormant" % len(dorm))
        if asks:
            extra.append('<span class="ask">%d question%s for you</span>' % (asks, "" if asks == 1 else "s"))
        tiles.append('<div class="%s"><div class="gl">%s</div><div class="gp">%s</div>%s</div>'
                     % (cls, esc(short_goal(g)), people, '<div class="gx">%s</div>' % " &middot; ".join(extra) if extra else ""))
    return "\n".join(tiles)


def build_html(brief, packet, validation, font_pt, unresolved, reviewer=None, compact=False):
    unresolved = as_list(unresolved)
    contacts = {c["id"]: c for c in packet["contacts"]}
    goals = packet["goals"]
    tpl = string.Template(read_text(os.path.join(PLUGIN_ROOT, "templates", "brief.html")))

    def move_html(p):
        return ('<div class="move"><span class="tick"></span><span class="mtype">%s</span>%s</div>'
                % (esc(p["move"]["type"].replace("-", " ")), esc(p["move"]["action"])))

    picks = []
    for i, p in enumerate(brief["top_picks"], start=1):
        c = contacts[p["id"]]
        picks.append(
            '<li class="pick c%d"><div class="who"><span class="num">%d</span><span class="name">%s</span>'
            '<span class="role">%s</span>%s<span class="facts">%s</span></div>'
            '<div class="grid"><div>%s</div>%s<div>%s</div></div></li>'
            % (goal_index((p.get("goals") or [""])[0], goals), i, esc(c["name"]), esc(role_line(c)),
               chips(p["goals"], goals), facts(c), esc(p["why"]), move_html(p), esc(p["risk"])))
    dormant = []
    for p in brief["dormant"]:
        c = contacts[p["id"]]
        strength = (" &middot; strength" + dots(c["strength"])) if c.get("strength") is not None else ""
        dormant.append(
            '<div class="drow"><div><span class="name">%s</span><span class="quiet">%s days quiet</span><br>'
            '<span class="role">%s</span><br>%s<span class="facts">%s</span></div><div>%s</div>%s</div>'
            % (esc(c["name"]), esc(c["days_since"]), esc(role_line(c)), chips(p["goals"], goals), strength,
               esc(p["why"]), move_html(p)))
    if not dormant:
        dormant.append('<p class="role">No one at 60+ days is worth reopening this week.</p>')

    labels = {g["id"]: g.get("label") or g["title"] for g in goals}
    uncovered = ["<li>No one on file can serve the %s goal: %s</li>" % (esc(labels.get(u.get("goal"), u.get("goal"))), esc(u.get("reason")))
                 for u in brief.get("uncovered_goals") or [] if isinstance(u, dict)]
    asked = ["<li>%s</li>" % esc(question_text(q)) for q in brief.get("ceo_questions") or []]
    _, left_over = covered_groups(brief, packet)
    more = ['<li class="more">+%d more data question%s in run-log.md</li>' % (len(left_over), "" if len(left_over) == 1 else "s")] if left_over else []
    questions = "".join(uncovered + asked + more) or "<li>Nothing this week.</li>"
    holding = "".join("<li><b>%s</b> (%s): %s</li>" % (esc(contacts[h["id"]]["name"]), esc(contacts[h["id"]]["company"]), esc(h["reason"]))
                      for h in brief.get("holding") or []) or "<li>No one.</li>"

    today = dt.date.fromisoformat(packet["today"])
    prepared = "Prepared %s %s from <b>%s</b> (%d contacts)" % (
        WEEKDAYS[today.weekday()], esc(fmt_day(packet["today"])), esc(packet["roster"]["file"]), packet["roster"]["rows"])
    fr = packet["freshness"]
    meta = '<span%s>%s</span>' % (' class="caveat"' if fr["status"] == "unconfirmed" else "", esc(fr["message"]))
    banner = ""
    if unresolved:
        banner = '<div class="banner">Needs a human check (%s): %s</div>' % ("NF-08", "; ".join(esc(u) for u in unresolved))
    version = plugin_version()
    footer_left = "Network Focus%s &middot; fact check passed (brief fingerprint %s)%s" % (
        " v" + esc(version) if version else "", esc((validation.get("sha256") or "")[:12]),
        " &middot; reviewer: " + esc(reviewer) if reviewer else "")
    company = packet.get("company")
    return tpl.substitute(
        week_label=esc(fmt_day(packet["week_of"])), font_pt=("%.2f" % font_pt).rstrip("0").rstrip("."),
        body_class="compact" if compact else "",
        company_suffix=(" &middot; " + esc(company) + " CEO") if company else "", prepared_html=prepared,
        meta_html=meta, banner_html=banner, headline=esc(brief["headline"]),
        goal_cols=max(1, min(len(goals), 4)), goals_html=goal_strip(brief, packet, contacts, goals),
        picks_html="\n".join(picks), dormant_html="\n".join(dormant), questions_html=questions, holding_html=holding,
        footer_left=footer_left, footer_right="The roster words behind every pick: run-log.md")


def page_count_at_floor(brief, packet):
    """Pages the brief would print to at the smallest allowed font, or None if no browser. Used by the fact check."""
    browser = find_browser()
    if not browser:
        return None
    tmp = tempfile.mkdtemp(prefix="nf-fit-")
    try:
        html_path = os.path.join(tmp, "fit.html")
        pdf_path = os.path.join(tmp, "fit.pdf")
        with open(html_path, "w", encoding="utf-8") as f:
            font_pt, compact = LAYOUTS[-1]
            f.write(build_html(brief, packet, {"sha256": "0" * 64}, font_pt, FIT_BANNER, reviewer=FIT_REVIEWER, compact=compact))
        if not print_pdf(browser, html_path, pdf_path):
            return None
        return count_pages(pdf_path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def reviewer_line(audit, unresolved):
    """The reviewer's verdict as printed in the footer. 'Addressed' is the strategist's answer, not a second review."""
    open_issues = as_list(unresolved)
    if open_issues:
        return "%d issue%s open (see banner)" % (len(open_issues), "" if len(open_issues) == 1 else "s")
    if not audit:
        return "not run"
    if audit.get("verdict") == "revise":
        return "must-fix issues addressed in one revision"
    return audit.get("verdict") or "not run"


def write_run_log(path, brief, packet, validation, audit, outputs, unresolved):
    contacts = {c["id"]: c for c in packet["contacts"]}
    hist_path = os.path.join(packet["run_dir"], "validation-history.jsonl")
    history = [json.loads(x) for x in read_text(hist_path).splitlines() if x.strip()] if os.path.isfile(hist_path) else []
    failed = sum(1 for h in history if not h.get("ok"))
    L = []
    L.append("# Network Focus run log: week of %s" % fmt_day(packet["week_of"]))
    L.append("")
    L.append("- Run finished: %s" % dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
    L.append("- Roster: %s (%d contacts%s)" % (packet["roster"]["file"], packet["roster"]["rows"],
                                              ", sheet '%s'" % packet["roster"]["sheet"] if packet["roster"]["sheet"] else ""))
    L.append("- Data: %s" % packet["freshness"]["message"])
    L.append("- Fact check: passed, %d warning(s), fingerprint %s (%d check run(s) this week, %d sent back for fixes)"
             % (len(validation["warnings"]), validation["sha256"], len(history), failed))
    if audit:
        L.append("- Reviewer: %s (%d issue(s))" % (audit.get("verdict", "?"), len(audit.get("issues") or [])))
    for u in as_list(unresolved):
        L.append("- %s %s: %s" % ("NF-08", CODES["NF-08"], u))
    for u in brief.get("uncovered_goals") or []:
        if isinstance(u, dict):
            L.append("- No one on file can serve goal '%s': %s" % (u.get("goal"), u.get("reason")))
    L.append("- Files: %s" % ", ".join(os.path.basename(o) for o in outputs))
    L.append("")
    L.append("## Data checks")
    for c in packet["checks"]:
        L.append("- [%s] %s%s" % (c["severity"], c["message"], (" " + c["ask"]) if c.get("ask") else ""))
    L.append("")
    groups = {g["id"]: g for g in packet.get("question_groups") or []}
    if groups:
        L.append("## Questions for the CEO")
        for q in brief.get("ceo_questions") or []:
            L.append("- %s" % question_text(q))
            for gid in (q.get("covers") or []) if isinstance(q, dict) else []:
                if gid in groups:
                    L.append("  - answers [%s] %s" % (gid, " ".join(groups[gid]["messages"])))
        _, left_over = covered_groups(brief, packet)
        for g in left_over:
            L.append("- Not on the page (lower priority): [%s] %s %s" % (g["id"], " ".join(g["messages"]), g["ask"]))
        L.append("")
    L.append("## Picks and the roster words behind them")
    for sec in ("top_picks", "dormant"):
        for p in brief.get(sec) or []:
            c = contacts[p["id"]]
            L.append("### %s (%s, %s) [%s]" % (c["name"], c["role"], c["company"], "top pick" if sec == "top_picks" else "dormant"))
            for e in p.get("evidence") or []:
                L.append("- %s: \"%s\"" % (e.get("field", "?"), e["quote"]))
            if p.get("opening_line"):
                L.append("- Draft opening line: %s" % p["opening_line"])
            L.append("")
    if validation["warnings"]:
        L.append("## Fact-check warnings")
        L += ["- " + w for w in validation["warnings"]]
        L.append("")
    if audit and audit.get("issues"):
        L.append("## Reviewer notes")
        for i in audit["issues"]:
            L.append("- [%s] %s%s" % (i.get("severity", "?"), i.get("problem", ""), (" Fix: " + i["fix"]) if i.get("fix") else ""))
        L.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


def main(run_dir, out, audit_unresolved=""):
    packet = read_json(os.path.join(run_dir, "packet.json"))
    vpath = os.path.join(run_dir, "validation.json")
    bpath = os.path.join(run_dir, "brief.json")
    if not (os.path.isfile(vpath) and os.path.isfile(bpath)):
        out("NF-07 %s\n  What happened: the brief has not passed the fact check yet.\n  What to do: run the brief again." % CODES["NF-07"])
        return EXIT_BLOCKED
    validation = read_json(vpath)
    raw = read_bytes(bpath)
    packet_raw = read_bytes(os.path.join(run_dir, "packet.json"))
    if (not validation.get("ok") or hashlib.sha256(raw).hexdigest() != validation.get("sha256")
            or hashlib.sha256(packet_raw).hexdigest() != validation.get("packet_sha256")):
        out("NF-07 %s\n  What happened: brief.json or packet.json changed after the fact check, or the brief never passed it.\n"
            "  What to do: run the fact check again (nf.py validate) before printing." % CODES["NF-07"])
        return EXIT_BLOCKED
    brief = json.loads(raw.decode("utf-8-sig"))
    apath = os.path.join(run_dir, "audit.json")
    audit = None
    if os.path.isfile(apath):
        try:
            audit = read_json(apath, "utf-8-sig")
        except ValueError:
            audit = {"verdict": "unreadable", "issues": []}

    week_dir = os.path.dirname(run_dir)
    stem = "network-focus-brief-%s" % packet["week_of"]
    html_path = os.path.join(week_dir, stem + ".html")
    pdf_path = os.path.join(week_dir, stem + ".pdf")
    browser = find_browser()

    if os.path.exists(pdf_path):
        os.remove(pdf_path)  # never leave an earlier PDF looking like this run's result
    tmp_pdf = os.path.join(week_dir, stem + ".printing.pdf")
    pages = None
    reviewer = reviewer_line(audit, audit_unresolved)
    for font_pt, compact in LAYOUTS:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(build_html(brief, packet, validation, font_pt, audit_unresolved, reviewer=reviewer, compact=compact))
        if not browser:
            break
        if not print_pdf(browser, html_path, tmp_pdf):
            out("NF-06 %s\n  What happened: %s did not produce a PDF.\n  What to do: close any Chrome windows that are "
                "stuck, run /network-focus:doctor, then run the brief again. The HTML version is at %s"
                % (CODES["NF-06"], browser, html_path))
            return EXIT_BLOCKED
        pages = count_pages(tmp_pdf)
        if pages <= 1:
            os.replace(tmp_pdf, pdf_path)
            break
    if os.path.exists(tmp_pdf):
        os.remove(tmp_pdf)  # a two-page draft is never handed to the operator
    outputs = [html_path] + ([pdf_path] if browser and pages == 1 else [])
    if browser and pages and pages > 1:
        out("TOO LONG the brief runs to %d pages at the smallest allowed font. Shorten 'why', 'move.action' and 'risk' "
            "(aim for about 25%% fewer words), keep every fact, then validate again." % pages)
        return EXIT_TOO_LONG
    log_path = os.path.join(week_dir, "run-log.md")
    write_run_log(log_path, brief, packet, validation, audit, outputs + [log_path], audit_unresolved)
    if browser:
        out("RENDERED %s" % pdf_path)
        out("Also: %s" % html_path)
    else:
        out("NF-06 %s\n  What happened: no Chrome or Edge on this computer, so the brief was made as a web page instead: %s\n"
            "  What to do: open that file and use File > Print to print it or save it as a PDF. For a PDF straight away "
            "next week, install Google Chrome from https://www.google.com/chrome/." % (CODES["NF-06"], html_path))
    out("Run log: %s" % log_path)
    out("")
    out("SUMMARY (names and companies from the roster; relay this to the operator)")
    contacts = {c["id"]: c for c in packet["contacts"]}
    for i, p in enumerate(brief["top_picks"], start=1):
        c = contacts[p["id"]]
        out("%d. %s (%s): %s" % (i, c["name"], c["company"], p["move"]["action"]))
    for p in brief.get("dormant") or []:
        c = contacts[p["id"]]
        out("Dormant: %s (%s, %s days): %s" % (c["name"], c["company"], c["days_since"], p["move"]["action"]))
    for h in brief.get("holding") or []:
        out("Holding: %s (%s): %s" % (contacts[h["id"]]["name"], contacts[h["id"]]["company"], h["reason"]))
    for q in brief.get("ceo_questions") or []:
        out("Question for the CEO: %s" % question_text(q))
    for u in brief.get("uncovered_goals") or []:
        if isinstance(u, dict):
            out("Goal with no one on file: %s: %s" % (u.get("goal"), u.get("reason")))
    for u in as_list(audit_unresolved):
        out("NF-08 needs a human check: %s" % u)
    if packet["freshness"]["status"] == "unconfirmed":
        out("Freshness: %s" % packet["freshness"]["message"])
    return EXIT_OK if browser else EXIT_BLOCKED
