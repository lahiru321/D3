"""Spoken file description -> ranked candidate files/folders (FR-6, FR-7).

Everything finds candidates by name inside the indexed folders; ranking then
combines how well the spoken words cover the file (and parent folder) names,
file type, recency, a spoken date ("last month's"), whether the user recently
opened it (Windows Recent items), and past choices.
"""

import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rapidfuzz import fuzz

from d3.search.everything import Everything, SearchResult

STOPWORDS = {"the", "a", "an", "my", "file", "document", "doc", "please", "of", "for", "called", "named", "that", "one",
             "and", "with"}
FOLDER_WORDS = {"folder", "directory"}

# Spoken type words -> Everything ext: filter.
TYPE_WORDS = {
    "pdf": "pdf",
    "word": "doc;docx", "docx": "doc;docx",
    "excel": "xls;xlsx;csv", "spreadsheet": "xls;xlsx;csv", "sheet": "xls;xlsx;csv",
    "powerpoint": "ppt;pptx", "presentation": "ppt;pptx", "slides": "ppt;pptx",
    "image": "png;jpg;jpeg;gif;webp;bmp;heic", "photo": "png;jpg;jpeg;heic;webp", "picture": "png;jpg;jpeg;heic;webp",
    "screenshot": "png;jpg",
    "video": "mp4;mkv;mov;avi;webm", "movie": "mp4;mkv;mov;avi",
    "song": "mp3;m4a;flac;wav",
}

# Spoken date phrases -> period key. A strong ranking preference, not a hard filter:
# "last month's invoice" should still find an older invoice, never a code file.
DATE_PHRASES = [
    (r"\blast month'?s?\b", "lastmonth"),
    (r"\bthis month'?s?\b", "thismonth"),
    (r"\blast week'?s?\b", "lastweek"),
    (r"\bthis week'?s?\b", "thisweek"),
    (r"\byesterday'?s?\b", "yesterday"),
    (r"\btoday'?s?\b", "today"),
    (r"\blast year'?s?\b", "lastyear"),
]
LATEST_RE = re.compile(r"\b(latest|newest|most recent|recent|last)\b")

LIKELY_WANTED = {"pdf", "doc", "docx", "xls", "xlsx", "csv", "ppt", "pptx", "txt", "md", "png", "jpg", "jpeg",
                 "gif", "webp", "heic", "mp4", "mkv", "mov", "avi", "mp3", "m4a", "flac", "wav", "zip", "rar", "7z",
                 "psd", "fig", "ai", "svg"}  # not exe: apps open through the app index
# Source code is rarely what "open the invoice" means; it can still win on a clear name match.
CODE_EXT = {"py", "java", "ts", "tsx", "js", "jsx", "sql", "html", "css", "scss", "json", "xml", "yml", "yaml",
            "kt", "cs", "go", "rs", "c", "cpp", "h", "php", "rb", "sh", "ps1", "bat", "toml", "gradle", "properties"}
JUNK_EXT = {"dll", "sys", "tmp", "log", "pyc", "lock", "map", "cache", "db", "dat", "bin", "ini", "lnk", "url",
            "pf", "etl", "mui", "cab", "manifest", "class", "o", "obj", "pdb", "idx", "pack", "wal", "shm", "jsonl"}

# Anything under these folders is project source (Java packages named "invoice" etc.), not what you open by voice.
CODE_TREE = {"src", "test", "tests", "main", "java", "resources", "lib", "migration", "migrations", "api", "services"}
CODE_TREE_PENALTY = -15
FOLDER_PENALTY = -6   # "open the invoice" usually means a file; saying "folder" flips this to a bonus

PARENT_WEIGHT = 0.8   # a spoken word found only in a parent folder name ('the D3 plan' -> D3\PLAN.md)
PARENT_LEVELS = 3
DATE_BONUS = 20


@dataclass
class Query:
    words: list[str]
    folders_only: bool = False
    ext: str | None = None
    period: str | None = None    # key from DATE_PHRASES
    prefer_recent: bool = False


@dataclass
class Candidate:
    path: Path
    is_folder: bool
    modified: datetime | None
    score: float = 0.0
    coverage: float = 0.0
    parts: dict = field(default_factory=dict)


