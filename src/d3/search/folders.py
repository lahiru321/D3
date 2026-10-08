"""Spoken folder names -> paths: Windows known folders, drives, and user aliases."""

import ctypes
import os
import re
import uuid
from ctypes import wintypes
from pathlib import Path

KNOWN_FOLDERS = {
    "desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
    "music": "4BD8D571-6D19-48D3-BE97-422220080E43",
    "onedrive": "A52BBA46-E9E1-435F-B3D9-28DAA648C0F6",
    "programfiles": "905E63B6-C1BF-494E-B29C-65B732D3D21A",
    "programfilesx86": "7C5A40EF-A0FB-4BFC-874A-C0F2E0B9FA8E",
    "windows": "F38BF404-1D43-42F2-9305-67DE0B28FC23",
    "system": "1AC14E77-02E7-4E5D-B744-2EB1AE5198B7",
}

# Spoken variants -> known folder key.
SPOKEN = {
    "desktop": "desktop",
    "documents": "documents", "document": "documents", "docs": "documents",
    "downloads": "downloads", "download": "downloads",
    "pictures": "pictures", "picture": "pictures", "photos": "pictures", "photo": "pictures",
    "images": "pictures", "image": "pictures",
    "videos": "videos", "video": "videos",
    "music": "music",
}

FILLER = {"my", "the", "folder", "directory", "dir", "open", "a"}


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


def known_folder(key: str) -> Path | None:
    """Real path of a known folder (follows OneDrive redirection)."""
    guid_str = KNOWN_FOLDERS.get(key.lower().strip("{}"))
    if guid_str is None:
        return None
    guid = _GUID.from_buffer_copy(uuid.UUID(guid_str).bytes_le)
    out = ctypes.c_wchar_p()
    if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) != 0:
        return None
    try:
        return Path(out.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(out)


def known_folder_by_guid(guid: str) -> Path | None:
    for key, value in KNOWN_FOLDERS.items():
        if value.lower() == guid.strip("{}").lower():
            return known_folder(key)
    return None


def expand(spec: str) -> Path:
    """'{downloads}\\x' -> real path; anything else is a literal path."""
    m = re.match(r"^\{(\w+)\}(.*)$", spec)
    if m:
        base = known_folder(m.group(1))
        if base is None:
            raise ValueError(f"Unknown known folder {m.group(1)!r}")
        return base / m.group(2).lstrip("\\/") if m.group(2) else base
    return Path(spec)


def resolve_folder(target: str, aliases: dict[str, str]) -> Path | None:
    """Match 'my downloads', 'the D drive', 'projects folder' etc. Returns None if not a folder alias."""
    words = [w for w in re.sub(r"[^a-z0-9 ]+", " ", target.lower()).split() if w not in FILLER]
    phrase = " ".join(words)
    if not phrase:
        return None

    for alias, path in aliases.items():
        if phrase == alias.lower():
            return expand(path)

    if phrase in SPOKEN:
        return known_folder(SPOKEN[phrase])

    m = re.fullmatch(r"([a-z])(?: drive)?", phrase)  # "D drive", "D folder", or Whisper's bare "D."
    if m:
        drive = Path(f"{m.group(1).upper()}:\\")
        return drive if drive.exists() else None
    return None


HASHY = re.compile(r"^[0-9a-f]{8,}$|^\d+$")


def folder_names(roots: list[Path], excludes: list[str], depth: int = 2) -> list[str]:
    """Names of folders near the top of each root: the words people say ("Lumora", "PROJECTS")."""
    names: list[str] = []
    frontier = list(roots)
    for _ in range(depth):
        next_level = []
        for folder in frontier:
            try:
                entries = list(os.scandir(folder))
            except OSError:
                continue
            for entry in entries:
                name = entry.name
                if (not entry.is_dir(follow_symlinks=False) or name.startswith((".", "$", "_"))
                        or HASHY.match(name.lower()) or any(e.strip("\\").lower() in entry.path.lower() + "\\"
                                                           for e in excludes)):
                    continue
                if name not in names:
                    names.append(name)
                next_level.append(Path(entry.path))
        frontier = next_level
    return names
