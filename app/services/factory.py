from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.repository import Repository
from app.services.feedback import FeedbackEngine
from app.services.generation import ContentGenerator
from app.services.integrations import IntegrationService
from app.services.notifiers import NotifierManager
from app.services.pipeline import Pipeline
from app.services.publishers import PublisherManager
from app.services.safety import SafetyGuard
from app.services.similarity import SimilarityGuard
from app.services.slack_actions import SlackActionProcessor
from app.services.trends import TrendCollector


@dataclass
class Services:
    pipeline: Pipeline
    generator: ContentGenerator
    trend_collector: TrendCollector
    safety_guard: SafetyGuard
    similarity_guard: SimilarityGuard
    feedback_engine: FeedbackEngine
    notifiers: NotifierManager
    publishers: PublisherManager
    integrations: IntegrationService
    slack_actions: SlackActionProcessor


def build_services(settings: Settings, repository: Repository) -> Services:
    generator = ContentGenerator(settings)
    trends = TrendCollector(settings, repository)
    safety = SafetyGuard()
    similarity = SimilarityGuard()
    feedback = FeedbackEngine(repository)
    integrations = IntegrationService(settings, repository)
    notifiers = NotifierManager.from_settings(settings, integrations)
    publishers = PublisherManager(settings)
    pipeline = Pipeline(
        settings,
        repository,
        generator,
        trends,
        safety,
        similarity,
        feedback,
        integrations,
        notifiers,
        publishers,
    )
    slack_actions = SlackActionProcessor(settings, repository, pipeline, notifiers)
    return Services(
        pipeline=pipeline,
        generator=generator,
        trend_collector=trends,
        safety_guard=safety,
        similarity_guard=similarity,
        feedback_engine=feedback,
        notifiers=notifiers,
        publishers=publishers,
        integrations=integrations,
        slack_actions=slack_actions,
    )
