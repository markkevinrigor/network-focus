---
name: brief
description: Make this week's Network Focus brief, a one-page PDF naming the 3 to 5 people the CEO should invest in this week (why, the move, the risk of waiting), dormant relationships worth reopening, and data problems that need a human. Reads the roster spreadsheet and goals.md in the current folder. Use when the user types /network-focus:brief or asks for the weekly network brief or network focus brief.
argument-hint: "[roster file] [as-of=YYYY-MM-DD | use-as-is]"
allowed-tools:
  - Read
  - Write
  - AskUserQuestion
  - Agent(network-focus:network-strategist, network-focus:brief-auditor)
  - Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/nf.py" *)
  - Bash(python "${CLAUDE_PLUGIN_ROOT}/scripts/nf.py" *)
  - Bash(py -3 "${CLAUDE_PLUGIN_ROOT}/scripts/nf.py" *)
---

# Network Focus brief

You are running the weekly Network Focus brief for a chief of staff who does not write code. Be brief and plain with them. Do the steps in order. **Facts come from the scripts, judgment from the strategist, and you check the strategist with the script yourself.** Never edit the roster, never write brief.json or audit.json yourself, and never skip the fact check.

Arguments given: `$ARGUMENTS`
- `as-of=YYYY-MM-DD` becomes `--as-of YYYY-MM-DD` on the prepare command.
- `use-as-is` becomes `--use-as-is`.
- Anything else is the roster file name: `--roster "<name>"`.

The tool is `"${CLAUDE_PLUGIN_ROOT}/scripts/nf.py"`. Run every command exactly as shown, one command per Bash call, always with the quotes, with no `cd`, `&&`, pipes or redirects (they cause permission prompts). **Run no other commands at all**, not even to look something up: everything you need is printed by these commands or in the files you are told to Read. `PY` below means the Python command that worked in step 1.

**Finish the whole run in one go.** Every time a step says "dispatch", make a new Agent tool call with `run_in_background: false` and wait for it to return. Never use SendMessage to resume an agent, never run an agent in the background, and do not end your turn until step 8 is done or you have stopped with an NF code. (The tools this skill pre-approves only last for this one turn; a second turn would make the operator approve each command by hand.)

## 1. Check Python and the PDF maker

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/nf.py" doctor --quick`.
- If it says the command was not found, or prints anything about the Microsoft Store or "Python was not found", try `py -3 "${CLAUDE_PLUGIN_ROOT}/scripts/nf.py" doctor --quick`, then `python "${CLAUDE_PLUGIN_ROOT}/scripts/nf.py" doctor --quick`. Use the first that prints `RESULT:`.
- If none works: stop. Tell the operator: **NF-09 Python is missing or too old.** On a Mac, a box may have popped up offering to install developer tools: click Install, wait for it to finish, then run `/network-focus:brief` again. Otherwise install Python 3 from https://www.python.org/downloads/ and run it again.
- If it prints `RESULT: ... problem(s)`, show the PROBLEM lines with their "What to do" and stop.
- If it prints `RESULT: ready (web page instead of PDF)`, there is no Chrome or Edge: tell the operator in one line that the brief will come out as a web page they can print, and carry on.

## 2. Make sure goals.md is filled in

If there is no `goals.md` in the current folder, run `PY "${CLAUDE_PLUGIN_ROOT}/scripts/nf.py" init`. Then (and also whenever a later step reports NF-05):
1. Ask the operator to paste the CEO's current goals in plain words (AskUserQuestion is not needed; just ask in chat and wait).
2. Read `goals.md` (the template) and write it back filled in: one `## Goal:` section per goal with `Id:` (short, lowercase, hyphens), `Label:` (two or three words), `What:` (her words), `Look for tags:` (roster tags that signal relevance; ask the operator which tags their roster uses if unsure), `Should already know someone at:` and `Wants a first contact at:` (organisations she names). Set `Last reviewed:` to today.
3. Show the operator the goals in two or three lines and ask "Are these right?" with AskUserQuestion (Yes / Let me correct them). Continue only on Yes.

