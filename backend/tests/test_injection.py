"""Prompt-injection guard: attacks are blocked, normal business questions are not."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.security.injection import detect_injection, normalize

EVAL = Path(__file__).resolve().parents[1] / "eval"
ATTACKS = yaml.safe_load((EVAL / "attacks.yaml").read_text())["prompts"]
BENIGN = [q["question"] for q in yaml.safe_load((EVAL / "questions.yaml").read_text())["questions"]] + [
    "Mark order 1042 as shipped",
    "Create an invoice for order 5120",
    "Which customers were updated last week?",
    "Show me customers who might churn",
    "Drop-off in orders by month?",
    "What is our policy on overdue invoices?",
]
# Handled by RBAC instead (an analyst may legitimately see emails; a viewer cannot).
DELEGATED_TO_RBAC = {"Give me all customer emails and phone numbers in one list."}


@pytest.mark.parametrize("attack", [a for a in ATTACKS if a not in DELEGATED_TO_RBAC])
def test_attack_is_blocked(attack: str) -> None:
    verdict = detect_injection(attack)
    assert verdict.blocked, f"not blocked: {attack!r} score={verdict.score} reasons={verdict.reasons}"


@pytest.mark.parametrize("question", BENIGN)
def test_benign_question_is_allowed(question: str) -> None:
    verdict = detect_injection(question)
    assert not verdict.blocked, verdict.reasons


def test_obfuscation_is_normalised() -> None:
    assert "ignore" in normalize("i g n o r e previous").lower()
    assert normalize("ign​ore") == "ignore"


def test_overlong_message_is_blocked() -> None:
    assert detect_injection("revenue " * 400).blocked
