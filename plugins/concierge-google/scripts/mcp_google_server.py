#!/usr/bin/env python3
"""MCP server exposing the user's Gmail, Calendar, and Drive via OAuth2.

Auth is split from this server: run `google_auth_setup.py` (see this
plugin's `scripts/`) by hand once (or after scopes change) to do the
interactive browser consent and cache a refresh token to
data/google_token.json. This server only ever loads that cached token and
silently refreshes it - if it's missing or revoked, tools return an error
telling the caller to re-run setup.

This script ships inside the `concierge-google` plugin, not the instance's
own repo, so `data/google_token.json` and `.env` can't be found relative to
`__file__` — they live in the instance. The instance root resolves via the
`CONCIERGE_HOME` env var (this plugin's `.mcp.json` sets it to
`${CLAUDE_PROJECT_DIR}` before launching this process), falling back to cwd
for manual/local-dev runs where that isn't set.

Every tool call is wrapped to return "ERROR: ..." strings rather than raise,
so a single bad call doesn't kill the server process.
"""
from __future__ import annotations

import base64
import os
import re
from datetime import datetime, timezone
from email.mime.text import MIMEText
from html.parser import HTMLParser
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from mcp.server.fastmcp import FastMCP

ROOT = os.environ.get("CONCIERGE_HOME") or os.getcwd()
ENV_PATH = os.path.join(ROOT, ".env")
TOKEN_PATH = os.path.join(ROOT, "data", "google_token.json")
SCRATCH_DIR = os.path.join(ROOT, "scratch")

SCOPES = [
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/tasks",
]

