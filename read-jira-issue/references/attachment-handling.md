# Attachment handling

Read this reference only when an attachment warning appears or a format needs manual handling.

## Built-in extraction

The bundled script extracts these formats without running attachment code:

- Plain text, logs, structured text, source code, and common configuration files
- DOCX, PPTX, and XLSX OOXML text, including document parts, slides, notes, and comments when present
- ZIP, JAR, AAR, and APK listings plus text-like members, with decompression limits

It also uses optional tools when available:

- PDF: `pdftotext`, then the bundled `pypdf` wheel
- Images: `tesseract`
- WPS and legacy/proprietary Office formats: LibreOffice/soffice, then heuristic Unicode/ASCII strings

## Manual fallbacks

- Visually inspect PNG, JPEG, WEBP, and other image originals or thumbnails when OCR is absent or unclear.
- Use an available document tool for PDFs with empty extraction, especially scanned documents.
- Preserve unsupported originals and identify them by filename, MIME type, size, and checksum.
- Do not install system software merely to improve extraction unless the user authorizes that environment change.

## Archive safeguards

- Read ZIP members directly instead of blindly extracting paths.
- Skip encrypted members, oversized individual members, and content beyond configured total limits.
- Treat executables, APKs, and unknown binaries as evidence to inventory, not code to execute.

## Completeness language

Use “downloaded” only for original files successfully saved. Use “machine-read” only when text was extracted or visuals were actually inspected. List every remaining gap explicitly.
