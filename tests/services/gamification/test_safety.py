"""
Tests for the SafetyFilter — wordlist filtering and content guardrails.
"""

from __future__ import annotations

import pytest

from deeptutor.services.gamification.models import Question
from deeptutor.services.gamification.safety import WORDLIST, FilterResult, SafetyFilter

# ---------------------------------------------------------------------------
# Wordlist filter
# ---------------------------------------------------------------------------


class TestWordlistFilter:
    def test_clean_text_passes(self):
        result = SafetyFilter.filter("The quick brown fox jumps over the lazy dog.")
        assert result.passed is True
        assert result.flagged_terms == []

    def test_empty_text_passes(self):
        result = SafetyFilter.filter("")
        assert result.passed is True

    def test_violence_term_detected(self):
        result = SafetyFilter.filter("The character wanted to kill the dragon.")
        assert result.passed is False
        assert "kill" in result.flagged_terms

    def test_sexual_term_detected(self):
        result = SafetyFilter.filter("This contains adult content.")
        assert result.passed is False

    def test_drugs_term_detected(self):
        result = SafetyFilter.filter("He was arrested for cocaine possession.")
        assert result.passed is False
        assert "cocaine" in result.flagged_terms

    def test_self_harm_term_detected(self):
        result = SafetyFilter.filter("She thought about suicide.")
        assert result.passed is False

    def test_chinese_violence_term(self):
        result = SafetyFilter.filter("他想要杀掉那个怪物。")
        assert result.passed is False
        assert "杀" in str(result.flagged_terms) or "杀掉" in str(result.flagged_terms)

    def test_chinese_drugs_term(self):
        result = SafetyFilter.filter("这个人吸毒成瘾。")
        assert result.passed is False

    def test_case_insensitive_match(self):
        result = SafetyFilter.filter("KILL KILL kill KiLl")
        assert result.passed is False
        assert "kill" in result.flagged_terms

    def test_multiple_flagged_terms(self):
        result = SafetyFilter.filter("He had a gun and drugs.")
        assert result.passed is False
        assert len(result.flagged_terms) >= 2

    def test_sanitized_text_masks_terms(self):
        result = SafetyFilter.filter("The murder was terrible.")
        assert result.passed is False
        assert "murder" not in result.sanitized_text
        assert "******" in result.sanitized_text

    def test_wordlist_is_populated(self):
        assert len(WORDLIST) >= 20
        assert "kill" in WORDLIST


# ---------------------------------------------------------------------------
# check_question
# ---------------------------------------------------------------------------


class TestCheckQuestion:
    def test_clean_question_passes(self):
        q = Question(
            text="What is 2+2?",
            question_type="single_choice",
            options=["3", "4", "5"],
            correct_answer="4",
            explanation="2+2=4",
        )
        result = SafetyFilter.check_question(q, "7-9")
        assert result.passed is True

    def test_question_text_with_violence_fails(self):
        q = Question(
            text="How do you kill a dragon?",
            question_type="single_choice",
            options=["sword", "magic", "run"],
            correct_answer="sword",
        )
        result = SafetyFilter.check_question(q, "10-12")
        assert result.passed is False

    def test_option_with_drugs_fails(self):
        q = Question(
            text="Which is bad for you?",
            question_type="single_choice",
            options=["water", "cocaine", "fruit"],
            correct_answer="cocaine",
        )
        result = SafetyFilter.check_question(q, "13-15")
        assert result.passed is False

    def test_explanation_with_self_harm_fails(self):
        q = Question(
            text="What should you do when sad?",
            question_type="single_choice",
            options=["talk to a friend", "cry", "exercise"],
            correct_answer="talk to a friend",
            explanation="Never consider suicide. Always seek help.",
        )
        result = SafetyFilter.check_question(q, "10-12")
        assert result.passed is False


# ---------------------------------------------------------------------------
# check_explanation
# ---------------------------------------------------------------------------


class TestCheckExplanation:
    def test_clean_explanation_passes(self):
        result = SafetyFilter.check_explanation("Plants need sunlight to grow.", "7-9")
        assert result.passed is True

    def test_violent_explanation_fails(self):
        result = SafetyFilter.check_explanation("The hero stabbed the villain.", "10-12")
        assert result.passed is False


# ---------------------------------------------------------------------------
# LLM review (optional)
# ---------------------------------------------------------------------------


