"""Prompt-injection and abuse detection for incoming user messages.

This is a fast, explainable *first filter*. It is deliberately not the only
defence: even if an attack slips past it, the SQL validator, RBAC, the
read-only DB role and human confirmation still apply. Each rule has a weight;
a message is blocked when the total risk score reaches the threshold.
"""

from __future__ import annotations

import base64
import re
import unicodedata
from dataclasses import dataclass, field

BLOCK_THRESHOLD = 1.0
MAX_MESSAGE_CHARS = 2000

# (name, pattern, weight)
RULES: list[tuple[str, re.Pattern[str], float]] = [
    (
        "override_instructions",
        re.compile(
            r"\b(ignore|disregard|forget|override|bypass)\b.{0,40}\b(previous|prior|above|earlier|all|your|the|system)\b.{0,30}"
            r"\b(instructions?|rules?|prompts?|guardrails?|restrictions?|policies|policy)\b",
            re.I,
        ),
        1.0,
    ),
    (
        "role_hijack",
        re.compile(
            r"\b(you are now|act as|pretend (to be|you are)|roleplay as|from now on you)\b.{0,60}"
            r"\b(admin|root|dba|superuser|unrestricted|developer|jailbroken|dan)\b",
            re.I,
        ),
        1.0,
    ),
    ("developer_mode", re.compile(r"\b(developer|debug|god|sudo|maintenance|jailbreak)\s*mode\b|\bDAN\b", re.I), 0.8),
    (
        "disable_guardrails",
        re.compile(
            r"\b(disable|turn off|switch off|remove|deactivate|bypass)\b.{0,30}\b(guardrails?|safety|filters?|restrictions?|"
            r"security|validation|read[- ]only)\b",
            re.I,
        ),
        1.0,
    ),
    (
        "secret_request",
        re.compile(
            r"\b(database|db|admin|root|api|llm|openai|anthropic)\s+(password|credentials?|key|token|secret)s?\b|"
            r"\bconnection strings?\b|\bjwt secret\b",
            re.I,
        ),
        1.0,
    ),
    (
        "prompt_exfiltration",
        re.compile(
            r"\b(reveal|show|print|repeat|output|leak|tell me)\b.{0,40}\b(system prompt|your prompt|your instructions|"
            r"hidden instructions|initial prompt|api key|secret|password|connection string)\b",
            re.I,
        ),
        1.0,
    ),
    (
        "raw_sql_write",
        re.compile(
            r"\b(drop|truncate|alter)\s+(table|database|schema|role|user)\b|\bdelete\s+from\b|\binsert\s+into\b|"
            r"\bupdate\s+\w+\s+set\b|\bgrant\s+\w+\s+on\b|\bcreate\s+(table|role|user|function)\b",
            re.I,
        ),
        1.0,
    ),
    (
        "sql_injection_syntax",
        re.compile(r"(;\s*(--|/\*)|'\s*or\s+'?1'?\s*=\s*'?1|\bunion\s+select\b.*\bfrom\b\s+pg_)", re.I),
        0.8,
    ),
    (
        "system_catalog",
        re.compile(r"\b(pg_catalog|information_schema|pg_shadow|pg_authid|pg_user|audit\.events|audit schema)\b", re.I),
        1.0,
    ),
    (
        "dangerous_functions",
        re.compile(r"\b(pg_sleep|pg_read_file|lo_import|dblink|copy\s+\w+\s+(to|from)|set_config)\b", re.I),
        1.0,
    ),
    (
        "privilege_escalation",
        re.compile(
            r"\b(change|switch|set|elevate|give me)\b.{0,20}\b(my )?(role|permissions?|access|privileges?)\b.{0,20}\b(admin|finance|root|superuser)\b",
            re.I,
        ),
        1.0,
    ),
    (
        "skip_confirmation",
        re.compile(
            r"\b(without|skip|no need for|don'?t ask( for)?|bypass)\b.{0,20}\b(confirm(ation)?|approval|asking)\b", re.I
        ),
        1.0,
    ),
    (
        "mass_exfiltration",
        re.compile(
            r"\b(all|every|entire|full)\b.{0,25}\b(emails?|phone numbers?|salar(y|ies)|passwords?|credit card)", re.I
        ),
        0.6,
    ),
    (
        "delimiter_spoofing",
        re.compile(r"</?(system|assistant|schema|question|instructions?)>|\[/?INST\]|<\|im_(start|end)\|>", re.I),
        1.0,
    ),
]


@dataclass
class InjectionVerdict:
    blocked: bool
    score: float
    reasons: list[str] = field(default_factory=list)
    normalized: str = ""


def normalize(text: str) -> str:
    """Undo common obfuscation: unicode look-alikes, zero-width chars, spaced-out letters."""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[​-‏⁠﻿]", "", text)
    text = re.sub(r"\b(?:\w\s){4,}\w\b", lambda m: m.group(0).replace(" ", ""), text)  # "i g n o r e"
    return re.sub(r"\s+", " ", text).strip()


def _decoded_base64_chunks(text: str) -> list[str]:
    out = []
    for chunk in re.findall(r"[A-Za-z0-9+/]{24,}={0,2}", text):
        try:
            decoded = base64.b64decode(chunk, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        if decoded.isprintable():
            out.append(decoded)
    return out


def detect_injection(message: str, threshold: float = BLOCK_THRESHOLD) -> InjectionVerdict:
    """Score a user message. Blocks when the summed rule weights reach ``threshold``."""
    if len(message) > MAX_MESSAGE_CHARS:
        return InjectionVerdict(True, 1.0, [f"message_too_long (> {MAX_MESSAGE_CHARS} chars)"], message[:200])
    normalized = normalize(message)
    candidates = [normalized] + [normalize(d) for d in _decoded_base64_chunks(normalized)]
    score, reasons = 0.0, []
    for name, pattern, weight in RULES:
        if any(pattern.search(c) for c in candidates):
            score += weight
            reasons.append(name)
    if len(candidates) > 1 and reasons:
        reasons.append("hidden_in_base64")
        score += 0.5
    return InjectionVerdict(score >= threshold, round(score, 2), reasons, normalized)
