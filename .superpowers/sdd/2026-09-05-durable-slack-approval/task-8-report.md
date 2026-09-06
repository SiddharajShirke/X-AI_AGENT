# Follow-up Task 8 report

## RED

Command:

```text
C:\Users\siddh\X-AI_AGENT\.venv\Scripts\python.exe -m pytest -q tests/test_pipeline.py::test_timeout_recovers_committed_expiring_claim_after_restart
```

Observed behavioral failure before the production change:

```text
FAILED tests/test_pipeline.py::test_timeout_recovers_committed_expiring_claim_after_restart
E       AssertionError: assert 'expiring' == 'needs_guidance'
```

The simulated committed timeout claim remained stranded after rebuilding the
repository and services over the same database.

## GREEN

The same focused command passed after the repository change:

```text
.                                                                        [100%]
```

## Implementation

- Timeout candidate discovery now includes overdue drafts already persisted as
  `expiring`, while remaining scoped by `x_account_id`.
- `claim_draft_for_expiration()` now resumes an existing `expiring` claim.
- The timely active Slack-action exclusion remains on the `pending` claim path;
  recovery does not move an `expiring` draft back to `pending`.
- Pipeline lifecycle code was unchanged. Recovered claims follow the existing
  timeout feedback and terminal transition path, with no approval or publisher
  call.
- The regression uses the deterministic attempt-limit branch and verifies the
  recovered `needs_guidance` state, timeout feedback history, absence of a child
  draft, and no processing on a subsequent timeout pass.

## Files

- `app/repository.py`
- `tests/test_pipeline.py`
- `.superpowers/sdd/2026-09-05-durable-slack-approval/task-8-report.md`

## Verification

- Focused regression: passed.
- `pytest -q tests/test_scheduler.py tests/test_pipeline.py tests/test_slack_action_jobs.py`:
  46 passed.
- `pytest -q`: 185 passed with one pre-existing Starlette/httpx deprecation
  warning.
- `python -m compileall app scripts`: exit 0.
- `python scripts/demo_flow.py`: exit 0; both demo publications still reported
  explicit human approval actions.
- `git diff --check`: exit 0 (Git emitted only the repository's LF-to-CRLF
  working-copy warnings).

## Concerns

No known concern within the requested one-process SQLite scope. As requested,
this change is limited to the crash window immediately after the committed
`expiring` claim; it does not redesign or broaden recovery for later timeout
lifecycle crash windows.
