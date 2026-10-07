#!/usr/bin/env python3
"""Network Focus command line. One entry point so the skill pre-approves one command.

    nf.py doctor   [--folder DIR] [--quick]
    nf.py init     [--folder DIR]
    nf.py prepare  [--folder DIR] [--roster FILE] [--as-of YYYY-MM-DD] [--use-as-is] [--today YYYY-MM-DD]
    nf.py validate --run DIR
    nf.py render   --run DIR

Exit codes: 0 ok, 1 fact check failed (send errors back), 2 blocked (NF code), 4 too long for one page.
"""

import argparse
import datetime as dt
import json
import os
import shutil
import sys

if sys.version_info < (3, 9):
    sys.stdout.write("NF-09 Python is missing or too old\n  What happened: this is Python %d.%d; Network Focus needs 3.9 or newer.\n"
                     "  What to do: install Python 3 from https://www.python.org/downloads/ and run again.\n"
                     % sys.version_info[:2])
    sys.exit(2)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
PLUGIN_ROOT = os.path.dirname(HERE)

from nf_errors import NFError, EXIT_OK, EXIT_BLOCKED  # noqa: E402
import nf_read  # noqa: E402
import nf_checks  # noqa: E402


def out(text=""):
    sys.stdout.write(text + "\n")


def _utf8_stdout():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def parse_day(text):
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        raise NFError("NF-04", "'%s' is not a date I can read." % text, "Write the date as YYYY-MM-DD, for example as-of=2026-10-05.")


def json_dump(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, default=str)
        f.write("\n")


# ---------------------------------------------------------------- commands

def cmd_init(args):
    folder = os.path.abspath(args.folder)
    goals = os.path.join(folder, "goals.md")
    if os.path.exists(goals):
        out("goals.md already exists: %s" % goals)
        return EXIT_OK
    shutil.copyfile(os.path.join(PLUGIN_ROOT, "templates", "goals.md"), goals)
    out("CREATED %s" % goals)
    out("It is a blank template. Fill in the CEO's goals before running the brief.")
    return EXIT_OK


def archive_earlier_run(week_dir):
    """Move a same-week earlier run (PDF, HTML, run log, run files) into earlier-runs/<time>/. Nothing is deleted."""
    names = [n for n in os.listdir(week_dir) if n != "earlier-runs"] if os.path.isdir(week_dir) else []
    if not names:
        return None
    dest = os.path.join(week_dir, "earlier-runs", dt.datetime.now().strftime("%Y-%m-%d_%H%M%S"))
    os.makedirs(dest, exist_ok=True)
    for n in names:
        shutil.move(os.path.join(week_dir, n), os.path.join(dest, n))
    return dest


def cmd_prepare(args):
    folder = os.path.abspath(args.folder)
    today = parse_day(args.today) if args.today else dt.date.today()
    as_of = parse_day(args.as_of) if args.as_of else None
    goals_doc = nf_read.load_goals(os.path.join(folder, "goals.md"))
    roster_path = nf_read.find_roster(folder, args.roster)
    roster = nf_read.load_roster(roster_path)
    packet = nf_checks.build_packet(roster, goals_doc, today, as_of=as_of, use_as_is=args.use_as_is)

    week_dir = os.path.join(folder, "briefs", packet["week_of"])
    run_dir = os.path.join(week_dir, "run-files")
    archive_earlier_run(week_dir)  # an earlier PDF must never look like this run's result if this run fails
    os.makedirs(run_dir, exist_ok=True)
    packet["run_dir"] = run_dir
    json_dump(packet, os.path.join(run_dir, "packet.json"))

    out("PREPARED %s" % run_dir)
    out("Roster: %s (%d contacts%s)" % (roster["file"], len(packet["contacts"]),
                                       ", sheet '%s'" % roster["sheet"] if roster["sheet"] else ""))
    out("Week of: %s" % packet["week_of"])
    out("Data: %s" % packet["freshness"]["message"])
    infos = [c for c in packet["checks"] if c["severity"] == "info" and c["kind"] != "freshness"]
    groups = packet["question_groups"]
    if groups:
        out("Needs the CEO's input (most important first; the brief must ask the first %d):"
            % min(len(groups), packet["rules"]["ceo_questions_max"]))
        for g in groups:
            out("  - [%s] %s %s" % (g["id"], " ".join(g["messages"]), g["ask"]))
    if infos:
        out("Noted:")
        for c in infos:
            out("  - %s" % c["message"])
    return EXIT_OK


