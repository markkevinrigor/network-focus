"""Synthetic test data (a made-up company and made-up people) and small file builders. No real roster data."""

import csv
import os
import sys
import zipfile
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

HEADERS = ["Name", "Role", "Company", "How She Knows Them", "Days Since Last Contact", "Last Contact Channel",
           "Last Contact Summary", "Relationship Strength (1-5)", "Tags", "Notes"]

ROWS = [
    ["Avery Stone", "Partner", "Birchwood Ventures", "investor", "12", "email",
     "Asked for the latest metrics deck after a warm call.", "4", "investor, warm",
     "Intro from Jordan at Kestrel Capital. Wants metrics before a partner meeting."],
    ["Jordan Blake", "COO", "Ferris Group", "peer", "30", "call",
     "Compared notes on hiring a sales leader.", "3", "peer",
     "Friendly and helpful with hiring advice from his own search last year."],
    ["Morgan Lee", "VP Sales", "Tidewell", "ex-colleague", "20", "meeting",
     "Coffee chat. She said 'let's revisit in a month' about the VP Sales role.", "4", "sales-candidate",
     "Scaled a sales team from 10 to 60. Strong candidate for the role."],
    ["Morgan Lee", "Founder", "Quillstone", "event", "90", "event",
     "Met at a panel on grid data.", "2", "climate",
     "Runs a climate data startup. The follow-up she promised never happened."],
    ["Riley Chen", "Researcher", "Climate Lab", "intro", "45", "email",
     "Shared a paper on grid forecasting after an intro.", "2", "climate, academic",
     "Interested in advising. Asked for a product demo when it is ready."],
    ["Sam Ortiz", "Teacher", "Lincoln High", "personal", "2", "call",
     "Weekend catch-up about family news.", "5", "personal, friend",
     "Old friend from school. No overlap with the company goals."],
    ["Casey Park", "Partner", "Granite Fund", "investor", "75", "meeting",
     "Coffee around the seed round. Nothing since.", "3", "investor, dormant",
     "Good fit for the Series B round but went quiet after the seed."],
    ["Taylor Brooks", "CEO", "Megacorp", "event", "200", "event",
     "Shook hands at a conference.", "1", "aspirational", "No real relationship."],
    ["Drew Patel", "Head of Talent", "Kestrel Capital", "intro", "15", "email",
     "Offered to share three sales leader profiles.", "3", "investor, talent",
     "Has offered candidate profiles twice and is still waiting for a reply."],
    ["Quinn Rivera", "Partner", "Lumen Partners", "investor", "57", "email",
     "Quarterly check-in note about the market.", "2", "investor",
     "Lukewarm but open to a Series B conversation later this year."],
    ["Jamie Fox", "Advisor", "Independent", "advisor", "64", "call",
     "Monthly advisor call lapsed over the summer.", "4", "advisor, climate",
     "Knows several climate scientists and offered intros last spring."],
    ["", "", "", "", "", "", "", "", "", ""],
]

GOALS_MD = """# CEO goals for the Network Focus brief

Company: Harbor Analytics
Quarter: 2026 Q4
Last reviewed: 2026-10-01

## Goal: Raise Series B
Id: series-b
Label: Series B
What: Raise $20M this half. Warm paths into Birchwood Ventures, Kestrel Capital and Harrow Capital.
Look for tags: investor
Should already know someone at: Birchwood Ventures (also Birchwood); Kestrel Capital; Harrow Capital (also Harrow)
Wants a first contact at:

## Goal: Hire a VP Sales
Id: vp-sales
Label: VP Sales
What: Hire a VP Sales who has scaled a team past 50.
Look for tags: sales-candidate, talent
Should already know someone at:
Wants a first contact at:

## Goal: Build a climate advisory board
Id: climate
Label: Climate board
What: Recruit two climate scientists as advisors. No academic contacts yet.
Look for tags: climate
Should already know someone at:
Wants a first contact at: Climate Lab; university climate groups (tag: academic)
"""


def write_csv(path, rows=None, headers=None, delimiter=",", encoding="utf-8-sig"):
    with open(path, "w", newline="", encoding=encoding) as f:
        w = csv.writer(f, delimiter=delimiter)
        w.writerow(headers or HEADERS)
        for r in (ROWS if rows is None else rows):
            w.writerow(r)
    return path