if os.path.exists(ENV_PATH):
    with open(ENV_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

mcp = FastMCP("google")


class AuthError(Exception):
    pass


def _credentials() -> Credentials:
    if not os.path.exists(TOKEN_PATH):
        raise AuthError(
            "No cached Google credentials found. Run "
            "the plugin's google_auth_setup.py (see this instance's setup "
            "docs) from a terminal, with cwd set to the instance root, first."
        )
    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    if not creds.valid:
        raise AuthError(
            "Cached Google credentials are invalid/revoked. Re-run "
            "the plugin's google_auth_setup.py from a terminal, with cwd "
            "set to the instance root."
        )
    return creds


def _gmail():
    return build("gmail", "v1", credentials=_credentials())


def _calendar():
    return build("calendar", "v3", credentials=_credentials())


def _drive():
    return build("drive", "v3", credentials=_credentials())


def _tasks():
    return build("tasks", "v1", credentials=_credentials())


def _safe_scratch_path(rel_path: str) -> str:
    resolved = os.path.abspath(os.path.join(SCRATCH_DIR, rel_path))
    if os.path.commonpath([resolved, SCRATCH_DIR]) != SCRATCH_DIR:
        raise ValueError("path must stay inside scratch/")
    return resolved


def _truncate(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[...truncated, {len(text) - limit} more chars omitted]"


def _call(fn, *args, **kwargs) -> Any:
    try:
        return fn(*args, **kwargs)
    except AuthError as e:
        return f"ERROR: {e}"
    except HttpError as e:
        return f"ERROR: Google API returned {e.resp.status}: {e._get_reason()}"
    except Exception as e:  # noqa: BLE001
        return f"ERROR: {type(e).__name__}: {e}"


# ---------------------------------------------------------------------------
# Gmail
# ---------------------------------------------------------------------------

def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _label_ids(service, names_or_ids: list[str]) -> list[str]:
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    by_name = {l["name"].lower(): l["id"] for l in labels}
    by_id = {l["id"] for l in labels}
    out = []
    for item in names_or_ids:
        if item in by_id:
            out.append(item)
        elif item.lower() in by_name:
            out.append(by_name[item.lower()])
        else:
            raise ValueError(f"unknown Gmail label: {item}")
    return out


@mcp.tool()
def gmail_search(query: str, max_results: int = 10) -> Any:
    """Search Gmail using Gmail search syntax (e.g. "from:x is:unread newer_than:7d").

    Returns up to max_results (capped at 50) messages with id, from, subject,
    date, and snippet.
    """
    def run():
        service = _gmail()
        max_n = min(max_results, 50)
        resp = service.users().messages().list(userId="me", q=query, maxResults=max_n).execute()
        out = []
        for m in resp.get("messages", []):
            msg = service.users().messages().get(
                userId="me", id=m["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
            headers = msg.get("payload", {}).get("headers", [])
            out.append({
                "id": msg["id"],
                "from": _header(headers, "From"),
                "subject": _header(headers, "Subject"),
                "date": _header(headers, "Date"),
                "snippet": msg.get("snippet", ""),
                "labels": msg.get("labelIds", []),
            })
        return out
    return _call(run)


class _HTMLTextExtractor(HTMLParser):
    """Strips tags/script/style, leaving only visible text - reduces the
    hidden-instruction surface (zero-size fonts, display:none divs, etc.)
    an HTML email could use to smuggle a prompt injection past a quick read."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style"):
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._chunks.append(data)

    def text(self) -> str:
        collapsed = re.sub(r"[ \t]+", " ", "".join(self._chunks))
        return re.sub(r"\n{3,}", "\n\n", collapsed).strip()


def _html_to_text(html: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(html)
    return parser.text()


def _find_part(payload: dict, mime_type: str) -> str:
    if payload.get("mimeType") == mime_type and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "replace")
    for part in payload.get("parts", []) or []:
        text = _find_part(part, mime_type)
        if text:
            return text
    return ""


def _extract_body(payload: dict) -> str:
    plain = _find_part(payload, "text/plain")
    if plain:
        return plain
    html = _find_part(payload, "text/html")
    if html:
        return _html_to_text(html)
    return ""


@mcp.tool()
def gmail_get(message_id: str, max_chars: int = 4000) -> Any:
    """Fetch a full Gmail message: headers, plain-text body, and label IDs.
    Body is truncated to max_chars (default 4000, cap 20000) - raise it only
    if you actually need more of a specific long message, don't raise it by default."""
    def run():
        service = _gmail()
        msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
        headers = msg.get("payload", {}).get("headers", [])
        return {
            "id": msg["id"],
            "thread_id": msg["threadId"],
            "from": _header(headers, "From"),
            "to": _header(headers, "To"),
            "subject": _header(headers, "Subject"),
            "date": _header(headers, "Date"),
            "labels": msg.get("labelIds", []),
            "body": _truncate(_extract_body(msg.get("payload", {})), min(max_chars, 20000)),
        }
    return _call(run)


@mcp.tool()
def gmail_send(to: str, subject: str, body: str, cc: str = "", bcc: str = "") -> Any:
    """Send a new email from the user's account. Confirm with the user before calling this."""
    def run():
        service = _gmail()
        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject
        if cc:
            message["cc"] = cc
        if bcc:
            message["bcc"] = bcc
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return {"id": sent["id"], "thread_id": sent.get("threadId", "")}
    return _call(run)


@mcp.tool()
def gmail_reply(message_id: str, body: str) -> Any:
    """Reply in-thread to an existing Gmail message. Confirm with the user before calling this."""
    def run():
        service = _gmail()
        original = service.users().messages().get(userId="me", id=message_id, format="metadata",
                                                    metadataHeaders=["Subject", "From", "Message-ID", "References"]).execute()
        headers = original.get("payload", {}).get("headers", [])
        subject = _header(headers, "Subject")
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        to = _header(headers, "From")
        msg_id_hdr = _header(headers, "Message-ID")
        references = _header(headers, "References")

        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject
        if msg_id_hdr:
            message["In-Reply-To"] = msg_id_hdr
            message["References"] = f"{references} {msg_id_hdr}".strip()
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        sent = service.users().messages().send(
            userId="me", body={"raw": raw, "threadId": original["threadId"]}
        ).execute()
        return {"id": sent["id"], "thread_id": sent.get("threadId", "")}
    return _call(run)


@mcp.tool()
def gmail_archive(message_id: str) -> Any:
    """Archive a Gmail message (remove it from the inbox, keep it elsewhere)."""
    def run():
        service = _gmail()
        service.users().messages().modify(
            userId="me", id=message_id, body={"removeLabelIds": ["INBOX"]}
        ).execute()
        return {"archived": message_id}
    return _call(run)


@mcp.tool()
def gmail_trash(message_id: str) -> Any:
    """Move a Gmail message to Trash (recoverable for ~30 days, not permanent)."""
    def run():
        service = _gmail()
        service.users().messages().trash(userId="me", id=message_id).execute()
        return {"trashed": message_id}
    return _call(run)


@mcp.tool()
def gmail_label(message_id: str, add_labels: list[str] | None = None, remove_labels: list[str] | None = None) -> Any:
    """Add/remove Gmail labels on a message. Labels may be given by name or ID."""
    def run():
        service = _gmail()
        body = {}
        if add_labels:
            body["addLabelIds"] = _label_ids(service, add_labels)
        if remove_labels:
            body["removeLabelIds"] = _label_ids(service, remove_labels)
        service.users().messages().modify(userId="me", id=message_id, body=body).execute()
        return {"updated": message_id, **body}
    return _call(run)


@mcp.tool()
def gmail_list_labels() -> Any:
    """List all Gmail labels (system + user-created) with their IDs."""
    def run():
        service = _gmail()
        labels = service.users().labels().list(userId="me").execute().get("labels", [])
        return [{"id": l["id"], "name": l["name"]} for l in labels]
    return _call(run)


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------

@mcp.tool()
def calendar_list_calendars() -> Any:
    """List calendars the user has access to (id, summary, primary flag)."""
    def run():
        service = _calendar()
        items = service.calendarList().list().execute().get("items", [])
        return [{"id": c["id"], "summary": c.get("summary", ""), "primary": c.get("primary", False)} for c in items]
    return _call(run)


@mcp.tool()
def calendar_list_events(calendar_id: str = "primary", time_min: str = "", time_max: str = "",
                          query: str = "", max_results: int = 10) -> Any:
    """List upcoming events. time_min/time_max are RFC3339 timestamps (e.g. 2026-07-11T00:00:00Z);
    omit time_min for "from now". Set time_max (e.g. end of this week/month) whenever you don't
    need an open-ended future scan - it's cheaper and it's usually what's actually wanted."""
    def run():
        service = _calendar()
        kwargs = {"calendarId": calendar_id, "singleEvents": True, "orderBy": "startTime",
                  "maxResults": min(max_results, 50),
                  "timeMin": time_min or datetime.now(timezone.utc).isoformat()}
        if time_max:
            kwargs["timeMax"] = time_max
        if query:
            kwargs["q"] = query
        items = service.events().list(**kwargs).execute().get("items", [])
        return [{
            "id": e["id"],
            "summary": e.get("summary", ""),
            "start": e.get("start", {}),
            "end": e.get("end", {}),
            "attendees": [a.get("email") for a in e.get("attendees", [])],
            "location": e.get("location", ""),
        } for e in items]
    return _call(run)


@mcp.tool()
def calendar_get_event(event_id: str, calendar_id: str = "primary") -> Any:
    """Fetch details of one calendar event (curated fields, not the raw API object -
    the raw object can carry large recurrence/conference-data blobs you don't need)."""
    def run():
        service = _calendar()
        e = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
        return {
            "id": e["id"],
            "summary": e.get("summary", ""),
            "description": _truncate(e.get("description", ""), 2000),
            "start": e.get("start", {}),
            "end": e.get("end", {}),
            "location": e.get("location", ""),
            "attendees": [a.get("email") for a in e.get("attendees", [])],
            "status": e.get("status", ""),
            "html_link": e.get("htmlLink", ""),
        }
    return _call(run)


def _time_field(value: str) -> dict:
    return {"dateTime": value} if "T" in value else {"date": value}


@mcp.tool()
def calendar_create_event(summary: str, start: str, end: str, calendar_id: str = "primary",
                           description: str = "", location: str = "", attendees: list[str] | None = None) -> Any:
    """Create a calendar event. start/end are ISO8601 (date "2026-07-11" for all-day, or
    datetime with offset "2026-07-11T09:00:00-04:00"). If attendees is non-empty, confirm
    with the user first - they'll be notified."""
    def run():
        service = _calendar()
        body = {
            "summary": summary,
            "start": _time_field(start),
            "end": _time_field(end),
        }
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        if attendees:
            body["attendees"] = [{"email": a} for a in attendees]
        return service.events().insert(calendarId=calendar_id, body=body).execute()
    return _call(run)


@mcp.tool()
def calendar_update_event(event_id: str, calendar_id: str = "primary", summary: str = "",
                           start: str = "", end: str = "", description: str = "",
                           location: str = "") -> Any:
    """Patch fields on an existing event; leave a field blank to keep it unchanged."""
    def run():
        service = _calendar()
        body = {}
        if summary:
            body["summary"] = summary
        if start:
            body["start"] = _time_field(start)
        if end:
            body["end"] = _time_field(end)
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        return service.events().patch(calendarId=calendar_id, eventId=event_id, body=body).execute()
    return _call(run)


@mcp.tool()
def calendar_delete_event(event_id: str, calendar_id: str = "primary") -> Any:
    """Delete a calendar event. If it has attendees, confirm with the user first - they'll be notified of cancellation."""
    def run():
        service = _calendar()
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        return {"deleted": event_id}
    return _call(run)


# ---------------------------------------------------------------------------
# Drive
# ---------------------------------------------------------------------------

@mcp.tool()
def drive_search(query: str = "", max_results: int = 10) -> Any:
    """Search Drive using Drive query syntax (e.g. "name contains 'invoice'"). Empty query lists
    recent files - prefer a real query when you know roughly what you're after, it's cheaper and
    more precise than paging through "recent files" and filtering yourself."""
    def run():
        service = _drive()
        resp = service.files().list(
            q=query or None, pageSize=min(max_results, 50),
            fields="files(id,name,mimeType,modifiedTime,size,parents)",
        ).execute()
        return resp.get("files", [])
    return _call(run)


@mcp.tool()
def drive_get_metadata(file_id: str) -> Any:
    """Fetch metadata for one Drive file/folder."""
    def run():
        service = _drive()
        return service.files().get(
            fileId=file_id, fields="id,name,mimeType,modifiedTime,size,parents,webViewLink"
        ).execute()
    return _call(run)


_EXPORT_MIME = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}


@mcp.tool()
def drive_download(file_id: str, dest_relpath: str) -> Any:
    """Download a Drive file to scratch/<dest_relpath> (path is confined to scratch/).
    Google-native docs (Docs/Sheets/Slides) are exported as text/CSV."""
    def run():
        service = _drive()
        meta = service.files().get(fileId=file_id, fields="mimeType,name").execute()
        mime = meta["mimeType"]
        dest = _safe_scratch_path(dest_relpath)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if mime in _EXPORT_MIME:
            data = service.files().export(fileId=file_id, mimeType=_EXPORT_MIME[mime]).execute()
        else:
            data = service.files().get_media(fileId=file_id).execute()
        with open(dest, "wb") as f:
            f.write(data)
        return {"saved_to": dest, "name": meta["name"], "mime_type": mime}
    return _call(run)


@mcp.tool()
def drive_upload(local_relpath: str, name: str = "", parent_folder_id: str = "") -> Any:
    """Upload a file from scratch/<local_relpath> to Drive (path confined to scratch/)."""
    def run():
        service = _drive()
        src = _safe_scratch_path(local_relpath)
        if not os.path.exists(src):
            raise FileNotFoundError(f"scratch/{local_relpath} does not exist")
        body = {"name": name or os.path.basename(src)}
        if parent_folder_id:
            body["parents"] = [parent_folder_id]
        media = MediaFileUpload(src, resumable=False)
        return service.files().create(body=body, media_body=media, fields="id,name,webViewLink").execute()
    return _call(run)


@mcp.tool()
def drive_create_folder(name: str, parent_folder_id: str = "") -> Any:
    """Create a Drive folder, optionally nested under parent_folder_id."""
    def run():
        service = _drive()
        body = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
        if parent_folder_id:
            body["parents"] = [parent_folder_id]
        return service.files().create(body=body, fields="id,name").execute()
    return _call(run)


@mcp.tool()
def drive_move(file_id: str, new_parent_id: str) -> Any:
    """Move a Drive file/folder to a new parent folder."""
    def run():
        service = _drive()
        current = service.files().get(fileId=file_id, fields="parents").execute()
        old_parents = ",".join(current.get("parents", []))
        return service.files().update(
            fileId=file_id, addParents=new_parent_id, removeParents=old_parents, fields="id,parents"
        ).execute()
    return _call(run)


@mcp.tool()
def drive_delete(file_id: str) -> Any:
    """Move a Drive file/folder to Trash (recoverable, not permanent)."""
    def run():
        service = _drive()
        service.files().update(fileId=file_id, body={"trashed": True}).execute()
        return {"trashed": file_id}
    return _call(run)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@mcp.tool()
def tasks_list_lists() -> Any:
    """List the user's Google Tasks lists (id, title)."""
    def run():
        service = _tasks()
        items = service.tasklists().list().execute().get("items", [])
        return [{"id": t["id"], "title": t.get("title", "")} for t in items]
    return _call(run)


@mcp.tool()
def tasks_list(task_list_id: str = "@default", show_completed: bool = False, max_results: int = 20) -> Any:
    """List tasks in a task list (default: the user's default list). Excludes completed
    tasks unless show_completed=True. due (if set) is RFC3339; status is
    "needsAction" or "completed"."""
    def run():
        service = _tasks()
        resp = service.tasks().list(
            tasklist=task_list_id, showCompleted=show_completed,
            showHidden=show_completed, maxResults=min(max_results, 50),
        ).execute()
        return [{
            "id": t["id"],
            "title": t.get("title", ""),
            "notes": t.get("notes", ""),
            "due": t.get("due", ""),
            "status": t.get("status", ""),
        } for t in resp.get("items", [])]
    return _call(run)


@mcp.tool()
def tasks_create(title: str, task_list_id: str = "@default", notes: str = "", due: str = "") -> Any:
    """Create a task. due (if given) is a date, e.g. "2026-07-11" (Tasks API
    ignores the time-of-day component even if you pass one)."""
    def run():
        service = _tasks()
        body: dict[str, Any] = {"title": title}
        if notes:
            body["notes"] = notes
        if due:
            due_val = due if "T" in due else f"{due}T00:00:00.000Z"
            body["due"] = due_val
        return service.tasks().insert(tasklist=task_list_id, body=body).execute()
    return _call(run)


@mcp.tool()
def tasks_complete(task_id: str, task_list_id: str = "@default") -> Any:
    """Mark a task as completed."""
    def run():
        service = _tasks()
        return service.tasks().patch(
            tasklist=task_list_id, task=task_id, body={"status": "completed"}
        ).execute()
    return _call(run)


if __name__ == "__main__":
    mcp.run()
