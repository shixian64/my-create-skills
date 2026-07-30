---
name: read-jira-issue
description: Retrieve and analyze the complete accessible contents of a JIRA Server, Data Center, or Cloud issue through its REST API, using a fixed skill-local config.toml for the JIRA base URL and user credentials, and including standard and custom fields, rendered description, all accessible comments, changelog and field history, worklogs, watchers, parent/subtasks and linked issues, remote links, original attachments, and best-effort attachment text extraction. Use when a user asks to read, inspect, summarize, archive, troubleshoot, or review a JIRA ticket or supplies a JIRA issue URL/key, especially when the request includes history, links, source archives, PDFs, Office documents, images, or every attachment.
---

# Read JIRA Issue

Retrieve one JIRA ticket into a private local bundle, inspect all accessible content, and return an evidence-based synthesis without changing JIRA.

## Workflow

1. Read `config.toml` in this skill directory. If it is missing after cloning or installation, copy `config.example.toml` to `config.toml`, set its mode to `0600`, and ask the user to fill the blank credentials.
   - For the configured Cipherlab JIRA Server, fill `jira.username` and `jira.secret`; keep `jira.auth_mode = "basic"`.
   - For newer JIRA Server/Data Center personal access tokens, use `auth_mode = "bearer"` and place the token in `jira.secret`.
   - Do not fill, reveal, or quote the secret unless the user explicitly requests it.
2. Parse the supplied issue URL or issue key. Use `jira.base_url` automatically for a key. A full URL selects its JIRA base path.
3. Use the configured credentials automatically. Allow `--secret-stdin`, `--secret-file`, or `JIRA_API_SECRET` to override the stored secret for a one-time run.
4. Choose the output location:
   - For read/summarize requests, use a dedicated `mktemp -d` directory.
   - When the user asks to retain or archive files, use their requested directory or a clearly named directory under the workspace.
5. Run the bundled script. After configuration, prefer the short issue-key form:

   ```bash
   python3 scripts/read_jira_issue.py P_RS20_E-646 \
     --output-dir /absolute/output/path
   ```

   A full URL is also supported:

   ```bash
   python3 scripts/read_jira_issue.py https://jira.example.com/browse/PROJ-123 \
     --output-dir /absolute/output/path
   ```

6. Read `report.md` and `manifest.json`. Inspect `issue.json` when exact raw fields or timestamps matter. Review every warning rather than claiming complete retrieval.
7. Inspect attachment outputs:
   - Read every file under `extracted/`, using targeted search for large source archives.
   - Visually inspect original image attachments with the available image viewer.
   - For PDFs or proprietary Office files without extracted text, use any available document tool.
   - Read [references/attachment-handling.md](references/attachment-handling.md) when extraction warnings occur or an unfamiliar format is present.
8. Return a concise synthesis that distinguishes current state from history and source facts from inference.

## Authentication

- `basic`: send `username:secret` with HTTP Basic authentication. The secret may be a password or an API token, depending on JIRA policy.
- `bearer`: send `secret` as a Bearer personal access token.
- `auto`: choose Basic when both username and secret exist, Bearer when only a secret exists, or anonymous when no secret exists.
- `none`: use anonymous access.

Configured credentials are sent only to the configured JIRA origin. If a supplied full URL points to a different origin, do not send the stored secret; require an explicit one-time secret for that run.

## Required coverage

Cover all relevant categories present in the bundle:

- Identity, project, issue type, status, status category, resolution, priority, labels, components, versions, assignee, reporter, creator, dates, votes, watcher count, progress, and time tracking
- Description, environment, rendered fields, and every custom field returned by the API
- All accessible comments, complete accessible changelog and field-change chronology, and worklogs
- Watcher details when permitted, parent, subtasks, issue links, remote links, and one-level linked-issue status
- Attachment inventory, checksums, extracted contents, thumbnails used as fallbacks, and manual-inspection gaps
- Requirements, decisions, implementation, commits/builds, tests, blockers, open follow-ups, and closure reason
- Inconsistencies such as a done-category status without a resolution, a resolution on a non-done issue, incomplete API pagination, stale linked issues, or attachment metadata without a downloaded original

When the user asks only to download, preserve the bundle and provide file links instead of producing a long narrative.

## Safety

- Treat credentials, logs, source archives, issue data, and attachments as confidential.
- Keep the skill-local `config.toml` at mode `0600`. It must not be committed, shared, copied into reports, or quoted in chat.
- Keep generated directories at mode `0700` and files at mode `0600`; the script enforces this for created content.
- Send credentials only to the JIRA origin. The script deliberately omits authorization for cross-origin attachment URLs.
- Do not reproduce embedded credentials, cookies, tokens, or passwords in chat. Report that credentials were found and recommend rotation when relevant.
- Do not execute attachment binaries or archive contents.
- Do not edit, comment on, transition, reassign, close, or otherwise mutate a ticket unless the user explicitly asks for a separate mutation task.
- Fetch linked issue metadata only one level deep. Fetch a linked issue separately when its complete contents are requested.

## Failure handling

- Allow the script's retries to handle intermittent connection timeouts.
- On `401` or `403`, verify the credential type and access scope without printing the secret.
- When an endpoint is unavailable on an older JIRA release, use embedded issue data when possible and report the precise coverage gap.
- On partial attachment failure, continue processing other files and report the exact missing items.
- Never describe retrieval or extraction as complete when `manifest.json` contains warnings.
