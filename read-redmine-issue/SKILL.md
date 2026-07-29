---
name: read-redmine-issue
description: Retrieve and analyze the complete accessible contents of a Redmine issue through its REST API, using a fixed skill-local config.toml for the Redmine base URL and API key, and including standard and custom fields, description, journals and field-change history, watchers, parent/child and related issues, original attachments, and best-effort attachment text extraction. Use when a user asks to read, inspect, summarize, archive, troubleshoot, or review a Redmine ticket or supplies a Redmine issue URL/ID, especially when the request includes comments, history, linked tickets, source archives, PDFs, Office documents, images, or every attachment.
---

# Read Redmine Issue

Retrieve a ticket into a private local bundle, inspect all available content, and return an evidence-based summary without changing Redmine.

## Workflow

1. Read `config.toml` in this skill's root directory. It contains detailed inline instructions; ask the user to fill `redmine.base_url` and `redmine.api_key` when they are blank. Do not fill or reveal the key unless the user explicitly requests it.
2. Parse the supplied issue URL or numeric ID. Use `redmine.base_url` automatically for a numeric ID. A full issue URL overrides the configured base URL.
3. Use the configured API key automatically. Allow `--api-key-stdin`, `--api-key-file`, or `REDMINE_API_KEY` to override it for a one-time run.
4. Choose the output location:
   - For read/summarize requests, use a dedicated `mktemp -d` directory.
   - When the user asks to retain or archive files, use their requested directory or a clearly named directory under the workspace.
5. Run `scripts/read_redmine_issue.py` from this skill directory. After configuration, prefer the shorter numeric-ID form:

   ```bash
   python3 scripts/read_redmine_issue.py 12345 --output-dir /absolute/output/path
   ```

   The script always loads `config.toml` by default. Use `--config` only to test or temporarily select another TOML file. It fetches the full issue, one level of linked issues, all original attachments, and best-effort extracted attachment text.
6. Read `report.md` and `manifest.json`. Inspect `issue.json` when exact raw fields or timestamps matter. Review every warning rather than claiming complete extraction.
7. Inspect attachment outputs:
   - Read each file under `extracted/`, using targeted search for large source archives.
   - Visually inspect original image attachments with the available image-viewing tool.
   - For PDFs or proprietary Office files without extracted text, use any available document tool. If none exists, state that the original was downloaded but its internal content was not machine-read.
   - Read [references/attachment-handling.md](references/attachment-handling.md) when extraction warnings occur or an unfamiliar format is present.
8. Return a concise synthesis that distinguishes current state from history and source facts from inference.

## Required coverage

Cover all relevant categories present in the bundle:

- Identity, project, tracker, status, priority, assignee, author, dates, progress, and target version
- Description and every custom field
- Complete journal notes and field-change chronology
- Watchers, parent/child issues, relations, and linked-issue status
- Attachment inventory, checksums, extracted contents, and manual-inspection gaps
- Requirements, decisions, implementation, commits/builds, tests, blockers, open follow-ups, and closure reason
- Inconsistencies such as status versus `closed_on`, effort fields versus journal estimates, or 100% progress on a non-closed linked issue

When the user asks only to download, preserve the bundle and provide file links instead of producing a long narrative.

## Safety

- Treat API tokens, passwords, server credentials, logs, and attachments as confidential.
- Keep the skill-local `config.toml` at mode `0600`. It may contain the API key and must not be committed, shared, copied into reports, or quoted in chat.
- Do not reproduce embedded credentials in chat. Report that credentials were found and recommend rotation when relevant.
- Keep generated directories at mode `0700` and files at mode `0600`; the script enforces this for created content.
- Send the API key only to the Redmine origin. The script deliberately omits it for cross-origin URLs.
- Do not edit, comment on, reassign, close, or otherwise mutate a ticket unless the user explicitly asks.
- Do not recursively fetch attachments from linked issues by default; linked issue metadata is one level deep to avoid unbounded expansion. Fetch a linked issue separately when its full contents are requested.

## Failure handling

- Allow the script's retries to handle intermittent connection timeouts.
- On `401` or `403`, verify the token source and access scope without printing the token.
- On partial attachment failure, continue processing other files and report the exact missing items.
- Never describe attachment extraction as complete when `manifest.json` contains warnings.
