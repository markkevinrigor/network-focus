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


def print_pdf(browser, html_path, pdf_path):
    profile = tempfile.mkdtemp(prefix="nf-chrome-")
    try:
        if os.path.exists(pdf_path):
            os.remove(pdf_path)
        args = [browser, "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                "--disable-extensions", "--no-pdf-header-footer", "--print-to-pdf-no-header",
                "--user-data-dir=" + profile, "--print-to-pdf=" + pdf_path, Path(html_path).resolve().as_uri()]
        subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
    except subprocess.TimeoutExpired:
        return False
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    return os.path.isfile(pdf_path) and os.path.getsize(pdf_path) > 1000


def fmt_day(iso):
    d = dt.date.fromisoformat(iso)
    return "%d %s %d" % (d.day, d.strftime("%b"), d.year)


def chips(goal_ids, goals):
    out = []
    for g in goal_ids:
        idx = [x["id"] for x in goals].index(g) if g in [x["id"] for x in goals] else 0
        goal = next((x for x in goals if x["id"] == g), {"title": g})
        out.append('<span class="chip g%d">%s</span>' % (idx % 5, esc(short_goal(goal))))
    return " ".join(out)


def short_goal(goal):
    t = goal.get("label") or goal["title"]
    return t if len(t) <= 24 else t[:22].rstrip() + "..."


def facts(c, channel=True):
    bits = []
    if c.get("strength") is not None:
        bits.append("strength %d/5" % c["strength"])
    if c.get("days_since") is not None:
        bits.append("%d days" % c["days_since"])
    if channel and c.get("channel"):
        bits.append("last: %s" % c["channel"])
    return " &middot; ".join(esc(b) for b in bits)


def role_line(c):
    return ", ".join(x for x in (c.get("role"), c.get("company")) if x)


def as_list(unresolved):
    if not unresolved:
        return []
    return [unresolved] if isinstance(unresolved, str) else [u for u in unresolved if u]


def build_html(brief, packet, validation, font_pt, unresolved):
    unresolved = as_list(unresolved)
    contacts = {c["id"]: c for c in packet["contacts"]}
    goals = packet["goals"]
    tpl = string.Template(read_text(os.path.join(PLUGIN_ROOT, "templates", "brief.html")))

    picks = []
    for i, p in enumerate(brief["top_picks"], start=1):
        c = contacts[p["id"]]
        picks.append(
            '<li class="pick"><div class="who"><span class="num">%d</span><span class="name">%s</span>'
            '<span class="role">%s</span>%s<span class="facts">%s</span></div>'
            '<div class="grid"><div>%s</div>'
            '<div><span class="mtype">%s</span>%s</div>'
            '<div>%s</div></div></li>'
            % (i, esc(c["name"]), esc(role_line(c)), chips(p["goals"], goals), facts(c), esc(p["why"]),
               esc(p["move"]["type"].replace("-", " ")), esc(p["move"]["action"]), esc(p["risk"])))
    dormant = []
    for p in brief["dormant"]:
        c = contacts[p["id"]]
        dormant.append(
            '<tr><td class="d-who"><span class="name">%s</span><br><span class="role">%s</span><br>%s <span class="facts">%s</span></td>'
            '<td class="d-why">%s</td><td><span class="mtype">%s</span>%s</td></tr>'
            % (esc(c["name"]), esc(role_line(c)), chips(p["goals"], goals), facts(c, channel=False), esc(p["why"]),
               esc(p["move"]["type"].replace("-", " ")), esc(p["move"]["action"])))
    if not dormant:
        dormant.append('<tr><td colspan="3">No one at 60+ days is worth reopening this week.</td></tr>')
    labels = {g["id"]: g.get("label") or g["title"] for g in goals}
    uncovered = ["<li>No one on file can serve the %s goal: %s</li>" % (esc(labels.get(u.get("goal"), u.get("goal"))), esc(u.get("reason")))
                 for u in brief.get("uncovered_goals") or [] if isinstance(u, dict)]
    questions = "".join(uncovered + ["<li>%s</li>" % esc(q) for q in brief.get("ceo_questions") or []]) or "<li>Nothing this week.</li>"
    holding = "".join("<li><b>%s</b> (%s): %s</li>" % (esc(contacts[h["id"]]["name"]), esc(contacts[h["id"]]["company"]), esc(h["reason"]))
                      for h in brief.get("holding") or []) or "<li>No one.</li>"

    fr = packet["freshness"]
    fresh_cls = ' class="caveat"' if fr["status"] in ("unconfirmed",) else ""
    meta = ("Prepared %s from <b>%s</b> (%d contacts); every name, number and claim checked against it (check %s, see run-log.md). "
            "<span%s>%s</span>") % (
        esc(fmt_day(packet["today"])), esc(packet["roster"]["file"]), packet["roster"]["rows"],
        esc((validation.get("sha256") or "")[:12]), fresh_cls, esc(fr["message"]))
    banner = ""
    if unresolved:
        banner = '<div class="banner">Needs a human check (%s): %s</div>' % ("NF-08", "; ".join(esc(u) for u in unresolved))
    company = packet.get("company")
    return tpl.substitute(
        week_label=esc(fmt_day(packet["week_of"])), font_pt=("%.2f" % font_pt).rstrip("0").rstrip("."),
        company_suffix=(" &middot; " + esc(company) + " CEO") if company else "",
        meta_html=meta, banner_html=banner, headline=esc(brief["headline"]), picks_html="\n".join(picks),
        dormant_html="\n".join(dormant), questions_html=questions, holding_html=holding)


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
            f.write(build_html(brief, packet, {"sha256": "0" * 12}, 9.0, ""))
        if not print_pdf(browser, html_path, pdf_path):
            return None
        return count_pages(pdf_path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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
    for font_pt in (9.5, 9.0):  # one tighten step at most; 9pt is the floor
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(build_html(brief, packet, validation, font_pt, audit_unresolved))
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
        out("Question for the CEO: %s" % q)
    for u in brief.get("uncovered_goals") or []:
        if isinstance(u, dict):
            out("Goal with no one on file: %s: %s" % (u.get("goal"), u.get("reason")))
    for u in as_list(audit_unresolved):
        out("NF-08 needs a human check: %s" % u)
    if packet["freshness"]["status"] == "unconfirmed":
        out("Freshness: %s" % packet["freshness"]["message"])
    return EXIT_OK if browser else EXIT_BLOCKED
