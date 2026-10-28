# AMC 12 daily problem diagrams

Diagram images for Eliott's daily AMC 12 practice emails, redrawn in Asymptote.
One file per problem: `practice/figs/{year}-12{A|B}-{number}.png` (Fall 2021: `2021F-12A-{n}`).
Problems are past AMC 12 contests (© MAA); the original wording and figures are on the AoPS Wiki.

Diagram PNGs in `practice/figs` are written automatically by the `amc12-exam-questions`
routine.

## Exam inventory

The repository skill
[amc-exams-get](.github/skills/amc-exams-get/SKILL.md) discovers AoPS exam and
problem links and produces an exam-by-exam gap analysis before collecting links:

```powershell
python .github\skills\amc-exams-get\scripts\amc_exams_get.py
python .github\skills\amc-exams-get\scripts\amc_exams_get.py --collect-links
```

Use `/amc-exams-get` in a skill-aware assistant, or run the commands directly.
Repeat `--exam "2022 AMC 12A Problems"` to limit either command to specific exams.
Reports are in `data\amc\reports\latest.md` and `latest.json`, with per-run
pre-collection snapshots in `reports\runs`. Source references are stored under
`data\amc\exams\<family>\<year>\<sitting>\problems\<number>\source.json`, with
optional per-problem figures in `problems\<number>\figures\`.
Requests are sequential, with a two-second interval by default. If AoPS returns a
Cloudflare check, the script retries that page in installed Chrome or Edge. A browser
session that already opens the wiki can be reused by saving its request `Cookie`
header in `data\amc\.browser\cookies.txt` (gitignored).

**This is a link inventory, not a full-text archive.** AMC problems are copyrighted
by the MAA; no bulk reuse permission for questions or AoPS solutions is assumed.
The tool does not copy their text or images. User-supplied nonempty `question.md`
and `solution.md` files in each problem folder are counted separately from links
and diagrams, without modifying them. A failed website request produces an
explicit incomplete report with unknown website gaps and exit code 1, not a
successful empty collection.
