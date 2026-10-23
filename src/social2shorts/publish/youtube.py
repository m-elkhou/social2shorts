"""YouTube Data API adapter (``VideoPlatform``): OAuth, retried resumable upload, privacy."""

from __future__ import annotations

import http.client
import json
import logging
import os
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httplib2
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.errors import HttpError

from social2shorts.models import Claim, ShortMeta, UploadResult
from social2shorts.publish.contentid import check_claims

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
COST_LIST = 1  # videos.list
COST_UPDATE = 50  # videos.update
TRANSIENT_STATUS = frozenset({408, 429, 500, 502, 503, 504})  # other 4xx are never retried
QUOTA_REASONS = frozenset({"quotaExceeded", "dailyLimitExceeded", "uploadLimitExceeded"})
MAX_TAGS_CHARS = 450  # YouTube rejects more than 500 characters of tags
MAX_RETRIES = 8


class AuthRefreshError(Exception):
    """The stored token is missing or cannot be refreshed; run ``social2shorts auth``."""


class QuotaExceededError(Exception):
    """YouTube refused the call because today's API quota is spent."""


# --------------------------------------------------------------------- OAuth
def get_credentials(client_secrets: str, token_path: str, interactive: bool = True) -> Credentials:
    """Load, refresh or (interactively) create OAuth credentials.

    Args:
        client_secrets: OAuth client JSON downloaded from Google Cloud.
        token_path: Where the refresh token is stored (owner-only permissions).
        interactive: Allow opening the browser consent flow. Unattended runs pass False.

    Raises:
        AuthRefreshError: If the token is unusable, cannot be refreshed, or is missing
            while ``interactive`` is False.
    """
    creds = None
    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        except Exception as exc:  # corrupt JSON, missing fields, unreadable
            raise AuthRefreshError(f"stored token unusable: {exc}") from exc
    if creds is not None and creds.valid:
        return creds
    if creds is not None and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:
            raise AuthRefreshError(f"token refresh failed: {exc}") from exc
        _save(creds, token_path)
        return creds
    if creds is not None:
        raise AuthRefreshError("stored token has no refresh token")
    if not interactive:
        raise AuthRefreshError(f"no YouTube token at {token_path}")
    flow = InstalledAppFlow.from_client_secrets_file(client_secrets, SCOPES)
    creds = flow.run_local_server(port=0)
    _save(creds, token_path)
    return creds


