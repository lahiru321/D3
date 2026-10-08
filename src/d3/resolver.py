"""'open <target>' -> what to open. Order: folder alias, installed app, then file search."""

from dataclasses import dataclass, field
from pathlib import Path

from d3.config import resolve
from d3.search.apps import App, AppIndex
from d3.search.everything import Everything, EverythingError
from d3.search.files import Candidate, ChoiceMemory, FileSearch, RecentItems, parse
from d3.search.folders import expand, resolve_folder

FOLDER = "folder"
APP = "app"
EXE = "exe"
FILE = "file"
CHOOSE = "choose"
NONE = "none"


@dataclass
class Resolution:
    kind: str
    target: str
    folder: Path | None = None
    app: App | None = None
    exe: str | None = None
    candidates: list[Candidate] = field(default_factory=list)
    words: list[str] = field(default_factory=list)
    note: str = ""


class Resolver:
    def __init__(self, cfg: dict) -> None:
        data_dir = resolve(cfg["data"]["dir"])
        search = cfg["search"]
        self._folder_aliases = cfg.get("folders", {}).get("aliases", {})
        self._margin = search["ask_margin"]
        self._min_coverage = search["min_coverage"]
        self.apps = AppIndex(data_dir / "apps.json", cfg.get("apps", {}).get("aliases", {}))

        roots: list[Path] = []
        for spec in search["roots"]:
            try:
                path = expand(spec)
            except ValueError:
                continue
            if path.exists() and path not in roots:
                roots.append(path)
        self.memory = ChoiceMemory(data_dir / "choices.json")
        try:
            everything = Everything()
            everything.version()
            self.files: FileSearch | None = FileSearch(everything, roots, search["excludes"], RecentItems(), self.memory)
        except (OSError, EverythingError):
            self.files = None

    def resolve(self, target: str) -> Resolution:
        folder = resolve_folder(target, self._folder_aliases)
        if folder is not None:
            return Resolution(FOLDER, target, folder=folder)

        q = parse(target)
        if not q.folders_only and not q.period and not q.ext:
            app = self.apps.find(target)
            if app is not None:
                return Resolution(APP, target, app=app)
            exe = self.apps.fallback_exe(target)
            if exe is not None:
                return Resolution(EXE, target, exe=exe)

        if self.files is None:
            return Resolution(NONE, target, note="File search is unavailable: is Everything running?")
        try:
            q, ranked = self.files.search(target)
        except EverythingError as exc:
            return Resolution(NONE, target, note=str(exc))

        strong = [c for c in ranked if c.coverage >= self._min_coverage]
        if not strong:
            return Resolution(NONE, target, words=q.words, note=f"{len(ranked)} weak matches")
        if len(strong) > 1 and strong[0].score - strong[1].score < self._margin:
            return Resolution(CHOOSE, target, candidates=strong[:3], words=q.words)
        return Resolution(FILE, target, candidates=strong[:3], words=q.words)
