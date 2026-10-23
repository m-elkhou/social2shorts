"""Phone adapter: copies the QHD originals off the phone (Windows MTP) into a local cache folder.

Only files missing locally (same name and size) are copied, so repeated runs are fast.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from social2shorts.sources import VIDEO_EXTS, video_files

log = logging.getLogger(__name__)


class PhoneNotFound(Exception):
    """No connected device matches the configured name."""


class FileNotOnPhone(Exception):
    """A requested file or folder is not on the phone."""


class FolderNotOnPhone(FileNotOnPhone):
    """A configured folder does not exist on the phone."""


@dataclass(frozen=True)
class PhoneVideo:
    """A video file on the phone (size in bytes, None when the device does not report it)."""

    name: str
    folder: str
    size: int | None = None


class PhoneDevice(Protocol):
    """Port for a phone that can list and copy its videos."""

    def list_videos(self, folders: list[str]) -> list[PhoneVideo]: ...

    def copy(self, video: PhoneVideo, dest_dir: Path) -> Path: ...


def is_cached(video: PhoneVideo, dest_dir: Path) -> bool:
    """True if ``dest_dir`` already holds this video with the same size."""
    local = dest_dir / video.name
    return video.size is not None and local.is_file() and local.stat().st_size == video.size


def sync_originals(
    device: PhoneDevice, folders: list[str], dest_dir: Path, limit: int | None = None
) -> list[Path]:
    """Copy the phone videos not cached yet; return the paths that were copied.

    Raises:
        FolderNotOnPhone: If a configured folder is missing on the phone.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for video in device.list_videos(folders)[:limit]:
        if not is_cached(video, dest_dir):
            log.info("Copying %s from the phone", video.name)
            copied.append(device.copy(video, dest_dir))
    return copied


class PhoneOriginals:
    """``OriginalsSource`` adapter: syncs from the phone when plugged in, else uses the cache."""

    def __init__(
        self,
        device_factory: Callable[[], PhoneDevice],
        folders: list[str],
        limit: int | None = None,
    ):
        self.device_factory = device_factory
        self.folders = folders
        self.limit = limit

    def sync(self, dest_dir: Path) -> list[Path]:
        """Local originals after a best-effort sync."""
        if self.folders:
            try:
                copied = sync_originals(self.device_factory(), self.folders, dest_dir, self.limit)
                log.info("Phone sync: %d new original(s) copied", len(copied))
            except (PhoneNotFound, FileNotOnPhone, ImportError, OSError, TimeoutError) as exc:
                log.warning("Phone sync skipped (%s); using cached originals in %s", exc, dest_dir)
        return video_files(dest_dir)


class MtpPhoneSource:
    """Windows MTP implementation through the ``Shell.Application`` COM object (pywin32)."""

    def __init__(self, phone_name_match: str, folders: list[str], copy_timeout_s: float = 300.0):
        self.phone_name_match = phone_name_match
        self.folders = list(folders)
        self.copy_timeout_s = copy_timeout_s
        self._shell: Any = None

    def _get_shell(self) -> Any:
        if self._shell is None:
            import win32com.client

            self._shell = win32com.client.Dispatch("Shell.Application")
        return self._shell

    def _device_folder(self) -> Any:
        this_pc = self._get_shell().NameSpace(0x11)
        needle = self.phone_name_match.lower()
        for item in this_pc.Items():
            if needle in item.Name.lower():
                return item.GetFolder
        raise PhoneNotFound(
            f"No device matching {self.phone_name_match!r}: plug the phone in, unlock it "
            "and choose 'File transfer'"
        )

    def _resolve(self, path: str) -> Any:
        """Shell folder for ``path``, searching each storage of the device in order."""
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        for storage in self._device_folder().Items():
            if not storage.IsFolder:
                continue
            folder = storage.GetFolder
            for part in parts:
                nxt = next(
                    (i for i in folder.Items() if i.IsFolder and i.Name.lower() == part.lower()),
                    None,
                )
                if nxt is None:
                    folder = None
                    break
                folder = nxt.GetFolder
            if folder is not None:
                return folder
        return None

    def _items(self, path: str) -> list[Any]:
        folder = self._resolve(path)
        if folder is None:
            raise FolderNotOnPhone(path)
        return [
            it
            for it in folder.Items()
            if not it.IsFolder and Path(it.Name).suffix.lower() in VIDEO_EXTS
        ]

    def list_videos(self, folders: list[str]) -> list[PhoneVideo]:
        """Videos in ``folders`` with their size when the device reports it."""
        return [
            PhoneVideo(it.Name, path, int(it.Size or 0) or None)
            for path in folders
            for it in self._items(path)
        ]

    def copy(self, video: PhoneVideo, dest_dir: Path) -> Path:
        """Copy one video and wait until it is complete.

        Raises:
            FileNotOnPhone: If the file vanished from the phone.
            TimeoutError: If the asynchronous MTP copy does not finish in time.
        """
        items = self._items(video.folder)
        item = next((it for it in items if it.Name.lower() == video.name.lower()), None)
        if item is None:
            raise FileNotOnPhone(video.name)
        dest = dest_dir / video.name
        dest.unlink(missing_ok=True)  # stale/partial copy: never poll an old file
        self._get_shell().NameSpace(str(dest_dir.resolve())).CopyHere(item, 4 | 16)
        self._wait_complete(dest, video.size)
        return dest

    def _wait_complete(self, dest: Path, expected: int | None) -> None:
        """MTP CopyHere is asynchronous: wait for the expected size (or a stable size)."""
        deadline = time.monotonic() + self.copy_timeout_s
        last, stable = -1, 0
        while time.monotonic() < deadline:
            size = dest.stat().st_size if dest.exists() else -1
            if expected is not None and size == expected:
                return
            stable = stable + 1 if size > 0 and size == last else 0
            if expected is None and stable >= 4:  # ~2 s without growth
                return
            last = size
            time.sleep(0.5)
        raise TimeoutError(f"Copy of {dest.name!r} did not finish within {self.copy_timeout_s}s")