def _save(creds: Any, token_path: str) -> None:
    """Write the token with owner-only permissions (best effort on Windows)."""
    path = Path(token_path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    try:
        os.chmod(path, 0o600)  # tighten an already-existing looser file
    except OSError:
        log.warning("Could not tighten permissions on %s", path)


# ---------------------------------------------------------------------- body
def rfc3339(dt: datetime) -> str:
    """UTC timestamp as YouTube expects it (``2026-10-23T11:00:00Z``)."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def with_shorts_tag(description: str) -> str:
    """Append ``#Shorts`` unless the description already has it."""
    if "#shorts" in description.lower():
        return description
    return f"{description}\n\n#Shorts".strip()


def _fit_tags(tags: list[str]) -> list[str]:
    kept: list[str] = []
    for tag in tags:
        if sum(len(t) + 1 for t in kept) + len(tag) > MAX_TAGS_CHARS:
            break
        kept.append(tag)
    return kept


def build_body(
    meta: ShortMeta,
    privacy: str,
    *,
    category_id: str = "2",
    language: str | None = None,
    publish_at: datetime | None = None,
    made_for_kids: bool = False,
    synthetic_media: bool = False,
) -> dict:
    """``videos.insert`` body; a ``publish_at`` forces ``private`` + scheduled publication."""
    snippet: dict[str, Any] = {
        "title": meta.title,
        "description": with_shorts_tag(meta.description),
        "tags": _fit_tags(meta.tags),
        "categoryId": category_id,
    }
    if language:
        snippet["defaultLanguage"] = language
        snippet["defaultAudioLanguage"] = language
    status: dict[str, Any] = {
        "privacyStatus": privacy,
        "selfDeclaredMadeForKids": made_for_kids,
        "containsSyntheticMedia": synthetic_media,
    }
    if publish_at is not None:
        status["privacyStatus"] = "private"
        status["publishAt"] = rfc3339(publish_at)
    return {"snippet": snippet, "status": status}


# --------------------------------------------------------------------- retry
def _reasons(exc: HttpError) -> set[str]:
    try:
        errors = json.loads(exc.content or b"{}").get("error", {}).get("errors", [])
    except (ValueError, AttributeError):
        return set()
    return {e.get("reason", "") for e in errors if isinstance(e, dict)}


def is_quota_error(exc: BaseException) -> bool:
    """True for HTTP 403 errors that mean the daily quota or upload limit is spent."""
    return (
        isinstance(exc, HttpError)
        and exc.resp.status == 403
        and bool(_reasons(exc) & QUOTA_REASONS)
    )


def is_transient(exc: BaseException) -> bool:
    """True for errors worth retrying: 5xx, dropped connections, timeouts."""
    if isinstance(exc, HttpError):  # includes ResumableUploadError
        return exc.resp.status in TRANSIENT_STATUS
    return isinstance(exc, OSError | httplib2.HttpLib2Error | http.client.HTTPException)


def run_resumable(
    request: Any, *, sleep: Callable[[float], None] = time.sleep, max_retries: int = MAX_RETRIES
) -> dict:
    """Drive ``request.next_chunk()`` to completion with exponential backoff + jitter.

    Raises:
        QuotaExceededError: If YouTube reports the quota is spent.
        HttpError: For non-transient errors, or after ``max_retries`` consecutive failures.
    """
    response, failures = None, 0
    while response is None:
        try:
            _, response = request.next_chunk()
            failures = 0
        except Exception as exc:
            if is_quota_error(exc):
                raise QuotaExceededError(str(exc)) from exc
            if not is_transient(exc) or failures >= max_retries:
                raise
            failures += 1
            delay = min(2**failures, 64) + random.random()
            log.warning("Upload hiccup (%s); retry %d in %.0fs", exc, failures, delay)
            sleep(delay)
    return response


def parse_upload_response(response: dict, body: dict) -> UploadResult:
    """Applied privacy/publishAt from a ``videos.insert`` response (falls back to the request)."""
    status = response.get("status") or body["status"]
    publish_at = status.get("publishAt")
    return UploadResult(
        video_id=response["id"],
        privacy=status.get("privacyStatus", body["status"]["privacyStatus"]),
        publish_at=datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
        if publish_at
        else None,
    )


# -------------------------------------------------------------------- client
class YouTubeClient:
    """``VideoPlatform`` adapter. The API service is built once, on first use."""

    def __init__(
        self,
        client_secrets: str,
        token_path: str,
        *,
        category_id: str = "2",
        language: str | None = None,
        made_for_kids: bool = False,
        synthetic_media: bool = False,
        service: Any = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.client_secrets = client_secrets
        self.token_path = token_path
        self.category_id = category_id
        self.language = language
        self.made_for_kids = made_for_kids
        self.synthetic_media = synthetic_media
        self._service = service
        self.sleep = sleep

    @property
    def service(self) -> Any:
        """Authorized API client (never opens a browser: unattended runs must not block)."""
        if self._service is None:
            from googleapiclient.discovery import build

            creds = get_credentials(self.client_secrets, self.token_path, interactive=False)
            self._service = build("youtube", "v3", credentials=creds, cache_discovery=False)
        return self._service

    def upload(
        self, path: Path, meta: ShortMeta, *, privacy: str, publish_at: datetime | None = None
    ) -> UploadResult:
        """Resumable ``videos.insert``; returns the id and the privacy YouTube actually set.

        Unaudited API projects get every upload locked to ``private`` (``publishAt`` dropped).
        """
        from googleapiclient.http import MediaFileUpload

        body = build_body(
            meta, privacy, category_id=self.category_id, language=self.language,
            publish_at=publish_at, made_for_kids=self.made_for_kids,
            synthetic_media=self.synthetic_media,
        )  # fmt: skip
        media = MediaFileUpload(str(path), mimetype="video/mp4", chunksize=8 << 20, resumable=True)
        request = self.service.videos().insert(part="snippet,status", body=body, media_body=media)
        return parse_upload_response(run_resumable(request, sleep=self.sleep), body)

    def set_privacy(self, video_id: str, privacy: str) -> None:
        """Change visibility (clears any scheduled ``publishAt``)."""
        status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": self.made_for_kids,
                  "containsSyntheticMedia": self.synthetic_media}  # fmt: skip
        body = {"id": video_id, "status": status}
        self.service.videos().update(part="status", body=body).execute(num_retries=3)

    def check_claims(self, video_ids: list[str]) -> list[Claim]:
        """See :func:`social2shorts.publish.contentid.check_claims`."""
        return check_claims(self.service, video_ids)