def _col(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def write_xlsx(path, rows=None, headers=None, shared=True, modified="2026-07-03T08:30:52Z", numeric_days_as_float=False,
               skip_empty_cells=True):
    """A minimal real .xlsx: shared strings (as Excel writes) or inline strings (as openpyxl writes)."""
    table = [headers or HEADERS] + (ROWS if rows is None else rows)
    strings, index = [], {}
    rows_xml = []
    for r_i, row in enumerate(table, start=1):
        cells = []
        for c_i, val in enumerate(row):
            ref = "%s%d" % (_col(c_i), r_i)
            if val == "" and skip_empty_cells:
                continue
            if r_i > 1 and c_i in (4, 7) and str(val).isdigit():
                num = "%s.0" % val if numeric_days_as_float else val
                cells.append('<c r="%s"><v>%s</v></c>' % (ref, num))
            elif shared:
                if val not in index:
                    index[val] = len(strings)
                    strings.append(val)
                cells.append('<c r="%s" t="s"><v>%d</v></c>' % (ref, index[val]))
            else:
                cells.append('<c r="%s" t="inlineStr"><is><t>%s</t></is></c>' % (ref, escape(val)))
        rows_xml.append('<row r="%d">%s</row>' % (r_i, "".join(cells)))
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    sheet = '<?xml version="1.0" encoding="UTF-8"?><worksheet %s><sheetData>%s</sheetData></worksheet>' % (ns, "".join(rows_xml))
    wb = ('<?xml version="1.0" encoding="UTF-8"?><workbook %s xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
          '<sheets><sheet name="Contacts" sheetId="1" r:id="rId1"/></sheets></workbook>' % ns)
    rels = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '</Relationships>')
    core = ('<?xml version="1.0" encoding="UTF-8"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            '<dcterms:modified xsi:type="dcterms:W3CDTF">%s</dcterms:modified></cp:coreProperties>' % modified)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
        z.writestr("docProps/core.xml", core)
        if shared:
            sst = "".join("<si><t>%s</t></si>" % escape(s) for s in strings)
            z.writestr("xl/sharedStrings.xml", '<?xml version="1.0" encoding="UTF-8"?><sst %s>%s</sst>' % (ns, sst))
    return path


def write_goals(folder, text=GOALS_MD):
    p = os.path.join(folder, "goals.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def good_brief(week_of):
    """A brief that passes every check against ROWS + GOALS_MD (ids follow row order, blank row skipped)."""
    return {
        "week_of": week_of,
        "headline": "Answer Drew's offer of sales leader profiles and send Avery the metrics deck.",
        "top_picks": [
            {"id": "C09", "goals": ["vp-sales", "series-b"],
             "why": "Head of Talent at an investor who offered three sales leader profiles and is still waiting for a reply.",
             "move": {"type": "email", "action": "Reply today accepting the profiles and share the VP Sales brief; ask for two intros by Friday."},
             "risk": "A second unanswered offer reads as not serious, and the profiles go to someone else.",
             "evidence": [{"field": "summary", "quote": "Offered to share three sales leader profiles"},
                          {"field": "notes", "quote": "still waiting for a reply"}],
             "opening_line": "Drew, thank you for your patience, and yes please to the profiles."},
            {"id": "C01", "goals": ["series-b"],
             "why": "Partner who asked for the latest metrics deck after a warm call, 12 days ago.",
             "move": {"type": "send-materials", "action": "Send the metrics deck with a short note and offer two times for the partner meeting."},
             "risk": "The request cools and the partner meeting slips past the raise window.",
             "evidence": [{"field": "summary", "quote": "Asked for the latest metrics deck"}]},
            {"id": "C03", "goals": ["vp-sales"],
             "why": "Strongest VP Sales candidate on file, who asked to revisit in a month.",
             "move": {"type": "prepare", "action": "Prepare the role scorecard now and put a reminder in two weeks to reopen the conversation."},
             "risk": "Without preparation the revisit window opens with nothing new to show her.",
             "evidence": [{"field": "notes", "quote": "Scaled a sales team from 10 to 60"}]},
            {"id": "C05", "goals": ["climate"],
             "why": "Researcher at Climate Lab who is interested in advising and asked for a product demo.",
             "move": {"type": "email", "action": "Offer a 30-minute demo next week and ask what an advisory role would need to look like for her."},
             "risk": "Interest in advising fades and the board stays without an academic voice.",
             "evidence": [{"field": "notes", "quote": "Interested in advising"}]},
        ],
        "dormant": [
            {"id": "C07", "goals": ["series-b"], "why": "Good Series B fit who went quiet after the seed, 75 days ago.",
             "move": {"type": "email", "action": "Short traction update and an early look at the Series B."},
             "evidence": [{"field": "notes", "quote": "Good fit for the Series B round"}]},
            {"id": "C11", "goals": ["climate"], "why": "Advisor who knows several climate scientists and offered intros.",
             "move": {"type": "call", "action": "Restart the monthly call and take up the intro offer."},
             "evidence": [{"field": "notes", "quote": "offered intros last spring"}]},
        ],
        "holding": [],
        "ceo_questions": ["Who is your Harrow Capital contact? Nobody from there is in the roster."],
        "uncovered_goals": [],
    }
