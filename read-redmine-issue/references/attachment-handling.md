# Attachment handling

Read this reference only when an attachment warning appears or a format needs manual handling.

## Built-in extraction

The bundled script extracts these formats without third-party Python packages:

- Plain text, logs, structured text, source code, and common configuration files
- DOCX, PPTX, and XLSX OOXML text, including document parts, slides, notes, and comments where present
- ZIP/JAR/AAR/APK listings plus text-like members, with decompression size limits

It also uses optional tools when present:

- PDF: `pdftotext`, then the Python `pypdf` package
- Images: `tesseract`
- WPS and legacy/proprietary Office formats: LibreOffice/soffice, then heuristic Unicode/ASCII string extraction

## Manual fallbacks

- Visually inspect PNG, JPEG, WEBP, and other image originals when OCR is absent or unclear.
- Use an available PDF/document tool for PDFs with missing or empty text extraction, especially scanned documents.
- Preserve unsupported originals and identify them by filename, MIME type, size, and checksum.
- Do not install packages or system software merely to improve extraction unless the user's request authorizes that environment change. Prefer temporary dependencies when installation is explicitly approved.

## Archive safeguards

- Read ZIP members directly instead of blindly extracting their paths.
- Skip encrypted members, oversized individual members, and content beyond the configured total limit.
- Treat executables, APKs, and unknown binaries as evidence to inventory, not code to execute.

## Completeness language

Use “downloaded” for original files successfully saved. Use “machine-read” only for files whose text was extracted or whose visuals were actually inspected. List remaining gaps explicitly.
