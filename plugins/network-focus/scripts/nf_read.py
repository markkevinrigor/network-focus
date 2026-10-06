"""Read the roster (.xlsx or .csv) and goals.md using only the Python standard library.

Nothing here calls a model. Every value the brief prints comes through this file.
Compatible with Python 3.9 (the version macOS ships with its Command Line Tools).
"""

import csv
import datetime as dt
import io
import os
import re
import zipfile
import xml.etree.ElementTree as ET

from nf_errors import NFError, read_bytes, read_text

NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "dcterms": "http://purl.org/dc/terms/",
}

# Canonical column -> accepted header spellings, compared after normalising
# (lower case, letters and digits only). "Relationship Strength (1-5)" -> "relationshipstrength15".
COLUMNS = {
    "name": ["name", "fullname", "contact", "contactname"],
    "role": ["role", "title", "jobtitle", "position"],
    "company": ["company", "organization", "organisation", "org", "firm", "employer"],
    "how_known": ["howsheknowsthem", "howsheknows", "howknown", "howweknowthem", "relationship", "relationshiptype", "source"],
    "days_since": ["dayssincelastcontact", "dayssincecontact", "dayssince", "lastcontactdays", "dayssincelasttouch"],
    "last_contact_date": ["lastcontactdate", "lastcontacted", "datelastcontacted", "lastcontacton"],
    "channel": ["lastcontactchannel", "channel", "contactchannel"],
    "summary": ["lastcontactsummary", "summary", "lastinteraction", "lastinteractionsummary"],
    "strength": ["relationshipstrength15", "relationshipstrength", "strength", "relationshipscore"],
    "tags": ["tags", "labels", "tag"],
    "notes": ["notes", "note", "comments", "comment"],
}
REQUIRED = ["name"]  # plus one of days_since / last_contact_date, checked below
EXPECTED = ["role", "company", "how_known", "channel", "summary", "strength", "tags", "notes"]
PRETTY = {
    "name": "Name", "role": "Role", "company": "Company", "how_known": "How She Knows Them",
    "days_since": "Days Since Last Contact", "last_contact_date": "Last Contact Date",
    "channel": "Last Contact Channel", "summary": "Last Contact Summary",
    "strength": "Relationship Strength (1-5)", "tags": "Tags", "notes": "Notes",
}


def norm_header(text):
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


# ---------------------------------------------------------------- finding the file

ROSTER_EXTS = (".xlsx", ".csv")


def find_roster(folder, explicit=None):
    """Return the roster path. Explicit name wins; otherwise the newest .xlsx/.csv in the folder."""
    if explicit:
        path = explicit if os.path.isabs(explicit) else os.path.join(folder, explicit)
        if os.path.isdir(path):
            inner = _spreadsheets_in(path)
            if len(inner) == 1:
                return inner[0]
        if not os.path.isfile(path):
            raise NFError("NF-01", "I can't find the roster file '%s' in %s." % (explicit, folder),
                          "Check the spelling, or leave the file name off and I'll use the newest spreadsheet in the folder.")
        return _check_ext(path)

    candidates = _spreadsheets_in(folder)
    # A download sometimes arrives as a folder named "<something>.xlsx" with the real file inside.
    for entry in os.listdir(folder):
        p = os.path.join(folder, entry)
        if os.path.isdir(p) and entry.lower().endswith(ROSTER_EXTS):
            candidates.extend(_spreadsheets_in(p))
    others = [e for e in os.listdir(folder) if e.lower().endswith((".numbers", ".xls", ".ods"))]
    if not candidates:
        if others:
            raise NFError("NF-02", "I found '%s' but I can only read .xlsx or .csv files." % others[0],
                          "Open it and use File > Export (or Save As) to save a copy as .xlsx or .csv into this folder.")
        raise NFError("NF-01", "There is no roster spreadsheet (.xlsx or .csv) in %s." % folder,
                      "Save this week's roster export into this folder, then run the brief again.")
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[0]


def _spreadsheets_in(folder):
    out = []
    for entry in os.listdir(folder):
        p = os.path.join(folder, entry)
        if not os.path.isfile(p):
            continue
        if entry.startswith("~$") or entry.startswith("._") or entry.startswith("."):
            continue  # Excel lock files and Mac metadata files
        if entry.lower().endswith(ROSTER_EXTS):
            out.append(p)
    return out


def _check_ext(path):
    if not path.lower().endswith(ROSTER_EXTS):
        raise NFError("NF-02", "'%s' is not an .xlsx or .csv file." % os.path.basename(path),
                      "Export the roster as .xlsx or .csv and run the brief again.")
    return path


