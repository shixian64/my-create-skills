#!/usr/bin/env python3
"""Safe, best-effort text extraction helpers for JIRA attachments."""

from __future__ import annotations

import mimetypes
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
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
        elif name == "tab":
            output.append("\t")
        elif name in {"br", "cr", "p", "row", "tr"}:
            output.append("\n")
    text = "".join(output)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def is_probably_text(name: str, mime_type: str | None = None) -> bool:
    suffix = Path(name).suffix.lower()
    base = Path(name).name.lower()
    if mime_type and (
        mime_type.startswith("text/")
        or mime_type in {"application/json", "application/xml"}
    ):
        return True
    return suffix in TEXT_EXTENSIONS or base in {
        "makefile", "dockerfile", "readme", "license", "notice", "authors", "changelog",
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
            except Exception as exc:
                warnings.append(f"Could not read {name}: {exc}")
                continue
            if content:
                sections.append(f"## {name}\n\n{content}")
    if not sections:
        warnings.append("No readable OOXML text was found")
    return "\n\n".join(sections), warnings


def extract_generic_zip(
    path: Path,
    max_member_bytes: int,
    max_total_bytes: int,
) -> tuple[str, list[str]]:
    sections: list[str] = []
    warnings: list[str] = []
    inspected = 0
    with zipfile.ZipFile(path) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        listing = "\n".join(f"- {info.filename} ({info.file_size} bytes)" for info in infos)
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
        sections = [
            f"# Page {index}\n\n{page.extract_text() or ''}"
            for index, page in enumerate(reader.pages, 1)
        ]
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
        sections: list[str] = []
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
    with tempfile.TemporaryDirectory(prefix="jira-office-") as temp_dir:
        _, error = command_output(
            [
                executable,
                "--headless",
                "--convert-to",
                "txt:Text",
                "--outdir",
                temp_dir,
                str(path),
            ],
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
    if (mime_type or "").startswith("image/") or suffix in {
        ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp",
    }:
        return extract_image(path, timeout)
    if suffix in {".wps", ".doc", ".xls", ".ppt", ".odt", ".ods", ".odp"}:
        return extract_with_libreoffice(path, timeout)
    if zipfile.is_zipfile(path):
        return extract_generic_zip(path, max_member_bytes, max_total_bytes)
    return "", ["Unsupported binary format; inspect the original manually"]