def parse(target: str) -> Query:
    text = target.lower().replace("’", "'")
    q = Query(words=[])
    for pattern, period in DATE_PHRASES:
        if re.search(pattern, text):
            q.period = period
            text = re.sub(pattern, " ", text)
            break
    if LATEST_RE.search(text):
        q.prefer_recent = True
        text = LATEST_RE.sub(" ", text)
    words = re.sub(r"[^a-z0-9' ]+", " ", text).replace("'s", "").replace("'", "").split()
    for w in words:
        if w in FOLDER_WORDS:
            q.folders_only = True
        elif w in TYPE_WORDS and q.ext is None and len(words) > 1:
            q.ext = TYPE_WORDS[w]
        elif w not in STOPWORDS:
            q.words.append(w)
    return q


def period_range(period: str, now: datetime) -> tuple[datetime, datetime]:
    """[start, end) in local time for a DATE_PHRASES key."""
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = day - timedelta(days=day.weekday())
    month = day.replace(day=1)
    prev_month = (month - timedelta(days=1)).replace(day=1)
    year = day.replace(month=1, day=1)
    later = now + timedelta(seconds=1)
    return {
        "today": (day, later),
        "yesterday": (day - timedelta(days=1), day),
        "thisweek": (week, later),
        "lastweek": (week - timedelta(days=7), week),
        "thismonth": (month, later),
        "lastmonth": (prev_month, month),
        "lastyear": (year.replace(year=year.year - 1), year),
    }[period]


def name_tokens(path: Path, is_folder: bool) -> list[str]:
    """Words of a name, plus camelCase / letter-digit splits ('StoreXProposal', 'v2final'),
    keeping the unsplit forms too so 'D3' still matches 'd3'."""
    stem = path.name if is_folder else path.stem
    plain = re.sub(r"[^a-z0-9]+", " ", stem.lower()).split()
    split = re.sub(r"([a-z])([A-Z])", r"\1 \2", stem)
    split = re.sub(r"([A-Z])([A-Z][a-z])", r"\1 \2", split)  # 'XProposal' -> 'X Proposal'
    split = re.sub(r"([A-Za-z])(\d)|(\d)([A-Za-z])", r"\1\3 \2\4", split)
    extra = [t for t in re.sub(r"[^a-z0-9]+", " ", split.lower()).split() if t not in plain]
    return plain + extra


def folder_tokens(path: Path) -> list[str]:
    tokens = []
    for parent in list(path.parents)[:PARENT_LEVELS]:
        if parent.name:
            tokens += name_tokens(parent, is_folder=True)
    return tokens


def _word_match(word: str, tokens: list[str]) -> float:
    if not tokens:
        return 0.0
    best = max(fuzz.ratio(word, t) for t in tokens)
    if len(word) >= 3 and word in "".join(tokens):  # 'storex' inside 'storexrestaurant'
        best = max(best, 95)
    return best / 100 if best >= 70 else 0.0


def coverage(words: list[str], tokens: list[str], parent_tokens: list[str] = ()) -> float:
    """Share of spoken words found in the name (fuzzy per word, so 'proposive' still counts for
    'proposal'). Words found only in a parent folder name count at PARENT_WEIGHT."""
    if not words or not tokens:
        return 0.0
    total = 0.0
    for w in words:
        total += max(_word_match(w, tokens), PARENT_WEIGHT * _word_match(w, list(parent_tokens)))
    return total / len(words)


class RecentItems:
    """Names of items in %APPDATA%\\Microsoft\\Windows\\Recent (what the user actually opened)."""

    def __init__(self, refresh_s: int = 600) -> None:
        self._dir = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent"
        self._refresh_s = refresh_s
        self._loaded = 0.0
        self._names: dict[str, float] = {}

    def opened_at(self, path: Path) -> float | None:
        if time.time() - self._loaded > self._refresh_s:
            self._reload()
        return self._names.get(path.name.lower()) or self._names.get(path.stem.lower())

    def _reload(self) -> None:
        names = {}
        try:
            for lnk in self._dir.glob("*.lnk"):
                stem = re.sub(r" \(\d+\)$", "", lnk.stem.lower())  # 'Downloads (2)'
                names[stem] = lnk.stat().st_mtime
        except OSError:
            pass
        self._names = names
        self._loaded = time.time()


