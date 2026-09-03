# Architecture

## Objective

One trusted operator can manage two or three independent X account workspaces in one FastAPI process and SQLite database. Account separation is explicit in every repository and Pipeline call; this is account-scoped data isolation inside one application, not hostile multi-tenant isolation.

```text
Accounts dashboard / account setup / signed Slack action
                         |
                         v
                  account-scoped routes
                         |
                         v
          Pipeline (generation and lifecycle sequencing)
             /            |                 \
      account data   notifier manager   publisher manager
          |          resolves Slack      per-call Buffer target
          |                 |                 |
          +---------- SQLite repository -----+
                     encrypted credentials
```

## Boundaries

- `app/repository.py` persists account-owned rows and performs the atomic publication claim/completion transactions.
- `app/services/pipeline.py` sequences generation, safety, review, feedback, timeout, and publication.
- `app/services/integrations.py` encrypts/decrypts reusable Buffer and Slack connections, resolves account bindings, tests connections, and verifies Slack signatures.
- `app/services/notifiers.py` resolves Slack for the current account on every notification. Console remains credential-free; Telegram is optional.
- `app/services/publishers.py` receives decrypted Buffer credentials only for one call. It never stores them in results or the manager.
- `app/routes/` validates transport concerns. Dashboard/API/Slack all call the same Pipeline transitions.

## Account ownership

`x_account_id` scopes profiles, contexts, schedules, trends, drafts, feedback, preferences, event logs, integration bindings, and publication attempts. Composite foreign keys prevent a child row from referencing another account's parent. Copying an account copies only profile/context/schedule configuration.

Reusable `integration_connections` contain Fernet-encrypted provider credentials. `account_integrations` bind a connection to an account and carry the account-specific target, such as a Buffer channel ID. Therefore one Buffer login may target two different X channels, while another account may use a separate login.

## Publication concurrency

Approval first performs a conditional SQLite update from `pending` to `publishing` and inserts a publication-attempt row in the same transaction. Only the winner calls Buffer. Repeated or overlapping approvals observe the existing state. A retry claims only `failed` and requires a new explicit action.

## Adapter selection

- Generation: Groq when configured; deterministic demo writer otherwise.
- Publishing: dry-run unless both the global Buffer flag and account live flag are enabled. When both are enabled, a missing/locked Buffer binding is a visible failure.
- Review delivery: dashboard always, account-bound Slack when configured, optional Telegram.
- Trends: stored/manual, optional RSS/X, then demo signals when none exist.

The scheduler is single-process and SQLite-backed. Horizontal scaling and distributed scheduling are outside this prototype.
