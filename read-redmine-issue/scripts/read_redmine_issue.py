#!/usr/bin/env python3
"""Fetch a Redmine issue, its history, related issues, and attachment contents."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import mimetypes
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


TEXT_EXTENSIONS = {
    ".txt", ".log", ".md", ".rst", ".csv", ".tsv", ".json", ".jsonl",
    ".xml", ".html", ".htm", ".css", ".js", ".mjs", ".cjs", ".ts",
    ".tsx", ".jsx", ".java", ".kt", ".kts", ".c", ".h", ".cc", ".cpp",
    ".hpp", ".py", ".rb", ".go", ".rs", ".php", ".sh", ".bash", ".zsh",
    ".fish", ".ps1", ".bat", ".cmd", ".ini", ".cfg", ".conf", ".properties",
    ".gradle", ".mk", ".cmake", ".yaml", ".yml", ".toml", ".sql", ".proto",
    ".aidl", ".manifest", ".gitignore", ".dockerfile",
}
OFFICE_ZIP_PREFIXES = {
    ".docx": ("word/",),
    ".pptx": ("ppt/slides/", "ppt/notesSlides/", "ppt/comments/"),
    ".xlsx": ("xl/",),
}
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download and render all accessible content for one Redmine issue."
    )
    parser.add_argument("issue", help="Full issue URL or numeric issue ID")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Configuration file (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--base-url",
        help="Override redmine.base_url from config; required for numeric ISSUE when config is blank",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Destination directory; defaults to ./redmine-issue-<id>",
    )
    parser.add_argument(
        "--api-key-file",
        type=Path,
        help="Read the Redmine API key from this file instead of the environment",
    )
    parser.add_argument(
        "--api-key-stdin",
        action="store_true",
        help="Read one API-key line from standard input without placing it in the command",
    )
    parser.add_argument(
        "--api-key-env",
        default="REDMINE_API_KEY",
        help="Environment variable containing the API key (default: REDMINE_API_KEY)",
    )
    parser.add_argument("--no-related", action="store_true", help="Do not fetch related issue summaries")
    parser.add_argument("--no-attachments", action="store_true", help="Do not download attachments")
    parser.add_argument("--no-extract", action="store_true", help="Download attachments without extracting text")
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


def resolve_issue(value: str, base_url: str | None) -> tuple[str, int]:
    value = value.strip()
    if value.isdigit():
        if not base_url:
            raise ValueError(
                "set redmine.base_url in config.toml or pass --base-url when ISSUE is numeric"
            )
        return base_url.rstrip("/"), int(value)

    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("ISSUE must be a Redmine issue URL or numeric ID")
    match = re.search(r"/issues/(\d+)(?:\.json)?(?:/)?$", parsed.path)
    if not match:
        raise ValueError("Could not find /issues/<id> in the supplied URL")
    prefix = parsed.path[: match.start()].rstrip("/")
    root = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, prefix, "", "", ""))
    return root.rstrip("/"), int(match.group(1))


def load_config(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise ValueError(f"configuration file not found: {path}")
    try:
        with path.open("rb") as stream:
            payload = tomllib.load(stream)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid TOML in {path}: {exc}") from exc
    redmine = payload.get("redmine", {})
    if not isinstance(redmine, dict):
        raise ValueError(f"[redmine] must be a TOML table in {path}")
    values: dict[str, str] = {}
    for name in ("base_url", "api_key"):
        value = redmine.get(name, "")
        if not isinstance(value, str):
            raise ValueError(f"redmine.{name} must be a quoted string in {path}")
        values[name] = value.strip()
    return values


def load_api_key(args: argparse.Namespace, config: dict[str, str]) -> str | None:
    if args.api_key_stdin:
        if sys.stdin.isatty():
            key = getpass.getpass("Redmine API key: ").strip()
        else:
            key = sys.stdin.readline().strip()
    elif args.api_key_file:
        key = args.api_key_file.read_text(encoding="utf-8").strip()
    else:
        key = os.environ.get(args.api_key_env, "").strip() or config.get("api_key", "")
    return key or None


class RedmineClient:
    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        timeout: int,
        retries: int,
        insecure: bool,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.retries = max(1, retries)
        self.ssl_context = ssl._create_unverified_context() if insecure else None

    def _request(self, url: str) -> bytes:
        url = urllib.parse.urljoin(f"{self.base_url}/", url)
        headers = {"Accept": "application/json", "User-Agent": "read-redmine-issue/1.0"}
        base_origin = urllib.parse.urlparse(self.base_url)[:2]
        target_origin = urllib.parse.urlparse(url)[:2]
        if self.api_key and target_origin == base_origin:
            headers["X-Redmine-API-Key"] = self.api_key
        request = urllib.request.Request(url, headers=headers)
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                with urllib.request.urlopen(
                    request, timeout=self.timeout, context=self.ssl_context
                ) as response:
                    return response.read()
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

    def get_issue(self, issue_id: int, include_all: bool = True) -> dict[str, Any]:
        query = ""
        if include_all:
            query = "?" + urllib.parse.urlencode(
                {"include": "journals,attachments,relations,watchers,children"}
            )
        url = f"{self.base_url}/issues/{issue_id}.json{query}"
        payload = json.loads(self._request(url).decode("utf-8"))
        return payload["issue"]

    def download(self, url: str) -> bytes:
        return self._request(url)


def safe_filename(value: str, fallback: str) -> str:
    name = Path(value).name.replace("\x00", "").strip()
    name = re.sub(r"[\\/:*?\"<>|]", "_", name)
    return name or fallback


def write_private(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8")
    else:
        path.write_bytes(data)
    path.chmod(0o600)


def decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def xml_visible_text(data: bytes) -> str:
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return decode_text(data)
    output: list[str] = []
    for node in root.iter():
        name = local_name(node.tag)
        if name in {"t", "v", "f"} and node.text:
            output.append(node.text)
        elif name in {"tab"}:
            output.append("\t")
        elif name in {"br", "cr", "p", "row", "tr"}:
            output.append("\n")
    text = "".join(output)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def is_probably_text(name: str, mime_type: str | None = None) -> bool:
    suffix = Path(name).suffix.lower()
    base = Path(name).name.lower()
    if mime_type and (mime_type.startswith("text/") or mime_type in {"application/json", "application/xml"}):
        return True
    return suffix in TEXT_EXTENSIONS or base in {
        "makefile", "dockerfile", "readme", "license", "notice", "authors", "changelog"
    }


def extract_office_zip(path: Path, suffix: str) -> tuple[str, list[str]]:
    prefixes = OFFICE_ZIP_PREFIXES[suffix]
    sections: list[str] = []
    warnings: list[str] = []
    with zipfile.ZipFile(path) as archive:
        names = sorted(
            name for name in archive.namelist()
            if name.endswith(".xml") and any(name.startswith(prefix) for prefix in prefixes)
        )
        for name in names:
            try:
                content = xml_visible_text(archive.read(name))
            except Exception as exc:  # continue through partially damaged Office files
                warnings.append(f"Could not read {name}: {exc}")
                continue
            if content:
                sections.append(f"## {name}\n\n{content}")
    if not sections:
        warnings.append("No readable OOXML text was found")
    return "\n\n".join(sections), warnings


def extract_generic_zip(
    path: Path, max_member_bytes: int, max_total_bytes: int
) -> tuple[str, list[str]]:
    sections: list[str] = []
    warnings: list[str] = []
    inspected = 0
    with zipfile.ZipFile(path) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        listing = "\n".join(
            f"- {info.filename} ({info.file_size} bytes)" for info in infos
        )
        sections.append(f"# Archive listing\n\n{listing}")
        for info in infos:
            if info.flag_bits & 0x1:
                warnings.append(f"Skipped encrypted member: {info.filename}")
                continue
            if info.file_size > max_member_bytes:
                warnings.append(
                    f"Skipped oversized member: {info.filename} ({info.file_size} bytes)"
                )
                continue
            if inspected + info.file_size > max_total_bytes:
                warnings.append("Stopped archive inspection at the configured total byte limit")
                break
            inspected += info.file_size
            mime_type, _ = mimetypes.guess_type(info.filename)
            if not is_probably_text(info.filename, mime_type):
                continue
            try:
                data = archive.read(info)
            except Exception as exc:
                warnings.append(f"Could not read {info.filename}: {exc}")
                continue
            sections.append(f"# {info.filename}\n\n{decode_text(data)}")
    return "\n\n".join(sections), warnings


def command_output(command: list[str], timeout: int) -> tuple[str, str | None]:
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", str(exc)
    text = decode_text(result.stdout)
    if result.returncode != 0:
        error = decode_text(result.stderr).strip() or f"exit code {result.returncode}"
        return text, error
    return text, None


def extract_pdf(path: Path, timeout: int) -> tuple[str, list[str]]:
    if shutil.which("pdftotext"):
        text, error = command_output(["pdftotext", "-layout", str(path), "-"], timeout)
        return text, [error] if error else []
    vendor_dir = Path(__file__).resolve().parent / "vendor"
    if vendor_dir.is_dir():
        for wheel in sorted(vendor_dir.glob("pypdf-*.whl"), reverse=True):
            sys.path.insert(0, str(wheel))
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        return "", ["PDF text extractor unavailable (install pypdf or pdftotext)"]
    try:
        reader = PdfReader(str(path))
        sections = [f"# Page {index}\n\n{page.extract_text() or ''}" for index, page in enumerate(reader.pages, 1)]
        return "\n\n".join(sections), []
    except Exception as exc:
        return "", [f"PDF extraction failed: {exc}"]


def extract_image(path: Path, timeout: int) -> tuple[str, list[str]]:
    if not shutil.which("tesseract"):
        return "", ["OCR unavailable; inspect the original image visually"]
    text, error = command_output(["tesseract", str(path), "stdout"], timeout)
    return text, [error] if error else []


def extract_with_libreoffice(path: Path, timeout: int) -> tuple[str, list[str]]:
    executable = shutil.which("libreoffice") or shutil.which("soffice")
    if not executable:
        strings_tool = shutil.which("strings")
        if not strings_tool:
            return "", ["LibreOffice unavailable; inspect the original file manually"]
        unicode_text, unicode_error = command_output(
            [strings_tool, "-el", "-n", "4", str(path)], timeout
        )
        ascii_text, ascii_error = command_output(
            [strings_tool, "-a", "-n", "4", str(path)], timeout
        )
        sections = []
        if unicode_text.strip():
            sections.append(f"# UTF-16LE strings\n\n{unicode_text.strip()}")
        if ascii_text.strip():
            sections.append(f"# ASCII strings\n\n{ascii_text.strip()}")
        errors = [item for item in (unicode_error, ascii_error) if item]
        if sections:
            errors.append(
                "LibreOffice unavailable; heuristic strings were extracted and may omit text or layout"
            )
            return "\n\n".join(sections), errors
        return "", errors or ["LibreOffice unavailable; inspect the original file manually"]
    with tempfile.TemporaryDirectory(prefix="redmine-office-") as temp_dir:
        _, error = command_output(
            [executable, "--headless", "--convert-to", "txt:Text", "--outdir", temp_dir, str(path)],
            timeout,
        )
        candidates = list(Path(temp_dir).glob("*.txt"))
        if not candidates:
            return "", [error or "LibreOffice did not produce a text file"]
        return decode_text(candidates[0].read_bytes()), [error] if error else []


def extract_attachment(
    path: Path,
    mime_type: str | None,
    timeout: int,
    max_member_bytes: int,
    max_total_bytes: int,
) -> tuple[str, list[str]]:
    suffix = path.suffix.lower()
    if is_probably_text(path.name, mime_type):
        return decode_text(path.read_bytes()), []
    if suffix in OFFICE_ZIP_PREFIXES and zipfile.is_zipfile(path):
        return extract_office_zip(path, suffix)
    if suffix in {".zip", ".jar", ".aar", ".apk"} and zipfile.is_zipfile(path):
        return extract_generic_zip(path, max_member_bytes, max_total_bytes)
    if suffix == ".pdf" or mime_type == "application/pdf":
        return extract_pdf(path, timeout)
    if (mime_type or "").startswith("image/") or suffix in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}:
        return extract_image(path, timeout)
    if suffix in {".wps", ".doc", ".xls", ".ppt", ".odt", ".ods", ".odp"}:
        return extract_with_libreoffice(path, timeout)
    if zipfile.is_zipfile(path):
        return extract_generic_zip(path, max_member_bytes, max_total_bytes)
    return "", ["Unsupported binary format; inspect the downloaded original manually"]


def md(value: Any) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, list):
        return ", ".join(md(item) for item in value)
    if isinstance(value, dict):
        return str(value.get("name") or value.get("id") or json.dumps(value, ensure_ascii=False))
    return str(value).replace("|", "\\|").replace("\r\n", "\n")


def relation_ids(issue: dict[str, Any]) -> list[int]:
    current = int(issue["id"])
    ids: set[int] = set()
    for relation in issue.get("relations", []):
        left = int(relation.get("issue_id", 0) or 0)
        right = int(relation.get("issue_to_id", 0) or 0)
        other = right if left == current else left
        if other:
            ids.add(other)
    for child in issue.get("children", []):
        if child.get("id"):
            ids.add(int(child["id"]))
    parent = issue.get("parent")
    if parent and parent.get("id"):
        ids.add(int(parent["id"]))
    return sorted(ids)


def render_report(
    issue: dict[str, Any],
    related: dict[int, dict[str, Any]],
    attachment_results: list[dict[str, Any]],
    fetch_warnings: list[str],
) -> str:
    fields = [
        ("Project", issue.get("project")), ("Tracker", issue.get("tracker")),
        ("Status", issue.get("status")), ("Priority", issue.get("priority")),
        ("Author", issue.get("author")), ("Assignee", issue.get("assigned_to")),
        ("Start date", issue.get("start_date")), ("Due date", issue.get("due_date")),
        ("Progress", f"{issue.get('done_ratio', 0)}%"),
        ("Created", issue.get("created_on")), ("Updated", issue.get("updated_on")),
        ("Closed", issue.get("closed_on")),
    ]
    lines = [
        f"# Redmine #{issue['id']}: {issue.get('subject', '')}", "",
        "## Metadata", "", "| Field | Value |", "|---|---|",
    ]
    lines.extend(f"| {name} | {md(value)} |" for name, value in fields)
    lines.extend(["", "## Description", "", md(issue.get("description")), ""])

    custom_fields = issue.get("custom_fields", [])
    if custom_fields:
        lines.extend(["## Custom fields", "", "| Field | Value |", "|---|---|"])
        lines.extend(f"| {md(item.get('name'))} | {md(item.get('value'))} |" for item in custom_fields)
        lines.append("")

    watchers = issue.get("watchers", [])
    if watchers:
        lines.extend(["## Watchers", "", *[f"- {md(item)}" for item in watchers], ""])

    relations = issue.get("relations", [])
    if relations or related:
        lines.extend(["## Relations and linked issues", ""])
        for item in relations:
            left = int(item.get("issue_id", 0) or 0)
            right = int(item.get("issue_to_id", 0) or 0)
            other = right if left == int(issue["id"]) else left
            linked = related.get(other, {})
            label = linked.get("subject", "details unavailable")
            status = md(linked.get("status"))
            lines.append(f"- #{other} [{md(item.get('relation_type'))}] {label} — {status}")
        for child in issue.get("children", []):
            linked = related.get(int(child["id"]), child)
            lines.append(f"- Child #{child['id']}: {linked.get('subject', '')} — {md(linked.get('status'))}")
        if issue.get("parent"):
            parent = issue["parent"]
            linked = related.get(int(parent["id"]), parent)
            lines.append(f"- Parent #{parent['id']}: {linked.get('subject', '')} — {md(linked.get('status'))}")
        lines.append("")

    lines.extend(["## Attachments", ""])
    if not attachment_results:
        lines.extend(["No attachments downloaded.", ""])
    for result in attachment_results:
        attachment = result["attachment"]
        lines.extend([
            f"### {attachment.get('filename', '')}", "",
            f"- ID: {attachment.get('id')}",
            f"- Type: {attachment.get('content_type') or 'unknown'}",
            f"- Size: {attachment.get('filesize', 0)} bytes",
            f"- Author: {md(attachment.get('author'))}",
            f"- Created: {attachment.get('created_on', '')}",
            f"- Original: `{result.get('relative_path', 'not downloaded')}`",
            f"- SHA-256: `{result.get('sha256', 'unavailable')}`",
        ])
        if result.get("extracted_path"):
            lines.append(f"- Extracted text: `{result['extracted_path']}`")
        for warning in result.get("warnings", []):
            lines.append(f"- Warning: {warning}")
        lines.append("")

    field_names = {str(item.get("id")): item.get("name") for item in custom_fields}
    lines.extend(["## History", ""])
    for journal in issue.get("journals", []):
        lines.extend([
            f"### {journal.get('created_on', '')} — {md(journal.get('user'))}", "",
        ])
        notes = journal.get("notes")
        if notes:
            lines.extend([str(notes).replace("\r\n", "\n"), ""])
        for detail in journal.get("details", []):
            name = str(detail.get("name", ""))
            if detail.get("property") == "cf":
                name = field_names.get(name, f"custom field {name}")
            lines.append(
                f"- Changed {detail.get('property')} `{name}`: "
                f"{md(detail.get('old_value'))} → {md(detail.get('new_value'))}"
            )
        if not notes and not journal.get("details"):
            lines.append("- No note or field change content")
        lines.append("")

    if fetch_warnings:
        lines.extend(["## Retrieval warnings", "", *[f"- {warning}" for warning in fetch_warnings], ""])
    lines.extend([
        "## Source files", "",
        "- `issue.json`: complete API response for the main issue",
        "- `related/`: one-level linked issue API responses",
        "- `attachments/`: original downloaded attachments",
        "- `extracted/`: best-effort text extracted from attachments",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    if args.api_key_stdin and args.api_key_file:
        print("error: use only one of --api-key-stdin and --api-key-file", file=sys.stderr)
        return 2
    config_path = args.config.expanduser().resolve()
    try:
        config = load_config(config_path)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        base_url, issue_id = resolve_issue(
            args.issue, args.base_url or config.get("base_url") or None
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    output_dir = (args.output_dir or Path.cwd() / f"redmine-issue-{issue_id}").resolve()
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_dir.chmod(0o700)
    client = RedmineClient(
        base_url=base_url,
        api_key=load_api_key(args, config),
        timeout=args.timeout,
        retries=args.retries,
        insecure=args.insecure,
    )

    try:
        issue = client.get_issue(issue_id, include_all=True)
    except Exception as exc:
        print(f"error: unable to retrieve issue #{issue_id}: {exc}", file=sys.stderr)
        return 1

    write_private(output_dir / "issue.json", json.dumps(issue, ensure_ascii=False, indent=2))

    warnings: list[str] = []
    related: dict[int, dict[str, Any]] = {}
    if not args.no_related:
        for linked_id in relation_ids(issue):
            try:
                linked = client.get_issue(linked_id, include_all=False)
                related[linked_id] = linked
                write_private(
                    output_dir / "related" / f"{linked_id}.json",
                    json.dumps(linked, ensure_ascii=False, indent=2),
                )
            except Exception as exc:
                warnings.append(f"Could not retrieve linked issue #{linked_id}: {exc}")

    attachment_results: list[dict[str, Any]] = []
    if not args.no_attachments:
        used_names: set[str] = set()
        for attachment in issue.get("attachments", []):
            attachment_id = int(attachment.get("id", 0) or 0)
            original_name = safe_filename(
                str(attachment.get("filename", "")), f"attachment-{attachment_id}"
            )
            filename = original_name
            if filename in used_names:
                filename = f"{attachment_id}-{original_name}"
            used_names.add(filename)
            destination = output_dir / "attachments" / filename
            result: dict[str, Any] = {
                "attachment": attachment,
                "relative_path": str(destination.relative_to(output_dir)),
                "warnings": [],
            }
            try:
                data = client.download(str(attachment["content_url"]))
                write_private(destination, data)
                result["sha256"] = hashlib.sha256(data).hexdigest()
            except Exception as exc:
                result["warnings"].append(f"Download failed: {exc}")
                attachment_results.append(result)
                continue

            if not args.no_extract:
                extracted, extraction_warnings = extract_attachment(
                    destination,
                    attachment.get("content_type"),
                    args.timeout,
                    args.max_archive_member_bytes,
                    args.max_archive_total_bytes,
                )
                result["warnings"].extend(item for item in extraction_warnings if item)
                if extracted.strip():
                    extracted_path = output_dir / "extracted" / f"{filename}.txt"
                    write_private(extracted_path, extracted)
                    result["extracted_path"] = str(extracted_path.relative_to(output_dir))
            attachment_results.append(result)

    manifest = {
        "issue_id": issue_id,
        "base_url": base_url,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "related_issue_ids": sorted(related),
        "attachments": attachment_results,
        "warnings": warnings,
    }
    write_private(output_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    write_private(output_dir / "report.md", render_report(issue, related, attachment_results, warnings))

    print(f"Issue #{issue_id} saved to {output_dir}")
    print(f"Config: {config_path}")
    print(f"Report: {output_dir / 'report.md'}")
    print(f"Attachments downloaded: {sum(1 for item in attachment_results if item.get('sha256'))}")
    print(f"Attachments with extracted text: {sum(1 for item in attachment_results if item.get('extracted_path'))}")
    if warnings or any(item.get("warnings") for item in attachment_results):
        print("Some content requires manual inspection; see report.md and manifest.json warnings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
