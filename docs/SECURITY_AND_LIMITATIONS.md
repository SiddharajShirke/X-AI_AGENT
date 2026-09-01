# Security and prototype limitations

## Deliberately prototype-level

- HTTP Basic authentication rather than SSO.
- SQLite rather than a managed relational database.
- In-process timer rather than a durable distributed job queue.
- Deterministic phrase/overlap safety rather than enterprise DLP.
- Direct outbound HTTP integrations with basic retry behavior.
- Text-only X publication.
- No multi-tenant isolation.

## Before production

Add identity and roles, CSRF protection for form actions, encrypted secrets, managed Postgres, durable queues, scheduler leader election, idempotency keys, rate-limit handling, provider reconciliation, structured audit retention, backups, monitoring, alerting, content provenance, legal review, and adversarial safety testing.

## Content responsibility

The system is a drafting and workflow tool. A responsible human remains accountable for accuracy, confidentiality, intellectual property, platform rules, and whether ten daily posts are appropriate for the audience.

## Handling confidential startup information

Use the dynamic `never_reveal` field for high-level categories and recognizable phrases that must be blocked. Do not store credentials, source code, raw customer records, legal secrets, or the complete unreleased product specification in this prototype. When `OPENAI_API_KEY` is enabled, the configured safe context and prohibition rules are sent to the selected OpenAI model as generation instructions; the generated text is then checked locally again. Organizations with stricter data-boundary requirements should replace this design with an approved private model/DLP architecture before entering sensitive material.

X and RSS text is treated as untrusted reference data. The prompt tells the writer to ignore instructions embedded in those sources, but production use still requires stronger prompt-injection testing and source sanitization.
