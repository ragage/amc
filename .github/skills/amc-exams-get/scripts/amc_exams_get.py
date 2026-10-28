"""Inventory AoPS exam links; never download or persist problem/solution text."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen


ROOT_TITLE = "AMC_Problems_and_Solutions"
BASE_URL = "https://artofproblemsolving.com/wiki/index.php"
FAMILY_PATTERN = r"(?:AMC_(?:8|10|12)|AIME|USAJMO|USOJMO|USAMO|AHSME|AJHSME)"
INDEX_RE = re.compile(rf"^{FAMILY_PATTERN}(?:_Problems_and_Solutions)?$")
EXAM_RE = re.compile(
    rf"^(?P<year>\d{{4}})_(?:(?P<season>Fall|Spring)_)?"
    rf"(?P<family>{FAMILY_PATTERN})(?P<letter>[ABP])?"
    rf"(?:_(?P<roman>I|II))?(?:_Problems)?$"
)
PROBLEM_RE = re.compile(r"^(?P<exam>.+_Problems)/Problem_(?P<number>[1-9]\d*)$")
DIAGRAM_RE = re.compile(
    r"^(?P<year>\d{4})(?P<fall>F)?-(?P<family>8|10|12)"
    r"(?P<letter>[AB])?-(?P<number>[1-9]\d*)\.png$",
    re.IGNORECASE,
)


class InventoryError(Exception):
    """An incomplete or invalid inventory, never a successful empty result."""


def source_url(title: str) -> str:
    return BASE_URL + "?" + urlencode({"title": title})


class WikiLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.titles: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attributes = dict(attrs)
        href = attributes.get("href")
        if not href or "new" in (attributes.get("class") or "").split():
            return
        parsed = urlsplit(urljoin(BASE_URL, href))
        if parsed.scheme != "https" or parsed.netloc != "artofproblemsolving.com":
            return
        query = parse_qs(parsed.query)
        if any(key in query for key in ("action", "oldid", "diff", "redlink")):
            return
        if parsed.path == "/wiki/index.php":
            title = query.get("title", [""])[0]
        elif parsed.path.startswith("/wiki/index.php/"):
            title = unquote(parsed.path[len("/wiki/index.php/"):])
        else:
            return
        if title:
            self.titles.add(title.replace(" ", "_"))


def links(html: str) -> set[str]:
    parser = WikiLinks()
    parser.feed(html)
    return parser.titles


@dataclass
class Exam:
    title: str
    family: str
    year: str
    sitting: str
    problems: dict[int, str] = field(default_factory=dict)

    @classmethod
    def parse(cls, title: str) -> Exam | None:
        match = EXAM_RE.fullmatch(title)
        if not match:
            return None
        canonical = title if title.endswith("_Problems") else f"{title}_Problems"
        raw_family = match.group("family")
        family = "usajmo" if raw_family == "USOJMO" else raw_family.lower().replace("_", "-")
        parts = [
            match.group("season"),
            match.group("letter") or match.group("roman"),
        ]
        sitting = "-".join(part.lower() for part in parts if part) or "standard"
        return cls(
            canonical,
            family,
            match.group("year"),
            sitting,
        )

    def directory(self, data: Path) -> Path:
        return data / "exams" / self.family / self.year / self.sitting


CHALLENGE_MARKERS = (
    "Just a moment",
    "Performing security verification",
    "cf-challenge",
    "challenge-platform",
)
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def is_challenge(html: str) -> bool:
    return any(marker in html for marker in CHALLENGE_MARKERS)


def blocked_message(title: str) -> str:
    return (
        f"Cloudflare security check blocked {source_url(title)}. "
        "The wiki page was not returned, so discovery stopped instead of inventing links. "
        "Open that page in Chrome, copy the request Cookie header into "
        "data/amc/.browser/cookies.txt, and rerun."
    )


def load_cookie_header(repo: Path) -> str:
    path = repo / "data" / "amc" / ".browser" / "cookies.txt"
    if not path.is_file():
        return ""
    lines = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not lines:
        return ""
    header = lines[0]
    if header.lower().startswith("cookie:"):
        header = header.split(":", 1)[1].strip()
    return header


def load_wiki_index(repo: Path) -> dict[str, list[str]]:
    path = repo / "data" / "amc" / ".browser" / "wiki-index.json"
    if not path.is_file():
        return {}
    record = json.loads(path.read_text(encoding="utf-8"))
    pages = record.get("pages")
    return pages if isinstance(pages, dict) else {}


def render_index_html(targets: list[str]) -> str:
    return "".join(f'<a href="{source_url(target)}">{target}</a>' for target in targets)


class ChromeFetcher:
    """Read wiki pages in installed Chrome after a plain request is challenged."""

    def __init__(self, cookie_header: str = "") -> None:
        self.cookie_header = cookie_header
        self._playwright = None
        self._browser = None
        self._page = None

    def __call__(self, title: str) -> str:
        page = self._ensure_page()
        page.goto(source_url(title), wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_selector("#mw-content-text, #content", timeout=25000)
        except Exception:
            return page.content()
        return page.content()

    def _ensure_page(self):
        if self._page is not None:
            return self._page
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise InventoryError(
                "Playwright is not installed, so the Cloudflare check cannot be opened in Chrome."
            ) from error
        self._playwright = sync_playwright().start()
        last_error: Exception | None = None
        for channel in ("chrome", "msedge"):
            try:
                self._browser = self._playwright.chromium.launch(channel=channel, headless=False)
                break
            except Exception as error:
                last_error = error
        if self._browser is None:
            self.close()
            raise InventoryError(f"Could not open Chrome or Edge: {last_error}")
        context = self._browser.new_context()
        if self.cookie_header:
            context.set_extra_http_headers({"Cookie": self.cookie_header})
        self._page = context.new_page()
        return self._page

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None
        self._page = None


class Fetcher:
    def __init__(
        self,
        delay: float,
        cookie_header: str = "",
        browser_fetch: Callable[[str], str] | None = None,
        wiki_index: dict[str, list[str]] | None = None,
    ) -> None:
        self.delay = delay
        self.cookie_header = cookie_header
        self.browser_fetch = browser_fetch
        self.wiki_index = wiki_index or {}
        self.http_blocked = False
        self.last_request: float | None = None

    def __call__(self, title: str) -> str:
        if self.http_blocked and title in self.wiki_index:
            return render_index_html(self.wiki_index[title])
        if self.last_request is not None:
            time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        try:
            html = self._http(title)
        except InventoryError:
            if title in self.wiki_index:
                self.http_blocked = True
                return render_index_html(self.wiki_index[title])
            if self.browser_fetch is None:
                raise
            html = ""
        if html and not is_challenge(html):
            return html
        if title in self.wiki_index:
            self.http_blocked = True
            return render_index_html(self.wiki_index[title])
        if self.browser_fetch is None:
            raise InventoryError(blocked_message(title))
        browsed = self.browser_fetch(title)
        if is_challenge(browsed):
            raise InventoryError(blocked_message(title))
        return browsed

    def _http(self, title: str) -> str:
        headers = {"User-Agent": BROWSER_USER_AGENT}
        if self.cookie_header:
            headers["Cookie"] = self.cookie_header
        request = Request(source_url(title), headers=headers)
        try:
            with urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8")
        except HTTPError as error:
            body = ""
            try:
                body = error.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            if body and not is_challenge(body) and self.browser_fetch is None:
                raise InventoryError(
                    f"HTTP {error.code} fetching {source_url(title)}. "
                    "Discovery incomplete; no access restrictions were bypassed."
                ) from error
            if self.browser_fetch is not None and (error.code in (403, 429, 503) or is_challenge(body)):
                raise InventoryError(
                    f"HTTP {error.code} fetching {source_url(title)}."
                ) from error
            raise InventoryError(
                f"HTTP {error.code} fetching {source_url(title)}. "
                "Discovery incomplete; no access restrictions were bypassed."
            ) from error
        except (URLError, TimeoutError, UnicodeError) as error:
            raise InventoryError(f"Unable to read {source_url(title)}: {error}") from error


def discover(fetch: Callable[[str], str], selected: set[str]) -> list[Exam]:
    pending = {ROOT_TITLE}
    visited: set[str] = set()
    exams: dict[str, Exam] = {}
    while pending:
        title = min(pending)
        pending.remove(title)
        visited.add(title)
        page_links = links(fetch(title))
        relevant = {
            candidate for candidate in page_links
            if INDEX_RE.fullmatch(candidate) or EXAM_RE.fullmatch(candidate)
        }
        if not relevant:
            raise InventoryError(f"No exam/index links found on {source_url(title)}.")
        for candidate in relevant:
            exam = Exam.parse(candidate)
            if exam is not None:
                if not selected or exam.title in selected:
                    exams[exam.title] = exam
            elif candidate not in visited:
                pending.add(candidate)
    if selected - exams.keys():
        raise InventoryError("Exam(s) not found: " + ", ".join(sorted(selected - exams.keys())))
    if not exams:
        raise InventoryError("No exams discovered; refusing an empty inventory.")
    for exam in sorted(exams.values(), key=lambda item: item.title):
        for candidate in links(fetch(exam.title)):
            match = PROBLEM_RE.fullmatch(candidate)
            if match and match.group("exam") == exam.title:
                exam.problems[int(match.group("number"))] = source_url(candidate)
        if not exam.problems:
            raise InventoryError(f"No problem links found on {source_url(exam.title)}.")
    return sorted(exams.values(), key=lambda item: (item.family, item.year, item.sitting))


def local_exams(repo: Path, data: Path) -> tuple[dict[str, Exam], dict[str, list[int]]]:
    exams: dict[str, Exam] = {}
    diagrams: dict[str, list[int]] = {}
    for metadata in sorted((data / "exams").glob("*/*/*/exam.json")):
        record = json.loads(metadata.read_text(encoding="utf-8"))
        exam = Exam.parse(record["title"])
        if exam is None or exam.directory(data) != metadata.parent:
            raise InventoryError(f"Invalid exam metadata: {metadata}")
        exams[exam.title] = exam
    diagram_dirs = (repo / "practice" / "figs", repo / "practice", repo / "figs")
    seen_images: set[str] = set()
    for diagram_dir in diagram_dirs:
        for image in sorted(diagram_dir.glob("*.png")):
            if image.name in seen_images:
                continue
            seen_images.add(image.name)
            match = DIAGRAM_RE.fullmatch(image.name)
            if not match:
                continue
            season = "Fall_" if match.group("fall") else ""
            letter = (match.group("letter") or "").upper()
            title = f"{match.group('year')}_{season}AMC_{match.group('family')}{letter}_Problems"
            exam = Exam.parse(title)
            if exam is not None:
                exams.setdefault(title, exam)
                diagrams.setdefault(title, []).append(int(match.group("number")))
    return exams, diagrams


def nonempty(path: Path) -> bool:
    return path.is_file() and bool(path.read_text(encoding="utf-8").strip())


def has_problem_figures(problem_dir: Path) -> bool:
    figures_dir = problem_dir / "figures"
    if not figures_dir.is_dir():
        return False
    return any(path.is_file() and path.stat().st_size > 0 for path in figures_dir.iterdir())


def gap_rows(
    repo: Path, data: Path, remote: list[Exam] | None, selected: set[str]
) -> list[dict]:
    known, diagrams = local_exams(repo, data)
    remote_map = {exam.title: exam for exam in remote or []}
    known.update(remote_map)
    rows = []
    for title, exam in sorted(
        known.items(), key=lambda item: (item[1].family, item[1].year, item[1].sitting)
    ):
        if selected and title not in selected:
            continue
        directory = exam.directory(data) / "problems"
        local_numbers = {
            int(path.name) for path in directory.glob("*")
            if path.is_dir() and re.fullmatch(r"[1-9]\d*|0[1-9]", path.name)
        }
        expected = set(exam.problems) if title in remote_map else None
        numbers = local_numbers | (expected or set())
        questions = {
            number for number in numbers
            if nonempty(directory / f"{number:02d}" / "question.md")
        }
        solutions = {
            number for number in numbers
            if nonempty(directory / f"{number:02d}" / "solution.md")
        }
        references = set()
        for number in numbers:
            path = directory / f"{number:02d}" / "source.json"
            if not path.is_file():
                continue
            record = json.loads(path.read_text(encoding="utf-8"))
            expected_url = source_url(f"{title}/Problem_{number}")
            if record.get("url") != expected_url:
                raise InventoryError(f"Invalid problem source reference: {path}")
            references.add(number)
        problem_figures = {
            number for number in numbers
            if has_problem_figures(directory / f"{number:02d}")
        }
        all_diagrams = sorted(set(diagrams.get(title, [])) | problem_figures)
        rows.append({
            "exam": title,
            "family": exam.family,
            "year": exam.year,
            "sitting": exam.sitting,
            "source": source_url(title),
            "remote_problems": sorted(expected) if expected is not None else None,
            "local_questions": sorted(questions),
            "local_solutions": sorted(solutions),
            "local_references": sorted(references),
            "local_diagrams": all_diagrams,
            "missing_questions": sorted(expected - questions) if expected is not None else None,
            "missing_solutions": sorted(expected - solutions) if expected is not None else None,
            "missing_references": sorted(expected - references) if expected is not None else None,
        })
    return rows


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def collect_links(exams: list[Exam], data: Path) -> int:
    created = 0
    for exam in exams:
        directory = exam.directory(data)
        write_json(directory / "exam.json", {
            "title": exam.title,
            "url": source_url(exam.title),
            "problem_numbers": sorted(exam.problems),
        })
        for number, url in sorted(exam.problems.items()):
            target = directory / "problems" / f"{number:02d}" / "source.json"
            if not target.exists():
                write_json(target, {"exam": exam.title, "number": number, "url": url})
                created += 1
    return created


def markdown(report: dict) -> str:
    exam_count = report["exams_discovered"]
    link_count = report["problem_links_discovered"]
    lines = [
        "# AMC exam collection report", "",
        f"- Discovery: **{report['discovery_status']}**",
        f"- Run (UTC): {report['run_id']}",
        f"- Mode: {report['mode']}",
        f"- Exams discovered: {exam_count if exam_count is not None else 'unknown'}",
        f"- Problem links discovered: {link_count if link_count is not None else 'unknown'}",
        f"- New source references saved: {report['references_created']}",
        "- Question/solution texts downloaded: **0**",
        "- Source: " + source_url(ROOT_TITLE), "",
    ]
    if report["error"]:
        lines += ["**Error:** " + report["error"], ""]
    lines += [
        "## Gap analysis before collection", "",
        "Unknown means discovery could not establish the website inventory. "
        "Diagrams and links do not count as question or solution text. "
        "Local content counts check nonempty files, not correctness.", "",
        "| Exam | Website problems | Local questions | Local solutions | "
        "Local references | Diagrams | Missing questions | Missing solutions | Missing references |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["gap_before"]:
        fields = [
            row["remote_problems"], row["local_questions"], row["local_solutions"],
            row["local_references"], row["local_diagrams"], row["missing_questions"],
            row["missing_solutions"], row["missing_references"],
        ]
        counts = ["unknown" if value is None else str(len(value)) for value in fields]
        lines.append("| " + row["exam"].replace("_", " ") + " | " + " | ".join(counts) + " |")
    if not report["gap_before"]:
        lines += ["", "No local exams found. Website coverage is unknown if discovery failed."]
    lines += [
        "", "Exact problem numbers and post-run counts are in `latest.json`. "
        "Each run retains `gap-before.json` before any reference collection.", "",
        "## Reuse boundary", "",
        "Only exam titles, problem numbers, and source links are collected. "
        "Bulk copying copyrighted AMC questions and AoPS solutions is not implemented. "
        "Locally supplied question.md and solution.md files are preserved.", "",
    ]
    return "\n".join(lines)


def run(
    repo: Path, collect: bool, selected: set[str], fetch: Callable[[str], str]
) -> dict:
    data = repo / "data" / "amc"
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_directory = data / "reports" / "runs" / run_id
    remote = None
    error = None
    try:
        remote = discover(fetch, selected)
    except InventoryError as exception:
        error = str(exception)
    before = gap_rows(repo, data, remote, selected)
    write_json(run_directory / "gap-before.json", {
        "run_id": run_id,
        "discovery_status": "failed" if error else "complete",
        "error": error,
        "exams": before,
    })
    created = collect_links(remote, data) if collect and remote is not None else 0
    report = {
        "run_id": run_id,
        "mode": "collect-links" if collect else "gap-analysis",
        "discovery_status": "failed" if error else "complete",
        "error": error,
        "exams_discovered": len(remote) if remote is not None else None,
        "problem_links_discovered": (
            sum(len(exam.problems) for exam in remote) if remote is not None else None
        ),
        "references_created": created,
        "gap_before": before,
        "gap_after": gap_rows(repo, data, remote, selected),
    }
    write_json(run_directory / "result.json", report)
    write_json(data / "reports" / "latest.json", report)
    (data / "reports" / "latest.md").write_text(markdown(report), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collect-links", action="store_true", help="Save missing source links only")
    parser.add_argument("--exam", action="append", default=[], help="Exact AoPS exam title; repeatable")
    parser.add_argument("--delay", type=float, default=2.0, help="Seconds between requests (minimum 1)")
    args = parser.parse_args()
    if not 1 <= args.delay <= 60:
        parser.error("--delay must be between 1 and 60 seconds")
    parsed_selected = [Exam.parse(title.replace(" ", "_")) for title in args.exam]
    if any(exam is None for exam in parsed_selected):
        parser.error("--exam must be an exam title such as '2022 AMC 12A Problems'")
    selected = {exam.title for exam in parsed_selected if exam is not None}
    repo = Path(__file__).resolve().parents[4]
    browser = ChromeFetcher(load_cookie_header(repo))
    fetcher = Fetcher(
        args.delay,
        browser.cookie_header,
        browser_fetch=browser,
        wiki_index=load_wiki_index(repo),
    )
    try:
        report = run(repo, args.collect_links, selected, fetcher)
    except (InventoryError, OSError, ValueError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    finally:
        browser.close()
    print(markdown(report))
    return 1 if report["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
