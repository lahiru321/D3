"""'open <target>' -> what to open. Order: folder alias, installed app, then file search.

Whisper often mishears personal names ("Lumora" -> "Loumora"). If nothing matches,
words are corrected against a vocabulary of the user's folder and app names and
the lookup is retried. The same vocabulary is given to Whisper as a hint.
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz, process

from d3.config import resolve
from d3.search.apps import App, AppIndex
from d3.search.everything import Everything, EverythingError
from d3.search.files import Candidate, ChoiceMemory, FileSearch, RecentItems, parse
from d3.search.folders import expand, folder_names, resolve_folder

FOLDER = "folder"
APP = "app"
EXE = "exe"
FILE = "file"
CHOOSE = "choose"
NONE = "none"

CORRECTION_CUTOFF = 80

GENERIC_NAMES = {
    "new folder", "files", "br", "bin", "assets", "resources", "locales", "policies", "tools", "appx", "uploads",
    "backups", "interfaces", "gamesaves", "data", "cache", "logs", "temp", "tmp", "config", "lib", "src", "docs",
    "images", "imageuploads", "plugins", "scripts", "build", "dist", "public", "static", "test", "tests", "app",
    # Windows/Office default folders
    "custom office templates", "my games", "my music", "my pictures", "my videos", "camera roll",
    "saved pictures", "screenshots", "video projects",
}
# Windows utilities: rarely said, and every name costs part of Whisper's small prompt budget.
SYSTEM_APPS = {"run", "services", "magnifier", "narrator", "news", "weather", "clock", "camera", "pemhttpd",
               "wsl", "access", "publisher", "xbox"}


def pinned_app_names() -> list[str]:
    """Apps on the taskbar and desktop: the ones people actually use and say."""
    places = [
        Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Internet Explorer" / "Quick Launch" / "User Pinned" / "TaskBar",
        Path(os.environ.get("USERPROFILE", "")) / "Desktop",
        Path(os.environ.get("PUBLIC", "")) / "Desktop",
    ]
    names = []
    for place in places:
        try:
            for lnk in sorted(place.glob("*.lnk")):
                name = re.sub(r" - Copy( \(\d+\))?$", "", lnk.stem)
                if name not in names:
                    names.append(name)
        except OSError:
            continue
    return names


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

        self.folder_names = folder_names(roots, search["excludes"])
        self.top_folders = folder_names(roots, search["excludes"], depth=1)  # 'Lumora', 'PROJECTS'
        self._vocab = sorted({w for name in self.folder_names + [a.name for a in self.apps.apps]
                              + list(self._folder_aliases)
                              for w in re.sub(r"[^a-z0-9]+", " ", name.lower()).split() if len(w) >= 4})

    def hint_names(self) -> list[str]:
        """Names to bias speech recognition, most likely to be said first (the Transcriber keeps
        as many as fit Whisper's prompt): folder aliases, taskbar/desktop apps, one-word Start
        menu apps, then folders near the top of the search roots."""
        pinned = pinned_app_names()
        one_word_apps = [a.name for a in self.apps.apps
                         if len(a.name.split()) == 1 and a.name.lower() not in SYSTEM_APPS]
        other_apps = [a.name for a in self.apps.apps if len(a.name.split()) == 2]
        ordered = (list(self._folder_aliases) + pinned + one_word_apps + self.top_folders
                   + self.folder_names + other_apps)
        names: list[str] = []
        seen: set[str] = set()
        for name in ordered:
            key = name.lower()
            # Skip numbered, default-Windows and generic tech folders: noise, not words people say.
            if (key not in seen and len(name.split()) <= 3 and key not in GENERIC_NAMES
                    and not (re.search(r"\d", name) and len(name) > 3)):
                names.append(name)
                seen.add(key)
        return names

    def correct(self, target: str) -> str:
        """Replace misheard words with the closest folder/app vocabulary word ('loumora' -> 'lumora')."""
        out = []
        for word in target.lower().split():
            if len(word) >= 4 and word not in self._vocab:
                hit = process.extractOne(word, self._vocab, scorer=fuzz.ratio, score_cutoff=CORRECTION_CUTOFF)
                word = hit[0] if hit else word
            out.append(word)
        return " ".join(out)

    def resolve(self, target: str) -> Resolution:
        res = self._resolve(target)
        if res.kind == NONE:
            corrected = self.correct(target)
            if corrected != target.lower():
                retry = self._resolve(corrected)
                if retry.kind != NONE:
                    retry.note = f"heard {target!r}, matched as {corrected!r}"
                    return retry
        return res

    def _resolve(self, target: str) -> Resolution:
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
