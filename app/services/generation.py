from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Protocol

from app.config import Settings
from app.models import ContentContext, GenerationResult, StartupProfile, TrendItem
from app.services.prompts import PromptBuilder

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GenerationInput:
    profile: StartupProfile
    context: ContentContext
    trends: list[TrendItem]
    approved_examples: list[str]
    rejected_examples: list[str]
    learned_preferences: list[str]
    attempt: int
    parent_text: str
    sequence: int = 0
    variation: int = 0


class Writer(Protocol):
    def generate(self, data: GenerationInput) -> GenerationResult: ...


def _trim_post(text: str, limit: int = 280) -> str:
    text = "\n".join(line.rstrip() for line in text.strip().splitlines()).strip()
    if len(text) <= limit:
        return text
    shortened = text[: limit - 1].rstrip()
    if " " in shortened:
        shortened = shortened.rsplit(" ", 1)[0]
    return shortened.rstrip(".,;:") + "…"


class DemoWriter:
    """Credential-free writer used for deterministic, end-to-end demonstrations."""

    def generate(self, data: GenerationInput) -> GenerationResult:
        profile = data.profile
        context = data.context
        domain = profile.domain.rstrip(".")
        problems = profile.problems or profile.content_pillars or ["reliable execution"]
        # The sequence is based on saved draft history, so demo mode keeps
        # producing visibly different examples across days without randomness.
        seed = max(data.sequence, 0) + max(data.attempt - 1, 0) * 5 + data.variation * 7
        problem = problems[(seed // 3) % len(problems)]
        trend = data.trends[(seed // max(len(problems), 1)) % len(data.trends)] if data.trends else None
        signal = trend.title if trend else f"a recurring discussion in {domain}"

        templates = {
            1: [
                f"A founder lesson from {domain}: solving {problem} once is a demo. The real test is whether the workflow still behaves well after the hundredth run.",
                f"The less glamorous part of building in {domain} is often the most important: making {problem} predictable enough for a team to trust every day.",
                f"Building around {problem} keeps reinforcing one idea: reliability is not a launch feature. It is a habit designed into every repeated workflow.",
            ],
            2: [
                f'One useful signal in {domain}: teams are moving from asking "can it do this?" to "can it do this consistently, explainably, and with human control?"',
                f"{signal} points to a broader shift: practical teams are judging tools by repeatability and recovery, not only by the best-case demo.",
                f"The market conversation around {problem} is getting more concrete. Buyers increasingly want evidence of dependable operation, not another polished prototype.",
            ],
            3: [
                f"A recurring problem in {domain}: failure is easy to notice, but silent inconsistency is not. Teams need ways to see when the same input starts producing different operational outcomes.",
                f"The hardest part of {problem} is rarely the happy path. It is deciding what should happen when confidence is low, context is missing, or a human needs to step in.",
                f"Many teams can automate a task. Fewer have a clear answer for ownership when that automation is uncertain. That gap matters in {domain}.",
            ],
            4: [
                f"A simple way to evaluate {problem}: test the same workflow across normal cases, edge cases, and recovery cases. One successful run tells you very little about operational trust.",
                f"Useful evaluation for {domain} needs three questions: Did it finish? Was the result correct? Could a person understand and recover when it was not?",
                f"A small reliability habit: save failed examples, classify why they failed, and make the next test set include them. Memory should improve the system, not hide mistakes.",
            ],
            5: [
                f"In {domain}, observability is not just logs. Teams need to understand the input, decision path, confidence, output, and human intervention for each meaningful run.",
                f"A technical trade-off worth discussing: more autonomy can reduce clicks, but it also increases the need for boundaries, evaluation, and reversible actions.",
                f"For {problem}, deterministic checkpoints often matter more than adding another clever generation step. Clear state makes failures easier to inspect and recover.",
            ],
            6: [
                f"The market for {domain} may split into two layers: impressive capabilities and dependable operations. The second layer is where long-term trust will probably be won.",
                f"{signal} suggests the conversation is maturing. Teams are starting to compare operational fit, not only model capability.",
                f"A market signal we keep noticing: buyers ask fewer abstract AI questions and more concrete questions about control, evaluation, and responsibility.",
            ],
            7: [
                f"Contrarian view: the most autonomous system is not automatically the best product. In {domain}, clear boundaries and easy human intervention can be a stronger advantage.",
                f"More intelligence does not always fix {problem}. Sometimes the better move is a narrower workflow, clearer state, and a reliable path for escalation.",
                f"A polished demo can be less informative than a boring failure report. In {domain}, the second one often tells you whether the system can survive real use.",
            ],
            8: [
                f"A building lesson: write down why a draft, workflow, or experiment was rejected. The rejected version is useful data when the next attempt can actually retrieve the lesson.",
                f"Startup speed is not only shipping faster. It is shortening the loop between a real mistake, a clear lesson, and a visibly better next attempt.",
                f"One thing worth protecting while building in {domain}: the ability to change direction without losing the history of why earlier choices failed.",
            ],
            9: [
                f"For teams working on {domain}: what is harder in practice—getting a workflow to succeed once, or keeping its behavior reliable after hundreds of runs?",
                f"Where does human review add the most value in your workflow today: before an action, after an exception, or only when confidence drops?",
                f"What evidence would make you trust a system handling {problem}: benchmark scores, a full audit trail, controlled trials, or something else?",
            ],
            10: [
                f"A likely next step for {domain}: systems will be judged less by how independently they act and more by how clearly they coordinate with people when reality gets messy.",
                f"The future of {problem} may look less like removing humans and more like giving them better timing, context, and control over the decisions that matter.",
                f"Over time, dependable AI workflows may become ordinary infrastructure. The interesting question is which trust and oversight patterns become standard first.",
            ],
        }
        choices = templates.get(context.id, templates[((context.id - 1) % 10) + 1])
        index = seed % len(choices)
        text = _trim_post(choices[index])
        return GenerationResult(
            text=text,
            topic=problem,
            source_summary=trend.title if trend else "Configured startup context",
            provider="demo",
            rationale=f"{context.name} angle; attempt {data.attempt}; variation {data.variation}.",
        )


class GroqWriter:
    """Live writer that calls the Groq chat completions API.

    The client is injectable so tests can supply a fake without making
    any real network requests.
    """

    _SYSTEM_MESSAGE = (
        "You write natural, specific startup X posts. Confidentiality rules are "
        "absolute. Do not reveal hidden product details, imitate competitors, or "
        "copy source wording. Return only the final post text."
    )

    def __init__(self, settings: Settings, prompt_builder: PromptBuilder | None = None, client=None):
        self.settings = settings
        self.prompt_builder = prompt_builder or PromptBuilder()
        # Allow test injection; real client is created lazily on first use
        # when not provided, so the import does not fail without the package.
        self._injected_client = client

    def _get_client(self):
        if self._injected_client is not None:
            return self._injected_client
        from groq import Groq  # type: ignore[import]

        return Groq(
            api_key=self.settings.groq_api_key,
            timeout=self.settings.groq_timeout_seconds,
            max_retries=1,
        )

    def generate(self, data: GenerationInput) -> GenerationResult:
        prompt = self.prompt_builder.build(
            profile=data.profile,
            context=data.context,
            trends=data.trends,
            approved_examples=data.approved_examples,
            rejected_examples=data.rejected_examples,
            learned_preferences=data.learned_preferences,
            attempt=data.attempt + data.variation,
            parent_text=data.parent_text,
        )

        client = self._get_client()
        request = {
            "model": self.settings.groq_model,
            "messages": [
                {"role": "system", "content": self._SYSTEM_MESSAGE},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.8,
            "max_completion_tokens": 512,
            "n": 1,
        }
        if self.settings.groq_model in {
            "openai/gpt-oss-20b",
            "openai/gpt-oss-120b",
        }:
            request["reasoning_effort"] = "low"
        response = client.chat.completions.create(**request)

        raw_content: str = response.choices[0].message.content or ""
        if not raw_content.strip():
            raise ValueError(
                "Groq returned an empty or blank response; cannot produce a post."
            )

        text = _trim_post(raw_content)
        return GenerationResult(
            text=text,
            topic=data.context.name,
            source_summary=data.trends[0].title if data.trends else "Configured startup context",
            provider="groq",
            rationale=f"Groq generation for {data.context.name}.",
            prompt_snapshot=prompt,
        )


class ContentGenerator:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.demo_writer = DemoWriter()
        self.live_writer = GroqWriter(settings) if settings.groq_api_key else None

    def generate(self, data: GenerationInput) -> GenerationResult:
        if self.live_writer is not None:
            try:
                return self.live_writer.generate(data)
            except Exception as exc:
                logger.warning("Groq generation failed; using demo writer: %s", exc)
        result = self.demo_writer.generate(data)
        prompt = PromptBuilder().build(
            profile=data.profile,
            context=data.context,
            trends=data.trends,
            approved_examples=data.approved_examples,
            rejected_examples=data.rejected_examples,
            learned_preferences=data.learned_preferences,
            attempt=data.attempt + data.variation,
            parent_text=data.parent_text,
        )
        return result.model_copy(update={"prompt_snapshot": prompt})

    def variation(self, data: GenerationInput, variation: int) -> GenerationResult:
        return self.generate(replace(data, variation=variation))
