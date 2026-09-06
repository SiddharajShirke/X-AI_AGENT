# Security and prototype limitations

## Implemented safeguards

- Every lifecycle record and integration binding is account-scoped.
- Dashboard forms use HTTP Basic identity and signed CSRF tokens.
- Slack callbacks require the provider signature, a timestamp within five minutes, the account's bound connection, and an account-owned draft.
- Buffer and Slack credentials are Fernet-encrypted with `APP_ENCRYPTION_KEY`; UI/API responses show only a configured mask.
- Publication has an atomic claim, so repeated or overlapping approvals produce one publisher call.
- Global and per-account live switches default off. Missing credentials in live mode are failures, not dry-run success.
- `APP_MODE` does not gate publication. A demo/Buffer-live mismatch is warned and shown because the global Buffer switch plus an account LIVE switch remains publication-capable.
- The deprecated compatibility setting `X_LIVE_POSTING` remains false and cannot enable publishing; `BUFFER_LIVE_POSTING` is the effective global gate.
- Safety is checked again immediately before approval/retry. Silence is never approval.

## Deliberately prototype-level

- One trusted operator and HTTP Basic authentication; no SSO, roles, or hostile tenant boundary.
- SQLite and one in-process scheduler; no distributed queue or leader election.
- Application-managed encryption with one environment key; no managed key vault or rotation workflow.
- Deterministic disclosure/style checks rather than enterprise DLP.
- Text-only Buffer publication without provider reconciliation, media, or threads.
- Direct outbound webhooks with limited retry handling.
- No multi-instance, horizontal-scaling, distributed-worker, or leader-election safety. Run exactly one application instance against the persistent SQLite database.
- No automated backup, monitoring, retention policy, or disaster recovery.

## Slack callback deployment boundary

Slack Interactivity is an inbound HTTP boundary. Configure the exact callback URL displayed in **Connections** in the same Slack app that owns the incoming webhook, with Socket Mode Off and Interactivity & Shortcuts On. Socket Mode does not route interactions to this HTTP callback. The dashboard's webhook test proves outbound delivery only, so it is not evidence of inbound callback readiness. A public-origin change requires a Slack Request URL update before clicks can reach the application.

Keep `.env`, SQLite files, and backups private. If `APP_ENCRYPTION_KEY` is lost, saved integrations cannot be recovered. Never paste API keys, source code, raw customer data, legal secrets, or a complete unreleased specification into startup content fields. `never_reveal` should contain high-level prohibited topics or recognizable phrases.

Groq receives configured safe/public context and prohibition instructions when enabled, and output is checked locally. X/RSS text is untrusted reference material. Organizations with stricter boundaries need an approved private model and DLP architecture.

A responsible human remains accountable for accuracy, confidentiality, intellectual property, X/Buffer rules, and posting frequency.

WhatsApp is not implemented. When added, it should verify provider identity and call the same account-scoped Pipeline; it must not bypass or duplicate approval state.