## 3. Prepare the data

Run `PY "${CLAUDE_PLUGIN_ROOT}/scripts/nf.py" prepare` plus the arguments above.
- If the output starts with an `NF-` code, show the operator that message as it is (it says what happened and what to do) and stop. Do not try to fix the roster yourself.
- Otherwise note the run folder from the `PREPARED <folder>` line. Call it RUN. Tell the operator in one line which roster and week you are using, and the data line.

## 4. Strategist writes the brief

Use the Agent tool with `subagent_type: network-focus:network-strategist`, `run_in_background: false`, and this prompt:
"Run folder: RUN. Operator folder: <current folder>. Read RUN/packet.json and <current folder>/goals.md. Write RUN/brief.json."
(Use the real absolute paths.) While it works, tell the operator it takes a minute or two.

## 5. Fact check (you run it, every time)

Run `PY "${CLAUDE_PLUGIN_ROOT}/scripts/nf.py" validate --run "RUN"`.
- `PASS`: continue. Keep any `warning:` lines for step 6.
- `FAIL` (including `TOO LONG`, which means it would not fit on one page): dispatch a new strategist (a fresh Agent call, not SendMessage) with: "Run folder: RUN. Operator folder: <current folder>. Your brief.json failed the fact check. Fix every error and write the whole file again:" followed by the full FAIL output. Then validate again. If it fails a second time, stop and tell the operator: **NF-07 The brief failed the fact check.** Nothing was printed, because an unchecked brief is worse than none. The details are in RUN/validation.json. Run it again; if it fails twice in a row, send that file to whoever supports the plugin.

## 6. Independent review

Use the Agent tool with `subagent_type: network-focus:brief-auditor`, `run_in_background: false`, and the prompt:
"Run folder: RUN. Operator folder: <current folder>. Review RUN/brief.json against RUN/packet.json and <current folder>/goals.md. Write RUN/audit.json. The fact check passed with these warnings, which need your judgment:" followed by the warning lines (or "none").
Then Read `RUN/audit.json`.
- `pass`, or no must-fix issues: continue to step 7 with no banner.
- `revise`: dispatch the strategist once with: "Run folder: RUN. Operator folder: <current folder>. A reviewer raised these must-fix issues. Make the smallest change that fixes each one, keep every pick nobody flagged, write the whole brief.json again, and end with FIXED/DISAGREE lines:" followed by the must-fix issues (problem and suggested fix). Then run the fact check again (step 5 rules, including one retry). Every issue the strategist answered with `DISAGREE`, or did not answer, is unresolved: pass **each one** to render as its own `--unresolved "<issue in one sentence>"` (repeat the flag once per issue). Never drop one.

## 7. Print it

Run `PY "${CLAUDE_PLUGIN_ROOT}/scripts/nf.py" render --run "RUN"` (add one `--unresolved "..."` per issue step 6 left open).
- `RENDERED`: done.
- `TOO LONG`: dispatch the strategist once with the message, then fact check (step 5) and render again.
- `NF-06`: no PDF maker. Show the message as it is (it says where the web-page version is and how to print it), then go on to step 8 with the SUMMARY it printed.
- `NF-07`: the brief changed after the fact check. Run step 5 again, then render.

## 8. Tell the operator

Use the `SUMMARY` block that render printed (it has the names, companies and moves from the roster). In plain words, no more than about 12 lines, and no long dashes:
- Where the PDF is (full path), and that the run log next to it explains the data checks.
- The top picks: one line each, name and the move.
- The questions that need the CEO.
- If there was a banner (NF-08), say what needs a human check.
- If the summary has a `Freshness:` line, say: "If this roster was exported earlier than today, run `/network-focus:brief as-of=YYYY-MM-DD` with the export date."
