# Network Focus

A Claude Code plugin that turns an executive's network roster and quarterly goals into a one-page **Network Focus brief** every Monday:

- **Invest this week:** the 3 to 5 people who matter most for her goals right now, why, the specific move to make this week, and what is at risk if she doesn't.
- **Dormant, still worth reopening:** 2 to 3 people she hasn't spoken to in 60+ days who could still move a goal.
- **Needs your input / Holding:** data problems a human must settle (a goal organisation nobody on file works at, a note that names someone who isn't in the roster, goals that contradict the roster), and people who asked her to wait.

A strip at the top shows the week by goal, every move has a checkbox so the page doubles as the planning-meeting agenda, and the footer carries the plugin version, the brief's fingerprint and the reviewer's verdict.

It is built to be run by a chief of staff who does not write code.

## Install (two commands)

In a terminal, with [Claude Code](https://code.claude.com/docs/en/overview) installed:

```
claude plugin marketplace add markkevinrigor/network-focus
claude plugin install network-focus@network-focus
```

To update later: `claude plugin marketplace update network-focus`, then `claude plugin update network-focus@network-focus`, then restart Claude Code.

Requirements: a Claude subscription, Python 3.9 or newer (macOS offers to install it the first time), and Google Chrome or Microsoft Edge (for the PDF). Nothing else to install: the scripts use only Python's standard library.

## Run it

1. Make a folder (for example `Documents/Network Focus`) and save the week's roster export into it (`.xlsx` or `.csv`). Putting the export date in the file name, like `roster-2026-10-12.xlsx`, lets the brief correct the "days since last contact" figures for the file's age.
2. Open Claude Code in that folder and type `/network-focus:brief`. The first time, it asks for the CEO's goals and saves them in `goals.md`.
3. The PDF, an HTML copy and a plain-English `run-log.md` appear in `briefs/<week>/`.

Troubleshooting: `/network-focus:doctor` checks everything and explains any `NF-` code in plain words.

Roster columns (other spellings are recognised): Name, Role, Company, How She Knows Them, Days Since Last Contact (or Last Contact Date), Last Contact Channel, Last Contact Summary, Relationship Strength (1-5), Tags, Notes. Only Name and the days or date column are required.

## How it works

**Code does facts, the model does judgment, code checks the model.**

```
roster.xlsx + goals.md
      |
  nf.py prepare        Python, no model: reads the sheet, gives every row an ID, matches tags to goals,
      |                flags thin context / dormant / stated timing / duplicates, finds data problems
  packet.json          (goal organisations missing from the roster, notes that name unknown people, ...)
      |                and ranks them into question groups for the CEO, most important first
      |
  network-strategist   sub-agent (Opus, read + write only): picks people and moves, cites roster words
      |
  brief.json
      |
  nf.py validate       Python: every quote is in that person's row, no invented full names, "N days ago" claims
      |                match the row, timing and weak-tie rules, goal coverage, the top 3 question groups are
      |                asked, and a real print test: it must fit one page.
      |                Failures go back to the strategist once; a second failure stops with NF-07.
  brief-auditor        sub-agent (Sonnet, fresh context): judgment review; must-fix issues get one revision
      |
  nf.py render         Python: refuses a brief or data file that differs from the validated one (sha256), prints
                       names/roles/strength/days from the row IDs, one Letter page via headless Chrome or Edge
                       (10pt, then 9.5, then 9; the last step tightens spacing and drops the goal strip)
```

| Piece | File |
|---|---|
| `/network-focus:brief` (orchestrator) | `plugins/network-focus/skills/brief/SKILL.md` |
| `/network-focus:doctor` | `plugins/network-focus/skills/doctor/SKILL.md` |
| Strategist / auditor sub-agents | `plugins/network-focus/agents/` |
| Reader, checks, validator, renderer | `plugins/network-focus/scripts/` |
| Tests (synthetic data only) | `plugins/network-focus/tests/` |
| Practice files | `plugins/network-focus/examples/` |

### Error codes

| Code | Meaning |
|---|---|
| NF-01 | Can't find the roster |
| NF-02 | Can't read the roster file |
| NF-03 | A required column is missing |
| NF-04 | The roster is too old (dated more than 30 days ago) |
| NF-05 | goals.md is missing or empty |
| NF-06 | No Chrome or Edge to make the PDF |
| NF-07 | The brief failed the fact check |
| NF-08 | The reviewer flagged something that needs a person |
| NF-09 | Python is missing or too old |

## Development

```
cd plugins/network-focus/tests
python -m unittest discover -s . -t .
```

CI runs the tests on macOS (Python 3.9) and Windows, including a real PDF render. One test prints a brief with every field at its target length, five picks, three dormant, four goals, long job titles and the review banner, and requires one page at 9pt. Fields may run up to 10% over a target with a warning; if that overflows the page, the fact check's own print test returns TOO LONG and the strategist shortens it. Changes are listed in `CHANGELOG.md`. No real network data belongs in this repository: `.gitignore` excludes spreadsheets, `goals.md` and generated briefs, and the tests use a made-up company.

## Not built (yet)

- Pulling the roster from Calendar, Gmail and a CRM (for example through MCP connectors) instead of a spreadsheet export.
- Running unattended on a schedule (`claude -p "/network-focus:brief"` from cron or launchd, or a service built on the Claude Agent SDK).

License: MIT.
