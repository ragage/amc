import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "amc_exams_get.py"
SPEC = importlib.util.spec_from_file_location("amc_exams_get", SCRIPT)
amc = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = amc
SPEC.loader.exec_module(amc)

EXAM = "2022_AMC_12A_Problems"
OTHER = "2021_Fall_AMC_10B_Problems"


def page(*titles):
    return "".join(f'<a href="{amc.source_url(title)}">{title}</a>' for title in titles)


def website():
    return {
        amc.ROOT_TITLE: page("AMC_12", "AMC_10_Problems_and_Solutions"),
        "AMC_12": page(EXAM),
        "AMC_10_Problems_and_Solutions": page(OTHER),
        EXAM: page(f"{EXAM}/Problem_1", f"{EXAM}/Problem_3", f"{OTHER}/Problem_2"),
        OTHER: page(f"{OTHER}/Problem_25"),
    }


class DiscoveryTests(unittest.TestCase):
    def test_only_canonical_same_site_page_links(self):
        html = """
        <a href="/wiki/index.php?title=2022_AMC_12A_Problems">exam</a>
        <a href="/wiki/index.php/2024_AIME_II_Problems">path</a>
        <a href="https://example.com/wiki/index.php?title=2023_AMC_8_Problems">external</a>
        <a href="http://artofproblemsolving.com/wiki/index.php?title=2023_AMC_8_Problems">http</a>
        <a class="new" href="?title=2024_AMC_8_Problems">red</a>
        <a href="?title=2024_AMC_8_Problems&amp;action=edit">edit</a>
        <a href="?title=2024_AMC_8_Problems&amp;redlink=1">red query</a>
        <a href="?title=2024_AMC_8_Problems&amp;oldid=123">old</a>
        """
        self.assertEqual(amc.links(html), {EXAM, "2024_AIME_II_Problems"})

    def test_discovery_uses_actual_numbers_not_fixed_counts(self):
        pages = website()
        requests = []

        def fetch(title):
            requests.append(title)
            return pages[title]

        exams = amc.discover(fetch, set())
        self.assertEqual(len(exams), 2)
        exam = next(exam for exam in exams if exam.title == EXAM)
        self.assertEqual(set(exam.problems), {1, 3})
        self.assertFalse(any("/Problem_" in title for title in requests))

    def test_exam_filter_skips_unselected_exam_fetch(self):
        pages = website()
        del pages[OTHER]
        self.assertEqual([exam.title for exam in amc.discover(pages.__getitem__, {EXAM})], [EXAM])

    def test_unknown_exam_is_failure(self):
        with self.assertRaisesRegex(amc.InventoryError, "not found"):
            amc.discover(website().__getitem__, {"1900_AMC_12A_Problems"})

    def test_empty_root_and_empty_exam_are_failures(self):
        with self.assertRaisesRegex(amc.InventoryError, "No exam/index links"):
            amc.discover(lambda title: "<html>challenge</html>", set())
        pages = website()
        pages[EXAM] = "<html>No problems posted</html>"
        with self.assertRaisesRegex(amc.InventoryError, "No problem links"):
            amc.discover(pages.__getitem__, {EXAM})

    def test_cyclic_index_links_are_fetched_once(self):
        pages = website()
        pages["AMC_12"] += page("AMC_10_Problems_and_Solutions")
        pages["AMC_10_Problems_and_Solutions"] += page("AMC_12")
        requests = []

        def fetch(title):
            requests.append(title)
            return pages[title]

        amc.discover(fetch, set())
        self.assertEqual(requests.count("AMC_12"), 1)

    def test_historical_and_seasonal_folder_names(self):
        cases = {
            EXAM: ("amc-12", "2022", "a"),
            "2022_AMC_12A": ("amc-12", "2022", "a"),
            "2002_AMC_12P": ("amc-12", "2002", "p"),
            OTHER: ("amc-10", "2021", "fall-b"),
            "2024_AIME_II_Problems": ("aime", "2024", "ii"),
            "1990_AHSME_Problems": ("ahsme", "1990", "standard"),
            "1985_AJHSME_Problems": ("ajhsme", "1985", "standard"),
            "2024_USAMO_Problems": ("usamo", "2024", "standard"),
            "2024_USAJMO_Problems": ("usajmo", "2024", "standard"),
            "2020_USOJMO": ("usajmo", "2020", "standard"),
        }
        for title, expected in cases.items():
            with self.subTest(title=title):
                exam = amc.Exam.parse(title)
                self.assertEqual((exam.family, exam.year, exam.sitting), expected)
                self.assertTrue(exam.title.endswith("_Problems"))


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name)
        self.data = self.repo / "data" / "amc"
        self.exam = amc.Exam.parse(EXAM)
        self.problems = self.exam.directory(self.data) / "problems"

    def run_inventory(self, collect=False, pages=None):
        return amc.run(
            self.repo, collect, {EXAM},
            (pages if pages is not None else website()).__getitem__,
        )

    def test_scan_creates_reports_not_exam_references(self):
        report = self.run_inventory()
        self.assertEqual(report["discovery_status"], "complete")
        self.assertEqual(report["problem_links_discovered"], 2)
        self.assertEqual(report["references_created"], 0)
        self.assertFalse((self.data / "exams").exists())
        self.assertEqual(report["gap_before"][0]["missing_questions"], [1, 3])
        self.assertTrue((self.data / "reports" / "latest.md").is_file())

    def test_gap_is_persisted_before_collection(self):
        original = amc.collect_links

        def checked_collect(exams, data):
            snapshots = list((data / "reports" / "runs").glob("*/*"))
            self.assertEqual(len(snapshots), 1)
            self.assertEqual(snapshots[0].name, "gap-before.json")
            snapshot = json.loads(snapshots[0].read_text())
            self.assertEqual(snapshot["exams"][0]["missing_references"], [1, 3])
            self.assertFalse((data / "exams").exists())
            return original(exams, data)

        with patch.object(amc, "collect_links", checked_collect):
            report = self.run_inventory(collect=True)
        self.assertEqual(report["references_created"], 2)
        self.assertEqual(report["gap_before"][0]["missing_references"], [1, 3])
        self.assertEqual(report["gap_after"][0]["missing_references"], [])
        self.assertEqual(report["gap_after"][0]["missing_solutions"], [1, 3])
        self.assertEqual(
            {path.name for path in (self.problems / "01").iterdir()}, {"source.json"}
        )

    def test_rerun_preserves_content_and_collects_only_new_links(self):
        self.run_inventory(collect=True)
        question = self.problems / "01" / "question.md"
        solution = self.problems / "01" / "solution.md"
        question.write_text("User supplied question", encoding="utf-8")
        solution.write_text("User supplied solution", encoding="utf-8")
        source = self.problems / "01" / "source.json"
        source_before = source.read_bytes()
        pages = website()
        pages[EXAM] += page(f"{EXAM}/Problem_4")
        report = self.run_inventory(collect=True, pages=pages)
        self.assertEqual(report["references_created"], 1)
        self.assertEqual(report["gap_before"][0]["missing_references"], [4])
        self.assertEqual(report["gap_after"][0]["missing_questions"], [3, 4])
        self.assertEqual(report["gap_after"][0]["missing_solutions"], [3, 4])
        self.assertEqual(source.read_bytes(), source_before)
        self.assertEqual(question.read_text(), "User supplied question")
        self.assertEqual(solution.read_text(), "User supplied solution")
        self.assertEqual(len(list((self.data / "reports" / "runs").iterdir())), 2)
        self.assertEqual(self.run_inventory(collect=True, pages=pages)["references_created"], 0)

    def test_empty_content_does_not_count_as_collected(self):
        self.run_inventory(collect=True)
        (self.problems / "01" / "question.md").write_text(" \n", encoding="utf-8")
        report = self.run_inventory()
        self.assertEqual(report["gap_before"][0]["local_questions"], [])

    def test_diagram_is_not_a_collected_question(self):
        practice_figs = self.repo / "practice" / "figs"
        practice_figs.mkdir(parents=True)
        (practice_figs / "2022-12A-12.png").write_bytes(b"fixture")
        problem_figs = self.problems / "01" / "figures"
        problem_figs.mkdir(parents=True)
        (problem_figs / "question-01.png").write_bytes(b"figure")
        report = self.run_inventory()
        row = report["gap_before"][0]
        self.assertEqual(row["local_diagrams"], [1, 12])
        self.assertEqual(row["local_questions"], [])
        self.assertEqual(row["missing_questions"], [1, 3])

    def test_failure_retains_local_exam_with_unknown_remote_gaps(self):
        self.run_inventory(collect=True)

        def blocked(title):
            raise amc.InventoryError("HTTP 403: access denied")

        report = amc.run(self.repo, True, set(), blocked)
        self.assertEqual(report["discovery_status"], "failed")
        self.assertIsNone(report["exams_discovered"])
        self.assertIsNone(report["gap_before"][0]["missing_questions"])
        self.assertEqual(report["gap_before"][0]["local_references"], [1, 3])
        self.assertEqual(report["references_created"], 0)
        summary = (self.data / "reports" / "latest.md").read_text()
        self.assertIn("Exams discovered: unknown", summary)
        self.assertNotIn("None", summary)

    def test_failure_without_any_local_exams_does_not_invent_inventory(self):
        def blocked(title):
            raise amc.InventoryError("HTTP 429: rate limited")

        report = amc.run(self.repo, True, set(), blocked)
        self.assertEqual(report["gap_before"], [])
        self.assertIsNone(report["problem_links_discovered"])
        self.assertFalse((self.data / "exams").exists())

    def test_wrong_local_source_url_is_not_silently_counted(self):
        self.run_inventory(collect=True)
        source = self.problems / "01" / "source.json"
        source.write_text('{"url": "https://example.com"}', encoding="utf-8")
        with self.assertRaisesRegex(amc.InventoryError, "Invalid problem source"):
            self.run_inventory(collect=True)


