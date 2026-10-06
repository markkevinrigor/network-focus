---
name: doctor
description: Check that Network Focus is installed and ready to run in this folder, and explain any problem in plain English with what to do. Use when the user types /network-focus:doctor, or says the Network Focus brief is broken, failed, or shows an NF error code.
allowed-tools:
  - Read
  - Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/nf.py" *)
  - Bash(python "${CLAUDE_PLUGIN_ROOT}/scripts/nf.py" *)
  - Bash(py -3 "${CLAUDE_PLUGIN_ROOT}/scripts/nf.py" *)
---

# Network Focus doctor

The person asking does not write code. Check everything, change nothing, and explain what you find in plain words.

1. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/nf.py" doctor`. If the command is not found, or mentions the Microsoft Store or "Python was not found", try `py -3 "${CLAUDE_PLUGIN_ROOT}/scripts/nf.py" doctor`, then `python "${CLAUDE_PLUGIN_ROOT}/scripts/nf.py" doctor`.
   - If none works: Python is missing (**NF-09**). On a Mac: open the Terminal app, type `python3`, press Return, and click **Install** in the box that appears; when it finishes, run `/network-focus:doctor` again. On Windows: install Python 3 from https://www.python.org/downloads/ and tick "Add python.exe to PATH" on the first screen.
2. Show each line that says PROBLEM, with its "What to do", in a short numbered list. If everything is OK, say it is ready and that the next step is `/network-focus:brief`.
3. If the output names a last run with a run log, Read that `run-log.md` and summarise in three lines or fewer: which roster it used, whether the fact check passed, and any data checks marked [ask].
4. If the user mentioned an NF code, explain it using this table, then the fix:

| Code | Meaning | What to do |
|---|---|---|
| NF-01 | Can't find the roster | Save this week's roster export (.xlsx or .csv) into this folder, or name the file after the command. |
| NF-02 | Can't read the roster file | Re-export as .xlsx or .csv, without a password. |
| NF-03 | A required column is missing | The message lists the columns found; add or rename the missing one and export again. |
| NF-04 | The roster is too old | Export a fresh roster, or run `/network-focus:brief use-as-is` to accept it. |
| NF-05 | goals.md is missing or empty | Run `/network-focus:brief` and paste the CEO's goals when asked. |
| NF-06 | No Chrome or Edge to make the PDF | Install Google Chrome from https://www.google.com/chrome/; the HTML version is still saved. |
| NF-07 | The brief failed the fact check | Run it again. If it fails twice, send the run-files folder to whoever supports the plugin. |
| NF-08 | The reviewer flagged something | The PDF has an orange banner; check that point by hand before the meeting. |
| NF-09 | Python is missing or too old | See step 1. |

Never edit, move or delete any file.
