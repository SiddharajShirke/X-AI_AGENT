---
name: startup-x-agent-content-generation
description: "Change prompts, the GroqWriter, or the credential-free demo writer."
---

# Content Generation

## Purpose

Produce distinct, natural, source-aware posts under 280 characters.

## Read first

app/services/generation.py, app/services/prompts.py, app/services/feedback.py, tests/test_generation.py

## Workflow

1. Write a failing example that expresses the desired writing behavior.
2. Update prompt inputs or writer logic without adding publication side effects.
3. Preserve Groq failure fallback to DemoWriter — a Groq failure must not stop the review workflow.
4. Run safety and similarity tests after generation tests.

## Writers

- **GroqWriter** — live writer that uses the Groq Python SDK (`from groq import Groq`).
  - Activated when `GROQ_API_KEY` is set in the environment.
  - Calls `client.chat.completions.create(...)` with a system message + PromptBuilder user message.
  - Uses `settings.groq_model` (default: `openai/gpt-oss-120b`).
  - The model ID contains `openai/` but is **hosted and called through Groq** — do not use the OpenAI client.
  - Returns `provider="groq"`.
- **DemoWriter** — credential-free deterministic fallback. Always available.
  - Returns `provider="demo"`.
  - Used when `GROQ_API_KEY` is absent or when Groq generation raises any exception.

## Fallback behavior

When `GroqWriter` raises any exception:
1. Log a safe warning: `"Groq generation failed; using demo writer: <exc>"`.
2. Do **not** log the API key or authorization headers.
3. Fall back to `DemoWriter`.
4. Continue the normal review workflow.

## Prompts and feedback memory

- The PromptBuilder prompt, safety context, and feedback memory (approved/rejected examples, learned preferences) remain intact regardless of which writer is active.
- `prompt_snapshot` is always populated.

## Constraints

- Return one post only.
- Do not invent customers, metrics, launches, features, or research findings.
- Do not put secrets in prompts beyond the explicit prohibition list.
- Do not force hashtags, emojis, or a repeated template.
- Do not enable Groq web search or Compound tools.

## Verification

```bash
pytest tests/test_generation.py tests/test_guards_and_feedback.py -q
```
```bash
pytest -q
```