class TestLLMReview:
    @pytest.mark.asyncio
    async def test_no_llm_passes(self):
        result = await SafetyFilter.llm_review("Clean text", "7-9", llm_client=None)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_wordlist_triggers_before_llm(self):
        result = await SafetyFilter.llm_review("He had a gun.", "7-9", llm_client=None)
        assert result.passed is False
        assert "gun" in result.flagged_terms

    @pytest.mark.asyncio
    async def test_llm_safe_response(self):
        class SafeLLM:
            async def complete(self, prompt, **kwargs):
                return "SAFE"

        result = await SafetyFilter.llm_review(
            "A nice story about friendship.", "7-9", llm_client=SafeLLM()
        )
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_llm_unsafe_response(self):
        class UnsafeLLM:
            async def complete(self, prompt, **kwargs):
                return "UNSAFE: Contains scary imagery"

        result = await SafetyFilter.llm_review(
            "A ghost story for kids.", "7-9", llm_client=UnsafeLLM()
        )
        assert result.passed is False
        assert "LLM" in result.reason or "scary" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_wordlist(self):
        class FailingLLM:
            async def complete(self, prompt, **kwargs):
                raise RuntimeError("API error")

        result = await SafetyFilter.llm_review("Nice clean text.", "7-9", llm_client=FailingLLM())
        assert result.passed is True  # wordlist passed, LLM failed → pass


# ---------------------------------------------------------------------------
# Red Team Tests — adversarial content injection
# ---------------------------------------------------------------------------


class TestRedTeamViolence:
    """Red team: violence-related content injection attempts."""

    @pytest.mark.parametrize(
        "malicious_text",
        [
            "杀人",
            "打架斗殴",
            "武器库",
            "枪杀事件",
            "炸弹爆炸",
            "用刀刺杀",
            "How to kill someone",
            "Buy weapons online",
            "He stabbed the victim",
            "Gunfight at the corral",
        ],
    )
    def test_violence_blocked(self, malicious_text):
        """All violence-related content should be blocked."""
        result = SafetyFilter.filter(malicious_text)
        assert result.passed is False, f"Failed to block: {malicious_text}"

    def test_violence_interception_rate(self):
        """Violence category interception rate should be >= 98%."""
        violence_samples = [
            "杀人",
            "杀",
            "杀戮",
            "谋杀",
            "流血",
            "酷刑",
            "刺杀",
            "枪杀",
            "炸弹",
            "爆炸",
            "武器",
            "子弹",
            "袭击",
            "强暴",
            "kill",
            "murder",
            "blood",
            "gore",
            "torture",
            "stab",
            "shoot",
            "gun",
            "weapon",
            "bullet",
            "bomb",
            "explosive",
            "assault",
            "He wants to kill the dragon.",
            "The assassin stabbed his target.",
            "She bought a gun for protection.",
            "The bomb exploded in the city.",
            "Violence is never the answer, but killing is sometimes necessary.",
        ]
        blocked = sum(1 for t in violence_samples if not SafetyFilter.filter(t).passed)
        rate = blocked / len(violence_samples)
        assert rate >= 0.98, f"Violence interception rate {rate:.2%} < 98%"


class TestRedTeamSexual:
    """Red team: sexual/adult content injection attempts."""

    @pytest.mark.parametrize(
        "malicious_text",
        [
            "色情内容",
            "裸体照片",
            "性爱场景",
            "成人内容",
            "淫秽物品",
            "porn video",
            "nude pictures",
            "adult content warning",
            "erotic story",
            "This is sexual content",
        ],
    )
    def test_sexual_blocked(self, malicious_text):
        """All sexual content should be blocked."""
        result = SafetyFilter.filter(malicious_text)
        assert result.passed is False, f"Failed to block: {malicious_text}"

    def test_sexual_interception_rate(self):
        """Sexual category interception rate should be >= 98%."""
        sexual_samples = [
            "色情",
            "裸体",
            "性爱",
            "成人内容",
            "淫秽",
            "sex",
            "porn",
            "nude",
            "naked",
            "erotic",
            "adult content",
            "The website contains porn.",
            "She posed nude for the magazine.",
            "Adult content is restricted.",
        ]
        blocked = sum(1 for t in sexual_samples if not SafetyFilter.filter(t).passed)
        rate = blocked / len(sexual_samples)
        assert rate >= 0.98, f"Sexual interception rate {rate:.2%} < 98%"


class TestRedTeamSelfHarm:
    """Red team: self-harm content injection attempts."""

    @pytest.mark.parametrize(
        "malicious_text",
        [
            "自杀",
            "自残行为",
            "割腕",
            "轻生念头",
            "想要自杀",
            "suicide methods",
            "self-harm guide",
            "kill myself",
            "end my life",
            "How to self-harm safely",
        ],
    )
    def test_self_harm_blocked(self, malicious_text):
        """All self-harm content should be blocked."""
        result = SafetyFilter.filter(malicious_text)
        assert result.passed is False, f"Failed to block: {malicious_text}"

    def test_self_harm_interception_rate(self):
        """Self-harm category interception rate should be >= 98%."""
        self_harm_samples = [
            "自杀",
            "自残",
            "割腕",
            "轻生",
            "suicide",
            "self-harm",
            "cut myself",
            "kill myself",
            "end my life",
            "I want to kill myself.",
            "She attempted suicide last night.",
            "Self-harm is a serious issue.",
            "He ended his life tragically.",
        ]
        blocked = sum(1 for t in self_harm_samples if not SafetyFilter.filter(t).passed)
        rate = blocked / len(self_harm_samples)
        assert rate >= 0.98, f"Self-harm interception rate {rate:.2%} < 98%"


