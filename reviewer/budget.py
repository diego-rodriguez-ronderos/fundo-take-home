"""Worst-case cost: computed before every call, and enforced.

A call can never cost more than
    (input_tokens_upper_bound * input_price + MAX_OUTPUT_TOKENS * output_price) * MAX_ATTEMPTS
because output is capped by max_tokens and the SDK retries at most MAX_RETRIES times.
We assume every attempt is billed in full, which overstates real cost (failed requests
are usually not billed). Input tokens are bounded from characters: across all 42 cached
calls the observed ratio was 3.12 to 3.19 characters per token; we divide by 2.0.
"""
import json
import os

MAX_OUTPUT_TOKENS = 4000      # observed ~50 tokens per pattern; 60-pattern chunks use ~3,000
MAX_PATTERNS_PER_CALL = 60    # bounds the size of any single prompt
MAX_RETRIES = 3
MAX_ATTEMPTS = 1 + MAX_RETRIES
CHARS_PER_TOKEN_LOWER_BOUND = 2.0
BUDGET_USD = float(os.environ.get("REVIEWER_BUDGET_USD", "10"))


def input_tokens_upper_bound(system: str, payload: dict) -> int:
    return int((len(system) + len(json.dumps(payload, indent=1))) / CHARS_PER_TOKEN_LOWER_BOUND) + 1


def worst_case_call_usd(prices: tuple, system: str, payload: dict) -> float:
    pin, pout = prices
    tin = input_tokens_upper_bound(system, payload)
    return (tin * pin + MAX_OUTPUT_TOKENS * pout) / 1e6 * MAX_ATTEMPTS


def worst_case_application_usd(prices: tuple, system: str, n_patterns: int,
                               chars_per_pattern: int = 1200, meta_chars: int = 300) -> float:
    """Upper bound for one application before we see it, from its pattern count.
    chars_per_pattern: observed max ~900 in our data, rounded up."""
    pin, pout = prices
    calls = max(1, -(-n_patterns // MAX_PATTERNS_PER_CALL))
    per_call_chars = len(system) + meta_chars + min(n_patterns, MAX_PATTERNS_PER_CALL) * chars_per_pattern
    tin = per_call_chars / CHARS_PER_TOKEN_LOWER_BOUND
    return calls * (tin * pin + MAX_OUTPUT_TOKENS * pout) / 1e6 * MAX_ATTEMPTS
