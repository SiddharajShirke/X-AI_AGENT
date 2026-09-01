# Mapping to the earlier `XAgent` implementation

The earlier class already combines rewriting, validation, optional image processing, direct Tweepy/Buffer publication, scraping, Excel queue synchronization, and spreadsheet write-back. This prototype separates the new concerns around it:

| Earlier responsibility | Prototype equivalent |
|---|---|
| `publish()` | `app/services/publishers.py` behind explicit approval |
| `rewrite_text()` | `app/services/generation.py` |
| `truncate_tweet()` and validator | safety and length checks |
| `scrape()` | trend collector adapter boundary |
| Excel review queue | dashboard/Telegram/Slack review queue |
| sheet write-back | SQLite drafts, feedback, versions, and event log |
| dry run | default `DryRunPublisher` |

When merging into the original repository, keep this prototype’s `Pipeline` and replace `PublisherManager` with an adapter that calls the existing `XAgent.publish(text=..., rewrite=False)` only after approval. The existing class’s dry-run sheet write-back and direct-image limitations should be addressed separately before production use.
