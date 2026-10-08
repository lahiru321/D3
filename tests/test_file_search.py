from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from d3.search.everything import SearchResult
from d3.search.files import ChoiceMemory, FileSearch, coverage, name_tokens, parse, period_range
from d3.search.folders import resolve_folder


class NoRecent:
    def opened_at(self, path):
        return None


def ranker(tmp_path) -> FileSearch:
    return FileSearch(everything=None, roots=[], excludes=[], recent=NoRecent(),
                      memory=ChoiceMemory(tmp_path / "choices.json"))


def days_ago(n: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=n)


def top(fs: FileSearch, target: str, results: list[SearchResult]) -> Path:
    q = parse(target)
    return fs.rank(q, results)[0].path


@pytest.mark.parametrize("target, words, folders_only, ext, period, recent", [
    ("the StoreX proposal", ["storex", "proposal"], False, None, None, False),
    ("last month's invoice", ["invoice"], False, None, "lastmonth", False),
    ("my latest invoice", ["invoice"], False, None, None, True),
    ("the projects folder", ["projects"], True, None, None, False),
    ("the budget pdf", ["budget"], False, "pdf", None, False),
    ("proposal and budget", ["proposal", "budget"], False, None, None, False),
])
def test_parse(target, words, folders_only, ext, period, recent):
    q = parse(target)
    assert (q.words, q.folders_only, q.ext, q.period, q.prefer_recent) == (words, folders_only, ext, period, recent)


def test_name_tokens_keep_unsplit_forms():
    assert "d3" in name_tokens(Path("D:/PROJECTS/D3"), is_folder=True)
    tokens = name_tokens(Path("StoreXProposal.pdf"), is_folder=False)
    assert "proposal" in tokens
    assert coverage(["storex", "proposal"], tokens) > 0.9  # 'storex' matched across 'store' + 'x'


def test_coverage_is_fuzzy_and_uses_parent_folders():
    assert coverage(["storex", "proposive"], ["storex", "proposal"]) > 0.8   # misheard word still counts
    assert coverage(["d3", "plan"], ["plan"], ["d3", "projects"]) == pytest.approx(0.9)
    assert coverage(["report"], ["invoice"]) == 0.0


def test_documents_beat_code_and_code_trees(tmp_path):
    fs = ranker(tmp_path)
    results = [
        SearchResult(Path(r"D:\app\src\main\java\invoice"), days_ago(1), is_folder=True),
        SearchResult(Path(r"D:\app\db\V27__add_invoice.sql"), days_ago(1)),
        SearchResult(Path(r"C:\Users\me\Downloads\Invoice-0008.pdf"), days_ago(90)),
    ]
    assert top(fs, "my invoice", results).name == "Invoice-0008.pdf"


def test_spoken_period_is_a_preference_not_a_filter(tmp_path):
    fs = ranker(tmp_path)
    now = datetime.now().astimezone()
    start, end = period_range("lastmonth", now)
    inside = start + (end - start) / 2
    results = [
        SearchResult(Path(r"C:\Docs\invoice-a.pdf"), days_ago(2)),
        SearchResult(Path(r"C:\Docs\invoice-b.pdf"), inside.astimezone(timezone.utc)),
    ]
    assert top(fs, "last month's invoice", results).name == "invoice-b.pdf"
    # With nothing from last month, an invoice is still found.
    assert top(fs, "last month's invoice", results[:1]).name == "invoice-a.pdf"


def test_learns_from_choices(tmp_path):
    fs = ranker(tmp_path)
    a = SearchResult(Path(r"C:\Docs\report-final.pdf"), days_ago(1))
    b = SearchResult(Path(r"C:\Old\report-final.pdf"), days_ago(5))
    assert top(fs, "report final", [a, b]) == a.path
    fs.memory.record(["report", "final"], b.path)
    assert top(fs, "report final", [a, b]) == b.path


def test_folder_word_prefers_folders(tmp_path):
    fs = ranker(tmp_path)
    results = [
        SearchResult(Path(r"D:\Lumora\lumora.pdf"), days_ago(1)),
        SearchResult(Path(r"D:\Lumora"), days_ago(1), is_folder=True),
    ]
    assert top(fs, "the lumora folder", results) == Path(r"D:\Lumora")
    assert top(fs, "lumora", results).name == "lumora.pdf"


@pytest.mark.parametrize("target, expected", [
    ("my downloads", "Downloads"),
    ("the desktop", "Desktop"),
    ("projects", "PROJECTS"),
])
def test_folder_aliases(target, expected):
    path = resolve_folder(target, {"projects": r"D:\PROJECTS"})
    assert path is not None and path.name == expected


def test_not_a_folder_alias():
    assert resolve_folder("the storex proposal", {}) is None
