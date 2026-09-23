# Security and private data

This repository contains no installation credentials, tenant identifiers, field mappings, real recall ledger, business workbooks, or production logs.

Store private configuration in an ignored local file and provide the API key through JDY_API_KEY. The client only sends credentials to the fixed Jiandaoyun API origin and refuses HTTP redirects. Raw response bodies and authorization headers are not logged.

Before publishing changes:

1. Inspect the staged diff and filenames.
2. Run `python tools/check_public.py --staged`.
3. Keep credentials, tenant IDs, private paths, workbooks, state snapshots and logs out of commits.
4. Do not paste any of those values into public issues, CI output or screenshots.

The scanner is an additional check, not a proof that arbitrary content is safe. Git ignore rules do not remove files that were previously tracked. If a real credential is exposed, revoke it first and then remove it from all history.

Report defects with synthetic fixtures and redacted diagnostics. For a security-sensitive finding, use the repository's private reporting mechanism if enabled; do not disclose a working exploit with real credentials in a public issue.
