#!/usr/bin/env python3
"""Retrieve a JIRA issue, history, related metadata, and attachment contents."""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import mimetypes
import os
import re
import ssl
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from attachment_utils import extract_attachment


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"
ISSUE_KEY_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*-\d+")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download and render all accessible content for one JIRA issue."
    )
    parser.add_argument("issue", help="JIRA issue key or full browse/API URL")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Configuration file (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument("--base-url", help="Override jira.base_url from config")
    parser.add_argument(
        "--auth-mode",
        choices=("auto", "basic", "bearer", "none"),
        help="Override jira.auth_mode from config",
    )
    parser.add_argument("--username", help="Override jira.username from config")
    parser.add_argument(
        "--secret-file",
        type=Path,
        help="Read password/API token/PAT from a file instead of config",
    )
    parser.add_argument(
        "--secret-stdin",
        action="store_true",
        help="Read one secret line from standard input without placing it in the command",
    )
    parser.add_argument(
        "--secret-env",
        default="JIRA_API_SECRET",
        help="Environment variable containing a one-time secret (default: JIRA_API_SECRET)",
    )
    parser.add_argument("--output-dir", type=Path, help="Destination bundle directory")
    parser.add_argument("--no-related", action="store_true", help="Do not fetch linked issue summaries")
    parser.add_argument("--no-attachments", action="store_true", help="Do not download attachments")
    parser.add_argument("--no-extract", action="store_true", help="Do not extract attachment text")
    parser.add_argument("--timeout", type=int, default=60, help="Network/subprocess timeout in seconds")
    parser.add_argument("--retries", type=int, default=3, help="Network attempts per request")
    parser.add_argument(
        "--max-archive-member-bytes",
        type=int,
        default=5 * 1024 * 1024,
        help="Maximum uncompressed ZIP member size included in text output",
    )
    parser.add_argument(
        "--max-archive-total-bytes",
        type=int,
        default=50 * 1024 * 1024,
        help="Maximum total uncompressed ZIP bytes inspected",
    )
    parser.add_argument("--insecure", action="store_true", help="Disable HTTPS certificate verification")
    return parser.parse_args()


def load_config(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise ValueError(f"configuration file not found: {path}")
    try:
        with path.open("rb") as stream:
            payload = tomllib.load(stream)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid TOML in {path}: {exc}") from exc
    jira = payload.get("jira", {})
    if not isinstance(jira, dict):
        raise ValueError(f"[jira] must be a TOML table in {path}")
    result: dict[str, str] = {}
    for name in ("base_url", "auth_mode", "username", "secret"):
        value = jira.get(name, "")
        if not isinstance(value, str):
            raise ValueError(f"jira.{name} must be a quoted string in {path}")
        result[name] = value.strip()
    if result["auth_mode"] not in {"", "auto", "basic", "bearer", "none"}:
        raise ValueError("jira.auth_mode must be auto, basic, bearer, or none")
    return result


def configured_secret(args: argparse.Namespace, config: dict[str, str]) -> tuple[str, str]:
    if args.secret_stdin and args.secret_file:
        raise ValueError("use only one of --secret-stdin and --secret-file")
    if args.secret_stdin:
        if sys.stdin.isatty():
            return getpass.getpass("JIRA secret: ").strip(), "stdin"
        return sys.stdin.readline().strip(), "stdin"
    if args.secret_file:
        return args.secret_file.expanduser().read_text(encoding="utf-8").strip(), "file"
    environment = os.environ.get(args.secret_env, "").strip()
    if environment:
        return environment, "environment"
    return config.get("secret", ""), "config"


def normalize_origin(value: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme.lower(), parsed.netloc.lower()


def resolve_issue(value: str, configured_base_url: str | None) -> tuple[str, str]:
    raw = value.strip()
    if ISSUE_KEY_RE.fullmatch(raw):
        if not configured_base_url:
            raise ValueError("set jira.base_url in config.toml or pass --base-url for an issue key")
        return configured_base_url.rstrip("/"), raw.upper()

    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("ISSUE must be a JIRA issue key or full browse/API URL")
    match = re.search(
        r"/(?:browse|rest/api/(?:2|3)/issue)/([A-Za-z][A-Za-z0-9_]*-\d+)(?:/|$)",
        parsed.path,
    )
    if not match:
        match = ISSUE_KEY_RE.search(parsed.path)
    if not match:
        raise ValueError("could not find a JIRA issue key in the supplied URL")
    issue_key = (match.group(1) if match.lastindex else match.group(0)).upper()
    prefix = parsed.path[: match.start()].rstrip("/")
    if prefix.endswith("/browse"):
        prefix = prefix[:-7]
    else:
        prefix = re.sub(r"/rest/api/(?:2|3)/issue$", "", prefix)
    base_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, prefix, "", "", ""))
    return base_url.rstrip("/"), issue_key


def effective_auth_mode(requested: str, username: str, secret: str) -> str:
    mode = requested or "auto"
    if mode == "auto":
        if username and secret:
            return "basic"
        if secret:
            return "bearer"
        return "none"
    if mode == "basic" and (not username or not secret):
        raise ValueError("basic authentication requires jira.username and jira.secret")
    if mode == "bearer" and not secret:
        raise ValueError("bearer authentication requires jira.secret")
    return mode


def safe_filename(value: str, fallback: str) -> str:
    name = Path(value).name.replace("\x00", "").strip()
    name = re.sub(r"[\\/:*?\"<>|]", "_", name)
    return name or fallback


def write_private(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)
    path.chmod(0o600)


def write_json(path: Path, value: Any) -> None:
    write_private(path, json.dumps(value, ensure_ascii=False, indent=2))


class JiraClient:
    def __init__(
        self,
        base_url: str,
        auth_mode: str,
        username: str,
        secret: str,
        timeout: int,
        retries: int,
        insecure: bool,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth_mode = auth_mode
        self.username = username
        self.secret = secret
        self.timeout = max(1, timeout)
        self.retries = max(1, retries)
        self.origin = normalize_origin(self.base_url)
        self.ssl_context = ssl._create_unverified_context() if insecure else None

    def _authorization(self, target_url: str) -> str | None:
        if normalize_origin(target_url) != self.origin:
            return None
        if self.auth_mode == "basic":
            encoded = base64.b64encode(f"{self.username}:{self.secret}".encode()).decode("ascii")
            return f"Basic {encoded}"
        if self.auth_mode == "bearer":
            return f"Bearer {self.secret}"
        return None

    def request(self, url: str, accept: str = "application/json") -> tuple[bytes, str | None]:
        target = urllib.parse.urljoin(f"{self.base_url}/", url)
        headers = {"Accept": accept, "User-Agent": "read-jira-issue/1.0"}
        authorization = self._authorization(target)
        if authorization:
            headers["Authorization"] = authorization
        request = urllib.request.Request(target, headers=headers)
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                with urllib.request.urlopen(
                    request,
                    timeout=self.timeout,
                    context=self.ssl_context,
                ) as response:
                    return response.read(), response.headers.get_content_type()
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code < 500 and exc.code != 429:
                    break
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last_error = exc
            if attempt < self.retries:
                time.sleep(min(attempt, 3))
        assert last_error is not None
        raise last_error

    def get_json(self, path: str) -> Any:
        data, _ = self.request(path, "application/json")
        return json.loads(data.decode("utf-8"))

    def get_issue(self, key: str, linked: bool = False) -> dict[str, Any]:
        if linked:
            fields = ",".join(
                [
                    "summary", "status", "resolution", "issuetype", "priority",
                    "assignee", "reporter", "updated", "parent", "subtasks",
                ]
            )
            query = urllib.parse.urlencode({"fields": fields})
        else:
            query = urllib.parse.urlencode(
                {"expand": "changelog,renderedFields,names,schema"}
            )
        return self.get_json(f"rest/api/2/issue/{urllib.parse.quote(key)}?{query}")

    def get_paginated(self, path: str, candidates: tuple[str, ...]) -> tuple[list[Any], dict[str, Any]]:
        items: list[Any] = []
        start_at = 0
        page_size = 100
        last_payload: dict[str, Any] = {}
        while True:
            separator = "&" if "?" in path else "?"
            query = urllib.parse.urlencode({"startAt": start_at, "maxResults": page_size})
            payload = self.get_json(f"{path}{separator}{query}")
            if isinstance(payload, list):
                items.extend(payload)
                return items, {"total": len(items), "startAt": 0, "maxResults": len(items)}
            if not isinstance(payload, dict):
                raise ValueError(f"unexpected pagination response for {path}")
            last_payload = payload
            page: list[Any] | None = None
            for name in candidates:
                candidate = payload.get(name)
                if isinstance(candidate, list):
                    page = candidate
                    break
            if page is None:
                raise ValueError(f"response for {path} contains none of {', '.join(candidates)}")
            items.extend(page)
            total = int(payload.get("total", len(items)) or len(items))
            if not page or len(items) >= total:
                break
            next_start = int(payload.get("startAt", start_at) or start_at) + len(page)
            if next_start <= start_at:
                break
            start_at = next_start
        metadata = {
            "total": int(last_payload.get("total", len(items)) or len(items)),
            "startAt": 0,
            "maxResults": len(items),
        }
        return items, metadata


def api_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code} {exc.reason}"
    if isinstance(exc, urllib.error.URLError):
        return f"network error: {exc.reason}"
    return str(exc)


def entity_name(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        return value or "—"
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(entity_name(item) for item in value) or "—"
    if isinstance(value, dict):
        for key in ("displayName", "name", "value", "key", "id"):
            candidate = value.get(key)
            if candidate not in (None, ""):
                return str(candidate)
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def body_text(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, str):
        return value.replace("\r\n", "\n")
    return json.dumps(value, ensure_ascii=False, indent=2)


def md_cell(value: Any) -> str:
    return entity_name(value).replace("|", "\\|").replace("\r\n", "\n").replace("\n", "<br>")


def user_label(value: Any) -> str:
    if not isinstance(value, dict):
        return entity_name(value)
    display = value.get("displayName") or value.get("name") or value.get("key") or value.get("accountId")
    login = value.get("name") or value.get("key") or value.get("accountId")
    if display and login and display != login:
        return f"{display} ({login})"
    return str(display or login or "—")


def linked_issue_records(issue: dict[str, Any]) -> list[dict[str, str]]:
    current = str(issue.get("key", ""))
    fields = issue.get("fields", {}) or {}
    records: list[dict[str, str]] = []
    parent = fields.get("parent")
    if isinstance(parent, dict) and parent.get("key"):
        records.append({"key": parent["key"], "relation": "parent"})
    for subtask in fields.get("subtasks") or []:
        if isinstance(subtask, dict) and subtask.get("key"):
            records.append({"key": subtask["key"], "relation": "subtask"})
    for link in fields.get("issuelinks") or []:
        if not isinstance(link, dict):
            continue
        relation_type = link.get("type") or {}
        if isinstance(link.get("outwardIssue"), dict):
            other = link["outwardIssue"]
            relation = relation_type.get("outward") or relation_type.get("name") or "outward link"
        elif isinstance(link.get("inwardIssue"), dict):
            other = link["inwardIssue"]
            relation = relation_type.get("inward") or relation_type.get("name") or "inward link"
        else:
            continue
        if other.get("key"):
            records.append({"key": other["key"], "relation": str(relation)})
    unique: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for record in records:
        identity = (record["key"], record["relation"])
        if record["key"] != current and identity not in seen:
            seen.add(identity)
            unique.append(record)
    return unique


def attachment_metadata(attachment: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": attachment.get("id"),
        "filename": attachment.get("filename"),
        "size": attachment.get("size"),
        "mime_type": attachment.get("mimeType"),
        "author": user_label(attachment.get("author")),
        "created": attachment.get("created"),
        "content_url": attachment.get("content"),
        "thumbnail_url": attachment.get("thumbnail"),
    }


def download_attachments(
    client: JiraClient,
    issue: dict[str, Any],
    output_dir: Path,
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    used_names: set[str] = set()
    for attachment in (issue.get("fields", {}) or {}).get("attachment") or []:
        if not isinstance(attachment, dict):
            continue
        attachment_id = str(attachment.get("id") or "unknown")
        original_name = safe_filename(str(attachment.get("filename") or ""), f"attachment-{attachment_id}")
        filename = original_name
        if filename in used_names:
            filename = f"{attachment_id}-{original_name}"
        used_names.add(filename)
        destination = output_dir / "attachments" / filename
        result: dict[str, Any] = {
            "attachment": attachment_metadata(attachment),
            "relative_path": str(destination.relative_to(output_dir)),
            "warnings": [],
        }
        content_url = attachment.get("content")
        if not content_url:
            result["warnings"].append("Attachment metadata has no content URL")
            results.append(result)
            continue
        try:
            data, response_type = client.request(str(content_url), "*/*")
            write_private(destination, data)
            result["sha256"] = hashlib.sha256(data).hexdigest()
            result["downloaded_bytes"] = len(data)
            mime_type = attachment.get("mimeType") or response_type or mimetypes.guess_type(filename)[0]
            result["detected_mime_type"] = mime_type
        except Exception as exc:
            result["warnings"].append(f"Original download failed: {api_error(exc)}")
            thumbnail_url = attachment.get("thumbnail")
            if thumbnail_url:
                thumb_name = f"{attachment_id}-{Path(filename).stem}-thumbnail.jpg"
                thumb_path = output_dir / "thumbnails" / thumb_name
                try:
                    thumb_data, thumb_type = client.request(str(thumbnail_url), "image/*")
                    write_private(thumb_path, thumb_data)
                    result["thumbnail_path"] = str(thumb_path.relative_to(output_dir))
                    result["thumbnail_sha256"] = hashlib.sha256(thumb_data).hexdigest()
                    result["thumbnail_mime_type"] = thumb_type
                    result["warnings"].append("Only the server thumbnail was downloaded; original is missing")
                except Exception as thumb_exc:
                    result["warnings"].append(f"Thumbnail download failed: {api_error(thumb_exc)}")
            results.append(result)
            continue

        if not args.no_extract:
            extracted, extraction_warnings = extract_attachment(
                destination,
                result.get("detected_mime_type"),
                args.timeout,
                args.max_archive_member_bytes,
                args.max_archive_total_bytes,
            )
            result["warnings"].extend(item for item in extraction_warnings if item)
            if extracted.strip():
                extracted_path = output_dir / "extracted" / f"{filename}.txt"
                write_private(extracted_path, extracted)
                result["extracted_path"] = str(extracted_path.relative_to(output_dir))
        results.append(result)
    return results


def render_report(
    issue: dict[str, Any],
    comments: list[dict[str, Any]],
    worklogs: list[dict[str, Any]],
    changelog: list[dict[str, Any]],
    watchers: dict[str, Any] | None,
    remote_links: list[dict[str, Any]],
    related_records: list[dict[str, str]],
    related: dict[str, dict[str, Any]],
    attachment_results: list[dict[str, Any]],
    warnings: list[str],
) -> str:
    fields = issue.get("fields", {}) or {}
    status = fields.get("status") or {}
    status_category = status.get("statusCategory") if isinstance(status, dict) else None
    watches = fields.get("watches") or {}
    metadata = [
        ("Key", issue.get("key")),
        ("Project", fields.get("project")),
        ("Issue type", fields.get("issuetype")),
        ("Status", status),
        ("Status category", status_category),
        ("Resolution", fields.get("resolution")),
        ("Priority", fields.get("priority")),
        ("Assignee", user_label(fields.get("assignee"))),
        ("Reporter", user_label(fields.get("reporter"))),
        ("Creator", user_label(fields.get("creator"))),
        ("Labels", fields.get("labels")),
        ("Components", fields.get("components")),
        ("Affects versions", fields.get("versions")),
        ("Fix versions", fields.get("fixVersions")),
        ("Created", fields.get("created")),
        ("Updated", fields.get("updated")),
        ("Due date", fields.get("duedate")),
        ("Resolved", fields.get("resolutiondate")),
        ("Votes", fields.get("votes")),
        ("Watcher count", watchers.get("watchCount") if watchers else watches.get("watchCount")),
        ("Original estimate", fields.get("timeoriginalestimate")),
        ("Remaining estimate", fields.get("timeestimate")),
        ("Time spent", fields.get("timespent")),
        ("Progress", fields.get("progress")),
    ]
    lines = [
        f"# JIRA {issue.get('key', '')}: {fields.get('summary', '')}",
        "",
        "## Metadata",
        "",
        "| Field | Value |",
        "|---|---|",
    ]
    lines.extend(f"| {name} | {md_cell(value)} |" for name, value in metadata)
    lines.extend(["", "## Description", "", body_text(fields.get("description")), ""])
    rendered_fields = issue.get("renderedFields") or {}
    rendered_description = rendered_fields.get("description") if isinstance(rendered_fields, dict) else None
    if rendered_description and rendered_description != fields.get("description"):
        lines.extend(["## Rendered description", "", body_text(rendered_description), ""])
    if fields.get("environment") not in (None, ""):
        lines.extend(["## Environment", "", body_text(fields.get("environment")), ""])

    names = issue.get("names") or {}
    custom_fields = sorted(
        ((key, names.get(key) or key, value) for key, value in fields.items() if key.startswith("customfield_")),
        key=lambda item: (str(item[1]).lower(), item[0]),
    )
    if custom_fields:
        lines.extend(["## Custom fields", "", "| Field | ID | Value |", "|---|---|---|"])
        lines.extend(
            f"| {md_cell(name)} | `{field_id}` | {md_cell(value)} |"
            for field_id, name, value in custom_fields
        )
        lines.append("")

    lines.extend(["## Comments", ""])
    if not comments:
        lines.extend(["No accessible comments.", ""])
    for comment in comments:
        lines.extend(
            [
                f"### {comment.get('created', '')} — {user_label(comment.get('author'))}",
                "",
                f"- ID: {comment.get('id', '—')}",
                f"- Updated: {comment.get('updated', '—')}",
            ]
        )
        if comment.get("visibility"):
            lines.append(f"- Visibility: {entity_name(comment.get('visibility'))}")
        lines.extend(["", body_text(comment.get("body")), ""])

    lines.extend(["## Changelog", ""])
    if not changelog:
        lines.extend(["No accessible field-change history.", ""])
    for history in changelog:
        lines.extend(
            [
                f"### {history.get('created', '')} — {user_label(history.get('author'))}",
                "",
            ]
        )
        items = history.get("items") or []
        if not items:
            lines.append("- No field-change items")
        for item in items:
            field = item.get("field") or item.get("fieldId") or "unknown field"
            before = item.get("fromString") if item.get("fromString") is not None else item.get("from")
            after = item.get("toString") if item.get("toString") is not None else item.get("to")
            lines.append(f"- Changed `{field}`: {entity_name(before)} → {entity_name(after)}")
        lines.append("")

    lines.extend(["## Worklogs", ""])
    if not worklogs:
        lines.extend(["No accessible worklogs.", ""])
    for worklog in worklogs:
        lines.extend(
            [
                f"### {worklog.get('started', '')} — {user_label(worklog.get('author'))}",
                "",
                f"- ID: {worklog.get('id', '—')}",
                f"- Time spent: {worklog.get('timeSpent', '—')} ({worklog.get('timeSpentSeconds', '—')} seconds)",
                f"- Created: {worklog.get('created', '—')}",
                f"- Updated: {worklog.get('updated', '—')}",
                "",
                body_text(worklog.get("comment")),
                "",
            ]
        )

    lines.extend(["## Watchers", ""])
    watcher_users = watchers.get("watchers", []) if watchers else []
    if watcher_users:
        lines.extend(f"- {user_label(user)}" for user in watcher_users)
        lines.append("")
    else:
        count = watchers.get("watchCount") if watchers else watches.get("watchCount")
        lines.extend([f"Watcher names unavailable; count reported by JIRA: {count if count is not None else '—'}.", ""])

    lines.extend(["## Relations and linked issues", ""])
    if not related_records and not remote_links:
        lines.extend(["No accessible linked issues or remote links.", ""])
    for record in related_records:
        linked = related.get(record["key"], {})
        linked_fields = linked.get("fields", {}) if isinstance(linked, dict) else {}
        lines.append(
            f"- {record['relation']}: {record['key']} — "
            f"{linked_fields.get('summary', 'details unavailable')} — "
            f"{entity_name(linked_fields.get('status'))}"
        )
    for remote in remote_links:
        obj = remote.get("object") or {}
        lines.append(
            f"- Remote link: {obj.get('title') or remote.get('globalId') or remote.get('id')} — "
            f"{obj.get('url') or 'URL unavailable'}"
        )
    lines.append("")

    lines.extend(["## Attachments", ""])
    if not attachment_results:
        lines.extend(["No attachments downloaded or no attachment metadata was returned.", ""])
    for result in attachment_results:
        attachment = result["attachment"]
        lines.extend(
            [
                f"### {attachment.get('filename', '')}",
                "",
                f"- ID: {attachment.get('id')}",
                f"- Type: {attachment.get('mime_type') or result.get('detected_mime_type') or 'unknown'}",
                f"- Size reported: {attachment.get('size', '—')} bytes",
                f"- Author: {attachment.get('author', '—')}",
                f"- Created: {attachment.get('created', '—')}",
                f"- Original: `{result.get('relative_path', 'not downloaded')}`",
                f"- SHA-256: `{result.get('sha256', 'unavailable')}`",
            ]
        )
        if result.get("thumbnail_path"):
            lines.append(f"- Thumbnail fallback: `{result['thumbnail_path']}`")
        if result.get("extracted_path"):
            lines.append(f"- Extracted text: `{result['extracted_path']}`")
        for warning in result.get("warnings", []):
            lines.append(f"- Warning: {warning}")
        lines.append("")

    consistency: list[str] = []
    category_key = status_category.get("key") if isinstance(status_category, dict) else None
    resolution = fields.get("resolution")
    if category_key == "done" and not resolution:
        consistency.append("Status category is Done but resolution is empty.")
    if category_key and category_key != "done" and resolution:
        consistency.append("Resolution is set while status category is not Done.")
    if consistency:
        lines.extend(["## Consistency notes", "", *[f"- {item}" for item in consistency], ""])

    if warnings:
        lines.extend(["## Retrieval warnings", "", *[f"- {warning}" for warning in warnings], ""])
    lines.extend(
        [
            "## Source files",
            "",
            "- `issue.json`: main issue REST response, including field names/schema and embedded changelog",
            "- `api/`: separately retrieved comments, worklogs, watchers, changelog, and remote links",
            "- `related/`: one-level linked issue REST responses",
            "- `attachments/`: original downloaded attachments",
            "- `thumbnails/`: fallback previews only when an original download failed",
            "- `extracted/`: best-effort attachment text",
            "- `manifest.json`: coverage counts, checksums, and all warnings",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    config_path = args.config.expanduser().resolve()
    try:
        config = load_config(config_path)
        configured_base = args.base_url or config.get("base_url") or None
        base_url, issue_key = resolve_issue(args.issue, configured_base)
        secret, secret_source = configured_secret(args, config)
        username = args.username if args.username is not None else config.get("username", "")
        requested_mode = args.auth_mode or config.get("auth_mode") or "auto"
        auth_mode = effective_auth_mode(requested_mode, username, secret)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    configured_origin = normalize_origin(configured_base) if configured_base else None
    target_origin = normalize_origin(base_url)
    if secret and secret_source == "config" and configured_origin and configured_origin != target_origin:
        print(
            "error: refusing to send the configured JIRA secret to a different origin; "
            "use --secret-stdin, --secret-file, or JIRA_API_SECRET for this run",
            file=sys.stderr,
        )
        return 2

    output_dir = (args.output_dir or Path.cwd() / f"jira-issue-{issue_key}").expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_dir.chmod(0o700)
    client = JiraClient(
        base_url=base_url,
        auth_mode=auth_mode,
        username=username,
        secret=secret,
        timeout=args.timeout,
        retries=args.retries,
        insecure=args.insecure,
    )

    try:
        issue = client.get_issue(issue_key)
    except Exception as exc:
        print(f"error: unable to retrieve {issue_key}: {api_error(exc)}", file=sys.stderr)
        return 1
    write_json(output_dir / "issue.json", issue)

    warnings: list[str] = []
    fields = issue.get("fields", {}) or {}

    comments: list[dict[str, Any]] = []
    comments_meta: dict[str, Any] = {}
    try:
        comments, comments_meta = client.get_paginated(
            f"rest/api/2/issue/{urllib.parse.quote(issue_key)}/comment?expand=renderedBody",
            ("comments", "values"),
        )
    except Exception as exc:
        embedded = (fields.get("comment") or {}).get("comments", [])
        comments = embedded if isinstance(embedded, list) else []
        comments_meta = {"total": len(comments), "fallback": "embedded issue field"}
        warnings.append(f"Could not retrieve complete comments endpoint: {api_error(exc)}")
    write_json(output_dir / "api" / "comments.json", {"comments": comments, **comments_meta})

    worklogs: list[dict[str, Any]] = []
    worklogs_meta: dict[str, Any] = {}
    try:
        worklogs, worklogs_meta = client.get_paginated(
            f"rest/api/2/issue/{urllib.parse.quote(issue_key)}/worklog",
            ("worklogs", "values"),
        )
    except Exception as exc:
        embedded = (fields.get("worklog") or {}).get("worklogs", [])
        worklogs = embedded if isinstance(embedded, list) else []
        worklogs_meta = {"total": len(worklogs), "fallback": "embedded issue field"}
        warnings.append(f"Could not retrieve complete worklog endpoint: {api_error(exc)}")
    write_json(output_dir / "api" / "worklogs.json", {"worklogs": worklogs, **worklogs_meta})

    watchers: dict[str, Any] | None = None
    try:
        payload = client.get_json(f"rest/api/2/issue/{urllib.parse.quote(issue_key)}/watchers")
        if isinstance(payload, dict):
            watchers = payload
            write_json(output_dir / "api" / "watchers.json", watchers)
        else:
            warnings.append("Watchers endpoint returned an unexpected response")
    except Exception as exc:
        warnings.append(f"Could not retrieve watcher names: {api_error(exc)}")

    remote_links: list[dict[str, Any]] = []
    try:
        payload = client.get_json(f"rest/api/2/issue/{urllib.parse.quote(issue_key)}/remotelink")
        if isinstance(payload, list):
            remote_links = payload
        elif isinstance(payload, dict) and isinstance(payload.get("values"), list):
            remote_links = payload["values"]
        else:
            warnings.append("Remote links endpoint returned an unexpected response")
        write_json(output_dir / "api" / "remote-links.json", remote_links)
    except Exception as exc:
        warnings.append(f"Could not retrieve remote links: {api_error(exc)}")

    changelog_payload = issue.get("changelog") or {}
    changelog = changelog_payload.get("histories", []) if isinstance(changelog_payload, dict) else []
    changelog = changelog if isinstance(changelog, list) else []
    changelog_total = int(changelog_payload.get("total", len(changelog)) or len(changelog)) if isinstance(changelog_payload, dict) else len(changelog)
    if len(changelog) < changelog_total:
        try:
            full_changelog, changelog_meta = client.get_paginated(
                f"rest/api/2/issue/{urllib.parse.quote(issue_key)}/changelog",
                ("values", "histories"),
            )
            if full_changelog:
                changelog = full_changelog
                changelog_total = int(changelog_meta.get("total", len(changelog)))
        except Exception as exc:
            warnings.append(
                f"Embedded changelog is partial ({len(changelog)}/{changelog_total}) and the pagination endpoint failed: {api_error(exc)}"
            )
    write_json(
        output_dir / "api" / "changelog.json",
        {"histories": changelog, "total": changelog_total, "retrieved": len(changelog)},
    )
    if len(changelog) < changelog_total:
        warnings.append(f"Changelog remains incomplete: retrieved {len(changelog)} of {changelog_total}")

    related_records = linked_issue_records(issue)
    related: dict[str, dict[str, Any]] = {}
    if not args.no_related:
        for record in related_records:
            key = record["key"]
            if key in related:
                continue
            try:
                linked = client.get_issue(key, linked=True)
                related[key] = linked
                write_json(output_dir / "related" / f"{safe_filename(key, 'linked')}.json", linked)
            except Exception as exc:
                warnings.append(f"Could not retrieve linked issue {key}: {api_error(exc)}")

    attachment_results: list[dict[str, Any]] = []
    if not args.no_attachments:
        attachment_results = download_attachments(client, issue, output_dir, args)

    for result in attachment_results:
        for warning in result.get("warnings", []):
            if warning.startswith("Original download failed") or warning.startswith("Only the server thumbnail"):
                warnings.append(f"{result['attachment'].get('filename')}: {warning}")

    manifest = {
        "issue_key": issue_key,
        "issue_id": issue.get("id"),
        "base_url": base_url,
        "auth_mode": auth_mode,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "coverage": {
            "comments_retrieved": len(comments),
            "comments_total": comments_meta.get("total", len(comments)),
            "worklogs_retrieved": len(worklogs),
            "worklogs_total": worklogs_meta.get("total", len(worklogs)),
            "changelog_retrieved": len(changelog),
            "changelog_total": changelog_total,
            "watcher_names_retrieved": len(watchers.get("watchers", [])) if watchers else 0,
            "remote_links_retrieved": len(remote_links),
            "related_issues_retrieved": len(related),
            "related_issues_referenced": len({record["key"] for record in related_records}),
            "attachments_listed": len((fields.get("attachment") or [])),
            "attachments_downloaded": sum(1 for item in attachment_results if item.get("sha256")),
            "attachments_extracted": sum(1 for item in attachment_results if item.get("extracted_path")),
        },
        "related": related_records,
        "attachments": attachment_results,
        "warnings": warnings,
    }
    write_json(output_dir / "manifest.json", manifest)
    write_private(
        output_dir / "report.md",
        render_report(
            issue,
            comments,
            worklogs,
            changelog,
            watchers,
            remote_links,
            related_records,
            related,
            attachment_results,
            warnings,
        ),
    )

    print(f"JIRA {issue_key} saved to {output_dir}")
    print(f"Config: {config_path}")
    print(f"Report: {output_dir / 'report.md'}")
    print(f"Comments: {len(comments)}")
    print(f"Changelog entries: {len(changelog)}")
    print(f"Worklogs: {len(worklogs)}")
    print(f"Attachments downloaded: {sum(1 for item in attachment_results if item.get('sha256'))}")
    if warnings or any(item.get("warnings") for item in attachment_results):
        print("Some content requires review; see report.md and manifest.json warnings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