# ---------------------------------------------------------------- reading rows

def read_table(path):
    """Return (headers, rows, meta). rows are lists of strings, padded to len(headers)."""
    if path.lower().endswith(".csv"):
        headers, rows = _read_csv(path)
        meta = {"sheet": None, "saved": None}
    else:
        headers, rows, meta = _read_xlsx(path)
    width = len(headers)
    rows = [(r + [""] * width)[:width] for r in rows]
    rows = [r for r in rows if any(c.strip() for c in r)]
    return headers, rows, meta


def _read_csv(path):
    raw = read_bytes(path)
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = list(csv.reader(io.StringIO(text), dialect))
    if not reader:
        raise NFError("NF-02", "The file '%s' is empty." % os.path.basename(path),
                      "Export the roster again and check it opens in Excel or Google Sheets.")
    return [h.strip() for h in reader[0]], [[c.strip() for c in r] for r in reader[1:]]


def _col_index(ref):
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _text_of(el):
    """Join every <t> under an element (handles rich-text runs)."""
    return "".join(t.text or "" for t in el.iter("{%s}t" % NS["m"]))


def _read_xlsx(path):
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        raise NFError("NF-02", "I can't open '%s'. It may be password-protected, damaged, or not really an .xlsx file."
                      % os.path.basename(path),
                      "Open it in Excel or Google Sheets, remove any password, and save a fresh copy as .xlsx or .csv.")
    names = set(z.namelist())

    shared = []
    if "xl/sharedStrings.xml" in names:
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        shared = [_text_of(si) for si in root.findall("m:si", NS)]

    # Pick the sheet: the first one whose header row contains a Name column.
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    targets = {r.get("Id"): r.get("Target") for r in rels.findall("rel:Relationship", NS)}
    sheets = []
    for s in wb.findall("m:sheets/m:sheet", NS):
        target = targets.get(s.get("{%s}id" % NS["r"]), "")
        target = target.lstrip("/")
        if not target.startswith("xl/"):
            target = "xl/" + target
        sheets.append((s.get("name"), target))

    chosen = None
    for sheet_name, target in sheets:
        if target not in names:
            continue
        rows = _sheet_rows(ET.fromstring(z.read(target)), shared)
        if rows and any(norm_header(h) in COLUMNS["name"] for h in rows[0]):
            chosen = (sheet_name, rows)
            break
        if chosen is None and rows:
            chosen = (sheet_name, rows)
    if chosen is None:
        raise NFError("NF-02", "'%s' has no data in any sheet." % os.path.basename(path),
                      "Check you exported the roster sheet, not an empty workbook.")

    saved = None
    if "docProps/core.xml" in names:
        core = ET.fromstring(z.read("docProps/core.xml"))
        el = core.find("dcterms:modified", NS)
        if el is not None and el.text:
            saved = parse_iso_date(el.text)
    sheet_name, rows = chosen
    return [h.strip() for h in rows[0]], rows[1:], {"sheet": sheet_name, "saved": saved}


def _sheet_rows(root, shared):
    out = []
    for row in root.iter("{%s}row" % NS["m"]):
        cells = {}
        for c in row.findall("m:c", NS):
            ref = c.get("r")
            idx = _col_index(ref) if ref else len(cells)
            kind = c.get("t")
            if kind == "inlineStr":
                is_el = c.find("m:is", NS)
                val = _text_of(is_el) if is_el is not None else ""
            else:
                v = c.find("m:v", NS)
                raw = v.text if v is not None and v.text is not None else ""
                if kind == "s" and raw != "":
                    val = shared[int(raw)]
                elif kind == "b":
                    val = "TRUE" if raw == "1" else "FALSE"
                else:
                    val = raw
                    if re.fullmatch(r"-?\d+\.0+", val or ""):
                        val = val.split(".")[0]  # 16.0 -> 16
            cells[idx] = val.strip() if isinstance(val, str) else str(val)
        if cells:
            width = max(cells) + 1
            out.append([cells.get(i, "") for i in range(width)])
    return out


