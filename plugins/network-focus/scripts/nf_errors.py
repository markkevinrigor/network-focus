"""The NF error codes. The handoff sheet's "when it breaks" table uses these exact titles."""

CODES = {
    "NF-01": "Can't find the roster",
    "NF-02": "Can't read the roster file",
    "NF-03": "A required column is missing",
    "NF-04": "The roster is too old",
    "NF-05": "goals.md is missing or empty",
    "NF-06": "No Chrome or Edge to make the PDF",
    "NF-07": "The brief failed the fact check",
    "NF-08": "The reviewer flagged something that needs a person",
    "NF-09": "Python is missing or too old",
}

# Exit codes the skill branches on.
EXIT_OK = 0
EXIT_CHECK_FAILED = 1   # validator found errors: send them back to the strategist
EXIT_BLOCKED = 2        # NF error: stop and show the operator the message
EXIT_TOO_LONG = 4       # brief does not fit one page: ask the strategist to shorten


class NFError(Exception):
    def __init__(self, code, message, fix):
        super().__init__(message)
        self.code = code
        self.message = message
        self.fix = fix

    def render(self):
        return "%s %s\n  What happened: %s\n  What to do: %s" % (self.code, CODES.get(self.code, ""), self.message, self.fix)


# Small file helpers that always close the file (open handles lock files on Windows).
def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def read_text(path, encoding="utf-8"):
    with open(path, encoding=encoding) as f:
        return f.read()


def read_json(path, encoding="utf-8"):
    import json
    with open(path, encoding=encoding) as f:
        return json.load(f)
