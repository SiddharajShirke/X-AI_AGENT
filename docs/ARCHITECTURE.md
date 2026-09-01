# Architecture

## Design objective

The prototype makes every important decision visible: what context was loaded, what draft was generated, what safety and similarity checks ran, what the human decided, what lesson was stored, and whether publishing occurred.

## Component diagram

```text
┌─────────────────────────────────────────────────────────────────────┐
│ Admin dashboard                                                     │
│ profile • never reveal • trends • competitors • contexts • timers  │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
                     ┌────────────────────┐
                     │ SQLite repository  │
                     │ config + history   │
                     └─────────┬──────────┘
                               ▼
┌───────────┐       ┌────────────────────┐       ┌──────────────────┐
│ Scheduler │──────►│ Pipeline           │◄──────│ Feedback memory  │
└───────────┘       └─────────┬──────────┘       └──────────────────┘
                              │
              ┌───────────────┼────────────────┐
              ▼               ▼                ▼
      Trend collector   Content generator   Safety guard
   manual/RSS/X/demo    OpenAI/demo writer  + similarity
              └───────────────┬────────────────┘
                              ▼
                    Pending review draft
                              │
              ┌───────────────┼──────────────────┐
              ▼               ▼                  ▼
          Dashboard        Telegram             Slack
              │               │                  │
              └───────────────┼──────────────────┘
                              ▼
                    Explicit human action
                    approve / reject / edit
                              │
              ┌───────────────┴─────────────────┐
              ▼                                 ▼
       Publisher manager                  Feedback engine
     dry-run / direct X                 examples + rules
```

## Dependency direction

Routes call services; services call the repository or adapters; adapters do not call routes. `Pipeline` owns business sequencing. `Repository` owns SQLite details. This allows a later production version to replace SQLite, notifications, generation, or publishing without changing the lifecycle contract.

## Core invariants

- Draft creation and publication are separate actions.
- Only `pending` drafts can be approved or rejected.
- Only explicit approval calls the publisher.
- Safety is checked at generation and again immediately before publishing.
- Rejected and expired drafts remain queryable.
- A replacement links to its parent and increments the attempt.
- Learned rules never override `never_reveal`.
- Attempt limits stop automatic loops.

## Adapter selection

- Generation: OpenAI when a key is present; otherwise `DemoWriter`.
- Publication: direct X only when explicitly enabled and credentials are complete; otherwise `DryRunPublisher`.
- Notification: console always; Telegram and Slack when configured.
- Trends: stored/manual first, optional X and RSS next, demo signals when none exist.