def parse_iso_date(text):
    """'2026-07-03T08:30:52Z' -> date(2026, 7, 3). Written by hand: fromisoformat rejects 'Z' before 3.11."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", text.strip())
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


# ---------------------------------------------------------------- mapping columns

def map_columns(headers):
    """Return ({canonical: index}, [unrecognised headers])."""
    found, unknown = {}, []
    for i, h in enumerate(headers):
        key = norm_header(h)
        hit = None
        for canon, aliases in COLUMNS.items():
            if key in aliases and canon not in found:
                hit = canon
                break
        if hit:
            found[hit] = i
        elif h.strip():
            unknown.append(h)
    return found, unknown


def load_roster(path):
    """Read and map the roster. Raises NFError for anything that stops the run."""
    headers, rows, meta = read_table(path)
    found, unknown = map_columns(headers)
    missing_required = [c for c in REQUIRED if c not in found]
    if "days_since" not in found and "last_contact_date" not in found:
        missing_required.append("days_since")
    if missing_required:
        raise NFError(
            "NF-03",
            "The roster is missing %s. I found these columns: %s."
            % (" and ".join("'%s'" % PRETTY[c] for c in missing_required), ", ".join(h for h in headers if h) or "none"),
            "Add the missing column (or rename the existing one to match) and export again. "
            "The column names I expect are listed in the handoff sheet.")
    records = []
    for n, r in enumerate(rows, start=2):  # row 1 is the header
        rec = {c: r[i] for c, i in found.items()}
        rec["_row"] = n
        records.append(rec)
    return {
        "path": path,
        "file": os.path.basename(path),
        "sheet": meta.get("sheet"),
        "saved": meta.get("saved"),
        "headers": headers,
        "columns_found": sorted(found),
        "columns_missing": [c for c in EXPECTED if c not in found],
        "columns_unknown": unknown,
        "records": records,
    }


# ---------------------------------------------------------------- goals.md

def parse_orgs(text):
    """'Birchwood Ventures (also Birchwood); Kestrel Capital; university climate groups (tag: academic)'"""
    out = []
    for part in [p.strip() for p in (text or "").split(";")]:
        if not part:
            continue
        m = re.match(r"^(.*?)\s*\((.*)\)\s*$", part)
        label, extra = (m.group(1).strip(), m.group(2)) if m else (part, "")
        aliases, tags = [label], []
        for bit in [b.strip() for b in extra.split(",") if b.strip()]:
            bit = re.sub(r"^(also|aka)\s+", "", bit, flags=re.I)
            t = re.match(r"^tag:\s*(.+)$", bit, flags=re.I)
            if t:
                tags.append(t.group(1).strip().lower())
            else:
                aliases.append(bit)
        out.append({"label": label, "aliases": aliases, "tags": tags})
    return out


def load_goals(path):
    if not os.path.isfile(path):
        raise NFError("NF-05", "There is no goals.md in this folder.",
                      "Run the brief again and I'll create one and help you fill it in.")
    text = read_text(path, "utf-8-sig")
    header = {}
    goals = []
    current = None
    for line in text.splitlines():
        m = re.match(r"^##\s+Goal\s*:?\s*(.+?)\s*$", line, flags=re.I)
        if m:
            current = {"title": m.group(1), "id": None, "label": "", "what": "", "tags": [], "known_orgs": [], "wanted_orgs": []}
            goals.append(current)
            continue
        kv = re.match(r"^\s*([A-Za-z][A-Za-z '\-]*?)\s*:\s*(.*)$", line)
        if not kv:
            continue
        key, val = kv.group(1).strip().lower(), kv.group(2).strip()
        if current is None:
            header[key] = val
        elif key == "id":
            current["id"] = re.sub(r"[^a-z0-9-]", "", val.lower())
        elif key == "what":
            current["what"] = val
        elif key == "label":
            current["label"] = val
        elif key in ("look for tags", "tags"):
            current["tags"] = [t.strip().lower() for t in val.split(",") if t.strip()]
        elif key.startswith("should already know"):
            current["known_orgs"] = parse_orgs(val)
        elif key.startswith("wants a first contact"):
            current["wanted_orgs"] = parse_orgs(val)
    goals = [g for g in goals if g["what"] or g["tags"]]
    if not goals or any("replace this" in (g["what"] or "").lower() for g in goals):
        raise NFError("NF-05", "goals.md has no goals filled in yet.",
                      "Run the brief again and paste the CEO's goals when I ask, or open goals.md and fill in each section.")
    for i, g in enumerate(goals, start=1):
        if not g["id"]:
            g["id"] = re.sub(r"[^a-z0-9]+", "-", g["title"].lower()).strip("-") or "goal-%d" % i
    reviewed = parse_iso_date(header.get("last reviewed", "") or "")
    return {"path": path, "text": text, "company": header.get("company", ""), "quarter": header.get("quarter", ""),
            "last_reviewed": reviewed, "goals": goals}
