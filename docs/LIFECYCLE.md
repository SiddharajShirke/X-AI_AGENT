# End-to-end lifecycle

## 1. Trigger

A manual dashboard action or an enabled schedule reaches its configured local `HH:MM`. The scheduler checks `last_run_date`, so the same slot runs once per local day.

## 2. Dynamic context load

The pipeline loads the latest profile, context, schedule, configuration version, approved examples, rejected examples, and weighted preferences. The process does not rely on values cached at server startup.

## 3. Current context collection

For contexts marked “live trends required,” the collector merges manual signals, optional X recent-search snippets, optional RSS entries, and safe demo signals. Competitor information is one-way market intelligence; private startup context is not sent to competitors or inserted into search queries.

## 4. Draft generation

The writer receives only safe public startup context plus the absolute prohibition list. It must choose one angle, avoid unverifiable claims, stay under 280 characters, and return only the post.

## 5. Safety and uniqueness

The deterministic safety guard checks never-reveal phrases, suspicious overlap, banned wording, emptiness, and length. The similarity guard compares the candidate with recent pending, rejected, expired, approved, and published text. Multiple internal candidates may be attempted; an unsafe or duplicate candidate never enters the review queue.

## 6. Human review

A pending draft is shown in the dashboard and optionally sent to Telegram and Slack. Silence is not approval.

### Approve

The pipeline re-runs safety, records the reviewer, calls the selected publisher, stores the provider and URL, records a positive example, and sends a status notification.

### Reject

The original draft is retained. The feedback engine maps the reason code and written note to reusable rules. If attempts remain, a child draft is generated with the rejected text and new rules in context. Similarity protection forces a materially different candidate.

### Edit

The edit is rechecked for disclosure and repetition. A safe edit becomes a child draft, preserving the original and the before/after learning signal. It can remain pending or be explicitly approved.

### No response

After the configured timeout, the draft becomes expired, a timeout lesson is stored, and a replacement is generated. No publisher is called.

## 7. Stop condition

When the attempt limit is reached, status becomes `needs_guidance`. The system waits for a human to change context, edit manually, or start a new chain.
