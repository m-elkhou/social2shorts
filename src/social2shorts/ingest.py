from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Protocol

from social2shorts.models import Original

VIDEO_EXT = re.compile(r"\.(mp4|mov|3gp|mkv|m4v)$", re.IGNORECASE)
DURATION_HEADER = re.compile(r"Longueur|Length|Durée|Duration", re.IGNORECASE)


class PhoneNotFound(Exception):
    pass


class FileNotOnPhone(Exception):
    pass


class FolderNotOnPhone(FileNotOnPhone):
    pass


@dataclass
class PhoneVideo:
    name: str
    duration_s: float | None
    folder: str


class PhoneSource(Protocol):
    def list_videos(self, folders: list[str]) -> list[PhoneVideo]: ...

    def copy(self, name: str, dest_dir: str) -> str: ...


def _parse_duration(text: str) -> float | None:
    text = (text or "").strip().replace("\u200e", "").replace("\u200f", "")
    if not text:
        return None
    try:
        total = 0.0
        for part in text.split(":"):
            total = total * 60 + float(part)
        return total
    except ValueError:
        return None


class MtpPhoneSource:
    """Real Windows MTP implementation via Shell.Application COM (pywin32)."""

    def __init__(
        self,
        phone_name_match: str,
        folders: list[str] | None = None,
        copy_timeout_s: float = 120.0,
    ):
        self.phone_name_match = phone_name_match
        self.folders = list(folders or [])
        self.copy_timeout_s = copy_timeout_s
        self._shell = None

    def _get_shell(self):
        if self._shell is None:
            import win32com.client

            self._shell = win32com.client.Dispatch("Shell.Application")
        return self._shell

    def _device_folder(self):
        this_pc = self._get_shell().NameSpace(0x11)
        needle = self.phone_name_match.lower()
        for item in this_pc.Items():
            if needle in item.Name.lower():
                return item.GetFolder
        raise PhoneNotFound(f"No device matching {self.phone_name_match!r}")

    def _resolve(self, path: str):
        """Return the shell Folder for `path`, searching each storage in order."""
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        for storage in self._device_folder().Items():
            if not storage.IsFolder:
                continue
            folder = storage.GetFolder
            for part in parts:
                nxt = None
                for it in folder.Items():
                    if it.IsFolder and it.Name.lower() == part.lower():
                        nxt = it
                        break
                if nxt is None:
                    folder = None
                    break
                folder = nxt.GetFolder
            if folder is not None:
                return folder
        return None

    @staticmethod
    def _duration_col(folder) -> int | None:
        for c in range(321):
            header = folder.GetDetailsOf(None, c)
            if header and DURATION_HEADER.search(header):
                return c
        return None

    def list_videos(self, folders: list[str]) -> list[PhoneVideo]:
        out: list[PhoneVideo] = []
        for path in folders:
            folder = self._resolve(path)
            if folder is None:
                raise FolderNotOnPhone(path)
            col = self._duration_col(folder)
            for it in folder.Items():
                if it.IsFolder or not VIDEO_EXT.search(it.Name):
                    continue
                dur = _parse_duration(folder.GetDetailsOf(it, col)) if col is not None else None
                out.append(PhoneVideo(it.Name, dur, path))
        return out

    def copy(self, name: str, dest_dir: str) -> str:
        """Copy `name` from the constructor `folders` (the Protocol has no folder arg)."""
        item = None
        for path in self.folders:
            folder = self._resolve(path)
            if folder is None:
                continue
            for it in folder.Items():
                if not it.IsFolder and it.Name.lower() == name.lower():
                    item = it
                    break
            if item is not None:
                break
        if item is None:
            raise FileNotOnPhone(name)
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, name)
        # Remove stale content so the poll cannot return on an old file.
        if os.path.exists(dest):
            os.remove(dest)
        # MTP CopyHere is asynchronous: the file appears and grows while copying,
        # so wait until its size is stable across two consecutive checks.
        self._get_shell().NameSpace(os.path.abspath(dest_dir)).CopyHere(item, 20)
        deadline = time.monotonic() + self.copy_timeout_s
        last_size = -1
        while time.monotonic() < deadline:
            if os.path.exists(dest):
                size = os.path.getsize(dest)
                if size > 0 and size == last_size:
                    return dest
                last_size = size
            time.sleep(0.5)
        raise TimeoutError(f"Copy of {name!r} did not appear in {dest_dir!r} within {self.copy_timeout_s}s")


class FakePhoneSource:
    """Test double. catalog: folder path -> list of (name, source_path_on_disk)."""

    def __init__(self, catalog: dict):
        self.catalog = catalog

    def list_videos(self, folders: list[str]) -> list[PhoneVideo]:
        out: list[PhoneVideo] = []
        for folder in folders:
            if folder not in self.catalog:
                raise FolderNotOnPhone(folder)
            out.extend(PhoneVideo(name, None, folder) for name, _ in self.catalog[folder])
        return out

    def copy(self, name: str, dest_dir: str) -> str:
        for entries in self.catalog.values():
            for n, src in entries:
                if n == name:
                    os.makedirs(dest_dir, exist_ok=True)
                    dest = os.path.join(dest_dir, name)
                    shutil.copyfile(src, dest)
                    return dest
        raise FileNotOnPhone(name)


def ffprobe_duration(path: str) -> float:
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(res.stdout.strip())


def ingest_originals(
    source: PhoneSource,
    folders: list[str],
    dest_dir: str,
    probe: Callable[[str], float] = ffprobe_duration,
) -> list[Original]:
    originals: list[Original] = []
    for video in source.list_videos(folders):
        path = source.copy(video.name, dest_dir)
        originals.append(Original(path=path, duration=probe(path), phashes=[]))
    return originals
