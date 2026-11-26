"""Shared bounded jitter for durable transport and execution retries."""

import random


def backoff(attempts: int) -> int:
    return random.randint(1, min(300, 2 ** min(max(attempts, 0) + 1, 9)))
