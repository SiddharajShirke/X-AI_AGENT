# End-to-end lifecycle

1. A manual action or an enabled account schedule selects an `x_account_id` and context. Schedule times are interpreted in that account's timezone.
2. The Pipeline loads only that account's latest profile, contexts, trends, approved/rejected examples, and learned preferences.
3. Groq or the credential-free demo writer creates candidates. `never_reveal`, banned-language, length, and similarity checks run before a draft can become `pending`.
4. The pending draft appears on the account dashboard and, when bound, its Slack channel. A notification is not approval. A webhook test proves outbound posting only; it does not prove that a Slack action can reach the HTTP callback.
5. The human chooses one action:
   - **Approve:** safety runs again, the draft is atomically claimed, and one publisher call occurs.
   - **Reject:** the original and reason remain in history; a materially different child draft is generated while attempts remain.
   - **Edit:** the original remains, the edit is checked, and a child draft is created. Checking “approve” is an explicit approval in the same authenticated submission.
6. Publication is dry-run unless both live switches are on. In live mode, the account's decrypted Buffer key and channel are used for that call only.
7. A failed live publication remains `failed` and needs an explicit Retry. Published drafts cannot retry.
8. Timeout becomes `expired`, records a timeout lesson, and may create a replacement. It never calls a publisher.
9. At the account's attempt limit, regeneration stops at `needs_guidance`.

Dashboard actions use HTTP Basic identity plus CSRF. Slack actions arrive through HTTP Interactivity only when the same Slack app has Socket Mode Off, Interactivity & Shortcuts On, and the exact connection callback URL from the dashboard configured in Slack. They use the Slack user identity only after HMAC verification, timestamp validation, connection/account binding validation, and draft/account validation. Repeated callbacks are idempotent at the publication claim.