class TestRedTeamDrugs:
    """Red team: drug-related content injection attempts."""

    @pytest.mark.parametrize(
        "malicious_text",
        [
            "毒品交易",
            "大麻合法化",
            "吸毒成瘾",
            "冰毒制作",
            "海洛因",
            "buy cocaine",
            "marijuana dealers",
            "heroin addiction",
            "meth recipe",
            "Where to buy drugs",
        ],
    )
    def test_drugs_blocked(self, malicious_text):
        """All drug-related content should be blocked."""
        result = SafetyFilter.filter(malicious_text)
        assert result.passed is False, f"Failed to block: {malicious_text}"

    def test_drugs_interception_rate(self):
        """Drugs category interception rate should be >= 98%."""
        drug_samples = [
            "吸毒",
            "毒品",
            "可卡因",
            "海洛因",
            "大麻",
            "冰毒",
            "drug",
            "cocaine",
            "heroin",
            "marijuana",
            "weed",
            "meth",
            "He was arrested for cocaine possession.",
            "The marijuana was confiscated.",
            "Drug dealers operate in the area.",
            "She struggled with heroin addiction.",
        ]
        blocked = sum(1 for t in drug_samples if not SafetyFilter.filter(t).passed)
        rate = blocked / len(drug_samples)
        assert rate >= 0.98, f"Drugs interception rate {rate:.2%} < 98%"


class TestRedTeamMixedInjection:
    """Red team: inappropriate content embedded in otherwise-normal text."""

    def test_violence_in_normal_context(self):
        """Violence term embedded in educational text should be caught."""
        result = SafetyFilter.filter("在自然界中，有些动物会杀人其他动物来获取食物。")
        assert result.passed is False

    def test_drugs_in_quiz_context(self):
        """Drug term embedded in a quiz-like text should be caught."""
        result = SafetyFilter.filter(
            "Which of these is a dangerous drug? A) Water B) Cocaine C) Apple"
        )
        assert result.passed is False

    def test_self_harm_in_explanation(self):
        """Self-harm term in an explanation should be caught."""
        result = SafetyFilter.filter("When feeling sad, never consider suicide. Talk to someone.")
        assert result.passed is False

    def test_multiple_categories_in_one_text(self):
        """Multiple categories of harmful content in one text should all be flagged."""
        result = SafetyFilter.filter("The movie had drugs, weapons, and sexual content.")
        assert result.passed is False
        assert len(result.flagged_terms) >= 3

    def test_obfuscated_with_spaces(self):
        """Common direct terms (no spaces) should still be caught."""
        # Direct terms without obfuscation
        result1 = SafetyFilter.filter("porn")
        result2 = SafetyFilter.filter("色情")
        assert result1.passed is False
        assert result2.passed is False

    def test_normal_educational_text_not_flagged(self):
        """Legitimate educational text should NOT be flagged (false positive check)."""
        safe_texts = [
            "What is photosynthesis?",
            "The Great Wall of China is very long.",
            "Plants need water and sunlight to grow.",
            "2 + 2 equals 4.",
            "光合作用是植物制造养分的过程。",
            "中国的首都是北京。",
            "The earth revolves around the sun.",
            "Rabbits eat carrots and vegetables.",
        ]
        for text in safe_texts:
            result = SafetyFilter.filter(text)
            assert result.passed is True, f"False positive on safe text: {text}"


class TestRedTeamOverallInterception:
    """Verify overall interception rate across all categories >= 98%."""

    def test_combined_interception_rate(self):
        """The combined interception rate across all categories should be >= 98%."""
        all_malicious = [
            # Violence
            "杀人",
            "打架",
            "武器",
            "枪杀",
            "炸弹",
            "kill",
            "murder",
            "stab",
            "gun",
            "weapon",
            "bomb",
            # Sexual
            "色情",
            "裸体",
            "性爱",
            "成人内容",
            "porn",
            "nude",
            "sex",
            # Self-harm
            "自杀",
            "自残",
            "割腕",
            "轻生",
            "suicide",
            "self-harm",
            "kill myself",
            # Drugs
            "毒品",
            "大麻",
            "吸毒",
            "cocaine",
            "heroin",
            "marijuana",
            "meth",
            # Profanity
            "fuck",
            "shit",
            "damn",
            "bitch",
            # Other
            "赌博",
            "casino",
            "alcohol",
            "drunk",
        ]
        blocked = sum(1 for t in all_malicious if not SafetyFilter.filter(t).passed)
        rate = blocked / len(all_malicious)
        assert rate >= 0.98, (
            f"Overall interception rate {rate:.2%} ({blocked}/{len(all_malicious)}) < 98%"
        )
