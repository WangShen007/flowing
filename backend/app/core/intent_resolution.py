from __future__ import annotations

import re
from dataclasses import dataclass

_ACTION_PHRASES = (
    "发送一条飞书消息",
    "发送一条消息",
    "发送飞书消息",
    "发送一则通知",
    "发送一条通知",
    "发送消息",
    "发送通知",
    "发一条飞书消息",
    "发一条消息",
    "发一个消息",
    "发飞书消息",
    "发一个通知",
    "发一条通知",
    "发个消息",
    "发个通知",
    "发消息",
    "发通知",
)
_ACTION_CONTEXT_RE = re.compile(r"(?:给|向|在|群|私聊|消息|通知)")
_PAYLOAD_SEPARATOR_RE = re.compile(r"[：:]")


@dataclass(frozen=True)
class QueryNormalization:
    original_query: str
    normalized_query: str
    corrections: tuple[str, ...] = ()


def _edit_distance_at_most_one(left: str, right: str) -> int:
    """Return 0/1 for small edit distance and 2 when the distance is larger."""

    if left == right:
        return 0
    if abs(len(left) - len(right)) > 1:
        return 2
    if len(left) == len(right):
        return 1 if sum(a != b for a, b in zip(left, right)) == 1 else 2

    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    short_index = 0
    long_index = 0
    skipped = 0
    while short_index < len(shorter) and long_index < len(longer):
        if shorter[short_index] == longer[long_index]:
            short_index += 1
            long_index += 1
            continue
        skipped += 1
        long_index += 1
        if skipped > 1:
            return 2
    return 1


def normalize_action_typos(query: str) -> QueryNormalization:
    """Correct one-edit send-action typos without touching entities or payload text.

    Only an action-shaped suffix immediately before a colon is eligible. This is
    deliberately narrower than spell-checking the whole request: recipient names,
    group names and the user-authored message must remain byte-for-byte intact.
    """

    original = str(query or "")
    separator = _PAYLOAD_SEPARATOR_RE.search(original)
    if not separator:
        return QueryNormalization(original, original)

    header = original[: separator.start()]
    payload = original[separator.start() :]
    if not _ACTION_CONTEXT_RE.search(header):
        return QueryNormalization(original, original)

    trimmed_header = header.rstrip()
    trailing_space = header[len(trimmed_header) :]
    if any(trimmed_header.endswith(action) for action in _ACTION_PHRASES):
        return QueryNormalization(original, original)

    best: tuple[int, int, int, str, str] | None = None
    for canonical in _ACTION_PHRASES:
        for candidate_length in (len(canonical) - 1, len(canonical), len(canonical) + 1):
            if candidate_length < 2 or candidate_length > len(trimmed_header):
                continue
            observed = trimmed_header[-candidate_length:]
            distance = _edit_distance_at_most_one(observed, canonical)
            if distance != 1:
                continue
            # An equal-length one-character typo is much safer than guessing
            # that a character was omitted. Prefer that, then the longest
            # observed action so a shorter suffix cannot leave typo residue.
            rank = (int(candidate_length == len(canonical)), candidate_length, len(canonical))
            if best is None or rank > best[:3]:
                best = (*rank, observed, canonical)

    if best is None:
        return QueryNormalization(original, original)

    _, _, _, observed, canonical = best
    corrected_header = trimmed_header[: -len(observed)] + canonical + trailing_space
    normalized = corrected_header + payload
    return QueryNormalization(original, normalized, (f"{observed}→{canonical}",))