class FetcherTests(unittest.TestCase):
    def test_http_and_connection_failures_are_explicit(self):
        errors = [
            HTTPError(amc.BASE_URL, 403, "Forbidden", {}, None),
            HTTPError(amc.BASE_URL, 429, "Too Many Requests", {}, None),
            URLError("unreachable"),
        ]
        for error in errors:
            with self.subTest(error=error):
                with patch.object(amc, "urlopen", side_effect=error):
                    with self.assertRaises(amc.InventoryError):
                        amc.Fetcher(2)(amc.ROOT_TITLE)

    def test_http_403_retries_in_browser_and_keeps_wiki_html(self):
        def browser(title):
            self.assertEqual(title, amc.ROOT_TITLE)
            return page(EXAM)

        with patch.object(amc, "urlopen", side_effect=HTTPError(amc.BASE_URL, 403, "Forbidden", {}, None)):
            html = amc.Fetcher(1, browser_fetch=browser)(amc.ROOT_TITLE)
        self.assertIn(EXAM, html)

    def test_wiki_index_fallback_when_http_blocked(self):
        wiki_index = {amc.ROOT_TITLE: [EXAM], EXAM: [f"{EXAM}/Problem_1"]}
        with patch.object(amc, "urlopen", side_effect=HTTPError(amc.BASE_URL, 403, "Forbidden", {}, None)):
            fetcher = amc.Fetcher(1, wiki_index=wiki_index)
            self.assertIn(EXAM, fetcher(amc.ROOT_TITLE))
            self.assertIn(f"{EXAM}/Problem_1", fetcher(EXAM))

    def test_unfinished_security_check_names_cookie_file(self):
        with patch.object(amc, "urlopen", side_effect=HTTPError(amc.BASE_URL, 403, "Forbidden", {}, None)):
            with self.assertRaisesRegex(amc.InventoryError, "cookies.txt"):
                amc.Fetcher(1, browser_fetch=lambda title: "Just a moment")(amc.ROOT_TITLE)

    def test_cookie_header_is_sent_and_not_required_in_output(self):
        captured = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return page(EXAM).encode("utf-8")

        def open_request(request, timeout):
            captured["cookie"] = request.get_header("Cookie")
            return Response()

        with patch.object(amc, "urlopen", side_effect=open_request):
            html = amc.Fetcher(1, cookie_header="cf_clearance=secret")(amc.ROOT_TITLE)
        self.assertEqual(captured["cookie"], "cf_clearance=secret")
        self.assertNotIn("secret", html)

    def test_main_reports_failure_exit_code(self):
        with patch.object(sys, "argv", [str(SCRIPT)]):
            with patch.object(amc, "run", return_value={"error": "HTTP 403"}):
                with patch.object(amc, "markdown", return_value="failed report"):
                    with patch("builtins.print"):
                        self.assertEqual(amc.main(), 1)


if __name__ == "__main__":
    unittest.main()
