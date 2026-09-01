---
name: startup-x-agent-x-publisher
description: "Change dry-run or Buffer-based X publication behavior."
---

# X Publisher

## Purpose

Keep publication behind approval and make demo/live selection explicit.
Buffer is the only live publishing provider. The application never calls the X API directly.

## Read first

app/services/publishers.py, app/services/pipeline.py, app/config.py, docs/SECURITY_AND_LIMITATIONS.md

## Publishers

- **DryRunPublisher** — default safe publisher. Returns `provider="dry_run"`. Makes no external calls.
- **BufferPublisher** — live publisher. Sends the approved post to the Buffer GraphQL API.
  - Buffer then publishes to the connected X channel.
  - Returns `provider="buffer"`.
  - Selected only when `BUFFER_LIVE_POSTING=true` AND both `BUFFER_API_KEY` and `BUFFER_CHANNEL_ID` are set.

## PublisherManager selection logic

| Condition | Publisher selected |
|---|---|
| `BUFFER_LIVE_POSTING=false` (default) | DryRunPublisher |
| `BUFFER_LIVE_POSTING=true`, API key missing | DryRunPublisher |
| `BUFFER_LIVE_POSTING=true`, channel ID missing | DryRunPublisher |
| `BUFFER_LIVE_POSTING=true`, API key + channel ID present | BufferPublisher |

There is **no path** from PublisherManager to any direct X/Twitter publisher.

## Buffer GraphQL API

- Endpoint: `settings.buffer_api_url` (default: `https://api.buffer.com`)
- Auth header: `Authorization: Bearer <BUFFER_API_KEY>`
- Content-Type: `application/json`
- Uses the `createPost` GraphQL mutation with variables (text not interpolated in the query string).
- API keys must **never** be logged or stored in PublishResult.
- `TWITTER_BEARER_TOKEN` (if configured) is **only** used for X recent-search trend research; it is unrelated to publishing.

## Buffer GraphQL error handling

Buffer returns HTTP 200 for both success and typed errors. All of these must be handled:

1. Network or timeout exception → `success=False`
2. Non-2xx HTTP response → `success=False`
3. Invalid JSON → `success=False`
4. Top-level `errors[]` in the response → `success=False`
5. `createPost.__typename` is `MutationError` → `success=False`
6. Missing `data.createPost` → `success=False`
7. Missing `post` object → `success=False`
8. Missing Buffer post ID → `success=False`
9. `PostActionSuccess` with a valid post ID → `success=True`, `provider="buffer"`

The `externalLink` field may be null while Buffer is still processing. Do **not** fabricate an X URL. Set `post_url=""` when `externalLink` is null.

## Constraints

- **Only explicit human approval** may trigger the Buffer API call.
- Dry-run remains the default. No credentials needed for demo mode.
- Never call the publisher during generation, rejection, edit, timeout, or notification.
- Do not hide a failed live publish behind success.
- Normalized `PublishResult` output must always be returned.
- Never log or commit `BUFFER_API_KEY`, authorization headers, or raw secrets.
- Buffer GraphQL errors must be checked even when HTTP status is 200.

## Workflow

1. Write a failing adapter or pipeline test without calling live Buffer.
2. Keep provider output normalized as PublishResult.
3. Require the live flag and complete credentials.
4. Record provider, external ID, URL, and failure reason.

## Verification

```bash
pytest tests/test_pipeline.py tests/test_integrations.py -q
```
```bash
python scripts/demo_flow.py
```
```bash
pytest -q
```