def cmd_doctor(args):
    from nf_render import find_browser
    folder = os.path.abspath(args.folder)
    problems = 0

    def line(ok, what, fix=None):
        nonlocal problems
        out("%s  %s" % ("OK     " if ok else "PROBLEM", what))
        if not ok:
            problems += 1
            if fix:
                out("         What to do: %s" % fix)

    line(True, "Python %d.%d.%d at %s" % (sys.version_info[:3] + (sys.executable,)))
    line(os.path.isfile(os.path.join(PLUGIN_ROOT, "templates", "brief.html")), "Plugin files present at %s" % PLUGIN_ROOT,
         "Reinstall the plugin: claude plugin install network-focus@network-focus")
    browser = find_browser()
    if browser:
        line(True, "PDF maker: %s" % browser)
    else:
        # Not a blocker: the brief is still made as a web page the operator can print (NF-06 at the end).
        out("WARNING  NF-06: no Chrome or Edge found, so the brief will be a web page to print instead of a PDF.")
        out("         What to do (for a PDF): install Google Chrome from https://www.google.com/chrome/")
    if args.quick:
        out("")
        out("RESULT: %s" % (("ready" + ("" if browser else " (web page instead of PDF)")) if not problems
                           else "%d problem(s) to fix first" % problems))
        return EXIT_OK if not problems else EXIT_BLOCKED

    out("Folder: %s" % folder)
    try:
        g = nf_read.load_goals(os.path.join(folder, "goals.md"))
        line(True, "goals.md has %d goal(s): %s" % (len(g["goals"]), "; ".join(x["title"] for x in g["goals"])))
    except NFError as e:
        line(False, "%s %s" % (e.code, e.message), e.fix)
    try:
        path = nf_read.find_roster(folder, args.roster)
        roster = nf_read.load_roster(path)
        line(True, "Roster %s: %d rows, columns OK" % (roster["file"], len(roster["records"])))
        if roster["columns_missing"]:
            out("         Note: optional columns missing: %s" % ", ".join(nf_read.PRETTY[c] for c in roster["columns_missing"]))
    except NFError as e:
        line(False, "%s %s" % (e.code, e.message), e.fix)
    briefs = os.path.join(folder, "briefs")
    if os.path.isdir(briefs):
        weeks = sorted(d for d in os.listdir(briefs) if os.path.isdir(os.path.join(briefs, d)))
        if weeks:
            log = os.path.join(briefs, weeks[-1], "run-log.md")
            out("Last run: %s%s" % (weeks[-1], " (run log: %s)" % log if os.path.isfile(log) else " (no run log: it did not finish)"))
    out("")
    out("RESULT: %s" % ("ready to run /network-focus:brief" if not problems else "%d problem(s) to fix first" % problems))
    return EXIT_OK if not problems else EXIT_BLOCKED


def cmd_validate(args):
    import nf_validate
    return nf_validate.main(os.path.abspath(args.run), out)


def cmd_render(args):
    import nf_render
    return nf_render.main(os.path.abspath(args.run), out, audit_unresolved=args.unresolved)


def main(argv=None):
    _utf8_stdout()
    p = argparse.ArgumentParser(prog="nf.py", description="Network Focus brief tools")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("doctor")
    d.add_argument("--folder", default=".")
    d.add_argument("--roster")
    d.add_argument("--quick", action="store_true")
    i = sub.add_parser("init")
    i.add_argument("--folder", default=".")
    pr = sub.add_parser("prepare")
    pr.add_argument("--folder", default=".")
    pr.add_argument("--roster")
    pr.add_argument("--as-of")
    pr.add_argument("--use-as-is", action="store_true")
    pr.add_argument("--today")
    v = sub.add_parser("validate")
    v.add_argument("--run", required=True)
    r = sub.add_parser("render")
    r.add_argument("--run", required=True)
    r.add_argument("--unresolved", action="append", default=[],
                   help="a reviewer issue left unresolved after one revision; repeat for each one (adds the banner)")
    args = p.parse_args(argv)
    try:
        return {"doctor": cmd_doctor, "init": cmd_init, "prepare": cmd_prepare,
                "validate": cmd_validate, "render": cmd_render}[args.cmd](args)
    except NFError as e:
        out(e.render())
        return EXIT_BLOCKED


if __name__ == "__main__":
    sys.exit(main())
