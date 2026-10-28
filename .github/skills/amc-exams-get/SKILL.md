---
name: amc-exams-get
description: 'Use when asked to collect AMC exam references, refresh the AoPS exam inventory, find missing questions or solutions, or run an exam-by-exam gap analysis. Inventories source links without bulk copying copyrighted problems and solutions.'
---

# AMC exam inventory and gap analysis

Run from the repository root. This skill inventories exam and problem **links**,
not the copyrighted text or images of AMC questions and AoPS solutions.
Bulk text collection is not implemented. Do not describe a source link or diagram
as a collected question or solution.

## Procedure

1. Run a read-only inventory and gap analysis:

   ```powershell
   python .github\skills\amc-exams-get\scripts\amc_exams_get.py
   ```

2. Read `data\amc\reports\latest.md`. Check discovery status before interpreting
   counts. An inaccessible website means unknown gaps, not zero missing problems.
   The report groups findings by exam, includes existing diagram-only exams, and
   separately counts question files, solution files, and source references.

3. To store missing source references, run:

   ```powershell
   python .github\skills\amc-exams-get\scripts\amc_exams_get.py --collect-links
   ```

   Discovery follows the AoPS index, linked exam-family indexes, and each exam's
   problem links. It does not assume fixed years, sittings, or problem counts.
   Every run saves its gap analysis **before** writing source references.
   Existing problem references and supplied content are preserved; `exam.json`
   is refreshed with the discovered problem numbers. Re-running discovers newly
   posted links.

4. For a specific exam, use its exact AoPS exam title (repeat `--exam` for several):

   ```powershell
   python .github\skills\amc-exams-get\scripts\amc_exams_get.py --collect-links --exam "2022 AMC 12A Problems"
   ```

5. Summarize discovery success/failure, exams and problem references discovered,
   new references saved, and missing question/solution counts by exam. Link to the
   report. State that full question/solution content was not downloaded.

## Storage

```text
data\amc\
  exams\<family>\<year>\<sitting>\
    exam.json
    problems\<number>\
      source.json
      question.md       # Optional locally supplied material, not downloaded
      solution.md       # Optional locally supplied material, not downloaded
      figures\          # Optional problem/solution figures (e.g. question-01.png, solution-01.png)
  reports\
    latest.md
    latest.json
    runs\<UTC timestamp>\
      gap-before.json
      result.json
practice\
  figs\                 # Practice daily AMC diagram PNGs
```

Examples: `amc-12\2022\a`, `amc-10\2021\fall-b`, `aime\2024\ii`.
Nonempty `question.md` and `solution.md` files supplied by the user are counted as
local content; this is a presence check, not a mathematical correctness check.
Keep attribution and applicable permissions with any supplied material.
Existing `practice\figs` images and per-problem `figures\` files stay in place and
do not count as question/solution text.

## Access and reuse boundaries

- Source: <https://artofproblemsolving.com/wiki/index.php?title=AMC_Problems_and_Solutions>.
- AMC contest problems are copyrighted by the MAA. Do not assume AoPS solutions
  have the same license or that publicly viewable content permits bulk copying.
- Do not add a bulk text downloader without first establishing applicable reuse
  rights. User-supplied content can be organized without fetching a third-party
  text collection.
- AoPS is behind a Cloudflare check. A plain request that gets HTTP 403 is retried
  once in installed Chrome or Edge. If that page is still a security check, discovery
  stops. Do not use proxy mirrors or treat the check page as an empty exam index.
- To reuse a browser session that can already open the wiki, save only the request
  `Cookie` header in `data\amc\.browser\cookies.txt` and rerun. That file is gitignored.
- Requests are sequential and paced. HTTP 429, empty indexes, and malformed discovery
  are explicit failures.
- Exit code 1 means incomplete discovery or another operational failure; consult
  the report. Exit code 2 means invalid CLI arguments.

## Validation

```powershell
python -m unittest discover -s .github\skills\amc-exams-get\tests -v
```