class ChoiceMemory:
    """Learns from what was opened: a path opened before for the same words ranks higher."""

    def __init__(self, path: Path) -> None:
        self._path = path
        try:
            self._data: dict[str, dict[str, int]] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._data = {}

    @staticmethod
    def key(words: list[str]) -> str:
        return " ".join(sorted(words))

    def count(self, words: list[str], path: Path) -> tuple[int, int]:
        """(times opened for these words, times opened overall)."""
        p = str(path).lower()
        same = self._data.get(self.key(words), {}).get(p, 0)
        overall = sum(paths.get(p, 0) for paths in self._data.values())
        return same, overall

    def record(self, words: list[str], path: Path) -> None:
        bucket = self._data.setdefault(self.key(words), {})
        bucket[str(path).lower()] = bucket.get(str(path).lower(), 0) + 1
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=1), encoding="utf-8")


class FileSearch:
    def __init__(self, everything: Everything, roots: list[Path], excludes: list[str],
                 recent: RecentItems, memory: ChoiceMemory) -> None:
        self._ev = everything
        self._roots = roots
        self._excludes = excludes
        self.recent = recent
        self.memory = memory

    def _scope(self) -> str:
        roots = "|".join('"' + str(r).rstrip("\\") + '\\"' for r in self._roots)
        excludes = " ".join(f'!"{e}"' for e in self._excludes)
        return f"<{roots}> {excludes}"

    @staticmethod
    def _filters(q: Query) -> str:
        parts = []
        if q.folders_only:
            parts.append("folder:")
        if q.ext:
            parts.append(f"ext:{q.ext}")
        return " ".join(parts)

    def candidates(self, q: Query) -> list[SearchResult]:
        base = f"{self._scope()} {self._filters(q)}"
        if not q.words:
            if not (q.period or q.ext):
                return []
            period = f"dm:{q.period}" if q.period else ""
            return self._ev.search(f"{base} {period} file:", max_results=50, sort_recent=True)
        strict = self._ev.search(f"{base} {' '.join(q.words)}", max_results=200)
        if len(strict) >= 3:
            return strict
        # Loose: any longer word, so a misheard word ('proposive') or a word that is
        # only in the folder name ('d3') doesn't empty the result.
        loose_words = [w for w in q.words if len(w) >= 3] or q.words
        loose = self._ev.search(f"{base} <{'|'.join(loose_words)}>", max_results=500, sort_recent=True)
        seen = {r.path for r in strict}
        return strict + [r for r in loose if r.path not in seen]

    def rank(self, q: Query, results: list[SearchResult]) -> list[Candidate]:
        now = datetime.now(timezone.utc)
        period = period_range(q.period, datetime.now().astimezone()) if q.period else None
        ranked = []
        for r in results:
            c = Candidate(r.path, r.is_folder, r.modified)
            tokens = name_tokens(r.path, r.is_folder)
            c.coverage = coverage(q.words, tokens, folder_tokens(r.path)) if q.words else 1.0
            precision = (sum(1 for t in tokens if any(fuzz.ratio(w, t) >= 80 for w in q.words)) / len(tokens)
                         if q.words and tokens else 0.0)
            age_days = (now - r.modified).total_seconds() / 86400 if r.modified else 3650
            recency = (25 if q.prefer_recent else 10) * math.exp(-max(age_days, 0) / 60)
            opened = self.recent.opened_at(r.path)
            recent_boost = 10 * math.exp(-(time.time() - opened) / 86400 / 30) if opened else 0.0
            same, overall = self.memory.count(q.words, r.path)
            learned = 20 if same else (5 if overall else 0)
            ext = r.path.suffix.lower().lstrip(".")
            in_code_tree = any(p.name.lower() in CODE_TREE or p.name.startswith(".") for p in r.path.parents)
            if r.is_folder:
                type_prior = 5 if q.folders_only else FOLDER_PENALTY
            elif ext in JUNK_EXT:
                type_prior = -25
            elif ext in CODE_EXT:
                type_prior = -12
            else:
                type_prior = 4 if ext in LIKELY_WANTED else 0
            if in_code_tree:
                type_prior += CODE_TREE_PENALTY
            in_period = DATE_BONUS if period and r.modified and period[0] <= r.modified < period[1] else 0
            c.parts = {"cover": round(70 * c.coverage, 1), "prec": round(15 * precision, 1),
                       "recent": round(recency, 1), "opened": round(recent_boost, 1), "learned": learned,
                       "type": type_prior, "date": in_period}
            c.score = sum(c.parts.values())
            ranked.append(c)
        ranked.sort(key=lambda c: c.score, reverse=True)
        return ranked

    def search(self, target: str) -> tuple[Query, list[Candidate]]:
        q = parse(target)
        return q, self.rank(q, self.candidates(q))
