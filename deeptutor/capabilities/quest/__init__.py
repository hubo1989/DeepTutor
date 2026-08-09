"""
Kid Quest Capability — Gamified Learning for Children.

Three-stage pipeline: sourcing → forging → questing.
"""

from deeptutor.capabilities.quest.capability import KidQuestCapability
from deeptutor.capabilities.quest.forging import ForgingService, validate_schema, check_answerability
from deeptutor.capabilities.quest.grading import GradingService, LevelResult
from deeptutor.capabilities.quest.hints import HintService, STAGES
from deeptutor.capabilities.quest.quest_cache import compute_content_hash, get_cached, save_cache
from deeptutor.capabilities.quest.questing import run_quest_round

__all__ = [
    "KidQuestCapability",
    "ForgingService",
    "validate_schema",
    "check_answerability",
    "GradingService",
    "LevelResult",
    "HintService",
    "STAGES",
    "compute_content_hash",
    "get_cached",
    "save_cache",
    "run_quest_round",
]
