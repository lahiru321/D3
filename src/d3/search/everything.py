"""Thin ctypes client for the Everything (voidtools) SDK.

Requires Everything 1.4.1+ running (as a service or app) and Everything64.dll,
which ships in vendor/everything/.
"""

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REQUEST_FULL_PATH_AND_FILE_NAME = 0x00000004
REQUEST_DATE_MODIFIED = 0x00000040
SORT_DATE_MODIFIED_DESCENDING = 14

ERRORS = {
    1: "out of memory",
    2: "Everything is not running",
    3: "unable to register window class",
    4: "unable to create listening window",
    5: "unable to create listening thread",
    6: "invalid index",
    7: "invalid call",
    8: "invalid request data",
    9: "bad parameter",
}

DEFAULT_DLL = Path(__file__).resolve().parents[3] / "vendor" / "everything" / "Everything64.dll"

# FILETIME counts 100 ns ticks since 1601-01-01.
_EPOCH_DIFF_TICKS = 116_444_736_000_000_000


class EverythingError(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchResult:
    path: Path
    modified: datetime | None


class Everything:
    def __init__(self, dll_path: Path = DEFAULT_DLL) -> None:
        self._dll = ctypes.WinDLL(str(dll_path))
        d = self._dll
        d.Everything_SetSearchW.argtypes = [wintypes.LPCWSTR]
        d.Everything_SetRequestFlags.argtypes = [wintypes.DWORD]
        d.Everything_SetMax.argtypes = [wintypes.DWORD]
        d.Everything_SetSort.argtypes = [wintypes.DWORD]
        d.Everything_QueryW.argtypes = [wintypes.BOOL]
        d.Everything_QueryW.restype = wintypes.BOOL
        d.Everything_GetNumResults.restype = wintypes.DWORD
        d.Everything_GetTotResults.restype = wintypes.DWORD
        d.Everything_GetLastError.restype = wintypes.DWORD
        d.Everything_GetResultFullPathNameW.argtypes = [wintypes.DWORD, wintypes.LPWSTR, wintypes.DWORD]
        d.Everything_GetResultFullPathNameW.restype = wintypes.DWORD
        d.Everything_GetResultDateModified.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.FILETIME)]
        d.Everything_GetResultDateModified.restype = wintypes.BOOL
        d.Everything_IsDBLoaded.restype = wintypes.BOOL
        d.Everything_GetMajorVersion.restype = wintypes.DWORD
        d.Everything_GetMinorVersion.restype = wintypes.DWORD
        d.Everything_GetRevision.restype = wintypes.DWORD

    def version(self) -> str:
        d = self._dll
        major = d.Everything_GetMajorVersion()
        if major == 0:
            self._raise()
        return f"{major}.{d.Everything_GetMinorVersion()}.{d.Everything_GetRevision()}"

    def is_db_loaded(self) -> bool:
        return bool(self._dll.Everything_IsDBLoaded())

    def search(self, query: str, max_results: int = 50, sort_recent: bool = False) -> list[SearchResult]:
        """Run an Everything query (full Everything search syntax, e.g. 'invoice ext:pdf dm:lastmonth')."""
        d = self._dll
        d.Everything_SetSearchW(query)
        d.Everything_SetRequestFlags(REQUEST_FULL_PATH_AND_FILE_NAME | REQUEST_DATE_MODIFIED)
        d.Everything_SetMax(max_results)
        if sort_recent:
            d.Everything_SetSort(SORT_DATE_MODIFIED_DESCENDING)
        if not d.Everything_QueryW(True):
            self._raise()

        results = []
        buf = ctypes.create_unicode_buffer(32_768)
        ft = wintypes.FILETIME()
        for i in range(d.Everything_GetNumResults()):
            d.Everything_GetResultFullPathNameW(i, buf, len(buf))
            modified = None
            if d.Everything_GetResultDateModified(i, ctypes.byref(ft)):
                ticks = (ft.dwHighDateTime << 32) | ft.dwLowDateTime
                if ticks:
                    modified = datetime.fromtimestamp((ticks - _EPOCH_DIFF_TICKS) / 10_000_000, tz=timezone.utc)
            results.append(SearchResult(Path(buf.value), modified))
        return results

    def _raise(self) -> None:
        code = self._dll.Everything_GetLastError()
        raise EverythingError(f"Everything SDK error {code}: {ERRORS.get(code, 'unknown')}")
