# Changelog

## 0.1.2 (2026-10-07)

Found in a live rehearsal on real data: the strategist spent one of its three CEO questions on data freshness, which the page header already shows, and a goal/roster contradiction never reached the page. It also called a contact her "first" at an organisation the goals say she has none at, and described the days since last contact as the age of an offer.

- **Questions for the CEO are chosen in code.** `prepare` bundles the checks that need a human into question groups, goal-level problems first (an organisation the goals rely on with nobody on file, then goals that contradict the roster), then ambiguous names, duplicates and stale goals. Each `ceo_questions` entry now says which groups it `covers`, and the fact check requires the top three groups to be asked. A question that never names its problem gets a warning for the reviewer.
- **Freshness is never a CEO question.** It stays in the header and in the summary to the operator.
- **Strategist and reviewer rules:** never settle a flagged contradiction in prose; `days_since` is time since last contact, not the age of an offer; the reviewer treats both, and an unfaithful question, as must-fix.
- **New page design:** a goal strip (who serves each goal this week), strength as dots, a checkbox on every move, a "days quiet" badge on dormant contacts, numbered questions, and a footer with the plugin version, the brief fingerprint and the reviewer's verdict. The header no longer claims every number is checked; it says what is.
- **Layout ladder:** 10pt, 9.5pt, 9pt, then 9pt compact (tighter spacing, no goal strip). The fact check's one-page test reserves room for the review banner and the longest footer, so a brief that passes the check still fits after review.
- 69 unit tests (was 51), including the rehearsal case and a one-page test with four goals and long job titles.
