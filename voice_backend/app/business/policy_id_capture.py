from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PolicyIdCandidate:
    id: str
    source: str
    normalized: str
    raw: str


class PolicyIdCapture:
    CANDIDATE_NONE_SENTINEL = "__NONE__"
    _POLICY_RE = re.compile(r"^POL-\d{4}$")
    _TOKEN_RE = re.compile(r"[a-zA-Z]+|\d+")
    _DIGIT_WORDS = {
        "zero": "0",
        "one": "1",
        "two": "2",
        "three": "3",
        "four": "4",
        "five": "5",
        "six": "6",
        "seven": "7",
        "eight": "8",
        "nine": "9",
        # "oh" is a common STT variant for zero in spoken numbers.
        "oh": "0",
    }

    def __init__(self, *, recent_user_turn_window: int = 3) -> None:
        self._recent_user_turn_window = recent_user_turn_window

    def build_candidates(
        self,
        *,
        text: str,
        history: list[dict[str, str]],
        source: str,
    ) -> list[PolicyIdCandidate]:
        candidates: list[PolicyIdCandidate] = []

        explicit = self._parse_explicit_policy_id(text)
        if explicit is not None:
            candidates.append(
                PolicyIdCandidate(
                    id=f"{source}_explicit_1",
                    source=source,
                    normalized=explicit,
                    raw=text,
                )
            )

        recent_user_texts = self._recent_user_texts(history)
        window_texts = list(recent_user_texts)
        if not window_texts or window_texts[-1] != text:
            window_texts.append(text)
        split = self._assemble_split_policy_id(window_texts)
        if split is not None:
            candidates.append(
                PolicyIdCandidate(
                    id=f"{source}_split_1",
                    source=source,
                    normalized=split,
                    raw=text,
                )
            )

        return self._dedupe_candidates(candidates)

    def resolve_candidate(
        self,
        *,
        candidates: list[PolicyIdCandidate],
        selected_candidate_id: str | None,
    ) -> tuple[PolicyIdCandidate | None, str]:
        normalized_selected_id = sanitize_candidate_selection(
            selected_candidate_id=selected_candidate_id,
            candidate_ids=[candidate.id for candidate in candidates],
        )
        if normalized_selected_id:
            for candidate in candidates:
                if candidate.id == normalized_selected_id:
                    return candidate, "selected"
        fallback = self._pick_best_candidate(candidates)
        if fallback is None:
            return None, "none"
        return fallback, "fallback"

    def context_payload(self, candidates: list[PolicyIdCandidate]) -> list[dict[str, str]]:
        return [
            {
                "id": candidate.id,
                "source": candidate.source,
                "normalized": candidate.normalized,
            }
            for candidate in candidates
        ]

    def merge_candidates(self, candidates: list[PolicyIdCandidate]) -> list[PolicyIdCandidate]:
        return self._dedupe_candidates(candidates)

    def normalize_strict(self, value: str) -> str | None:
        normalized = value.strip().upper()
        if self._POLICY_RE.fullmatch(normalized):
            return normalized
        return None

    def _parse_explicit_policy_id(self, text: str) -> str | None:
        collapsed = self._collapse_policy_like_text(text)
        policy_idx = collapsed.find("POLICY")
        policy_prefix_len = 6
        if policy_idx < 0:
            policy_idx = collapsed.find("POL")
            policy_prefix_len = 3
        if policy_idx < 0:
            return None
        tail = collapsed[policy_idx + policy_prefix_len :]
        digit_stream = "".join(ch for ch in tail if ch.isdigit())
        if len(digit_stream) == 4:
            return f"POL-{digit_stream}"
        if len(digit_stream) == 5 and digit_stream.startswith("0"):
            return f"POL-{digit_stream[1:]}"
        return None

    def _assemble_split_policy_id(self, texts: list[str]) -> str | None:
        if not texts:
            return None
        recent = texts[-self._recent_user_turn_window :]
        chunks = [(self._has_policy_prefix(text), self._extract_digit_stream(text)) for text in recent]
        has_prefix = any(prefix for prefix, _ in chunks)
        if not has_prefix:
            return None

        combined = "".join(chunk for _, chunk in chunks if chunk)
        if len(combined) != 4:
            return None
        return f"POL-{combined}"

    def _recent_user_texts(self, history: list[dict[str, str]]) -> list[str]:
        user_texts: list[str] = []
        for item in reversed(history):
            if item.get("role") != "user":
                continue
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            user_texts.append(content)
            if len(user_texts) >= self._recent_user_turn_window:
                break
        user_texts.reverse()
        return user_texts

    def _has_policy_prefix(self, text: str) -> bool:
        letters_only = re.sub(r"[^A-Z]", "", text.upper())
        return "POLICY" in letters_only or "POL" in letters_only

    def _extract_digit_stream(self, text: str) -> str:
        tokens = self._TOKEN_RE.findall(text.lower())
        digits: list[str] = []
        idx = 0
        while idx < len(tokens):
            repeated, next_idx = self._extract_repeated_digits(tokens, idx)
            if repeated is not None:
                digits.append(repeated)
                idx = next_idx
                continue
            token = tokens[idx]
            if token.isdigit():
                digits.append(token)
                idx += 1
                continue
            if token == "o" and self._letter_o_is_numeric(tokens, idx):
                digits.append("0")
                idx += 1
                continue
            mapped = self._single_digit_from_token(token)
            if mapped is not None:
                digits.append(mapped)
            idx += 1
        return "".join(digits)

    def _collapse_policy_like_text(self, text: str) -> str:
        tokens = self._TOKEN_RE.findall(text.lower())
        parts: list[str] = []
        idx = 0
        while idx < len(tokens):
            repeated, next_idx = self._extract_repeated_digits(tokens, idx)
            if repeated is not None:
                parts.append(repeated)
                idx = next_idx
                continue
            token = tokens[idx]
            if token.isdigit():
                parts.append(token)
                idx += 1
                continue
            if token == "o" and self._letter_o_is_numeric(tokens, idx):
                parts.append("0")
                idx += 1
                continue
            mapped = self._single_digit_from_token(token)
            if mapped is not None:
                parts.append(mapped)
                idx += 1
                continue
            parts.append(token.upper())
            idx += 1
        return "".join(parts)

    def _single_digit_from_token(self, token: str) -> str | None:
        if len(token) == 1 and token.isdigit():
            return token
        return self._DIGIT_WORDS.get(token)

    def _extract_repeated_digits(self, tokens: list[str], idx: int) -> tuple[str | None, int]:
        token = tokens[idx]
        repeat_map = {"double": 2, "triple": 3}
        repeat_count = repeat_map.get(token)
        if repeat_count is None:
            return None, idx
        if idx + 1 >= len(tokens):
            return None, idx
        repeated_token = tokens[idx + 1]
        if repeated_token == "o":
            repeated_digit = "0"
        else:
            repeated_digit = self._single_digit_from_token(repeated_token)
        if repeated_digit is None:
            return None, idx
        return repeated_digit * repeat_count, idx + 2

    def _letter_o_is_numeric(self, tokens: list[str], idx: int) -> bool:
        prev_token = tokens[idx - 1] if idx > 0 else None
        next_token = tokens[idx + 1] if idx + 1 < len(tokens) else None
        return self._is_numeric_context_token(prev_token) or self._is_numeric_context_token(next_token)

    def _is_numeric_context_token(self, token: str | None) -> bool:
        if not token:
            return False
        if token.isdigit():
            return True
        if token in self._DIGIT_WORDS:
            return True
        return token in {"double", "triple"}

    def _dedupe_candidates(self, candidates: list[PolicyIdCandidate]) -> list[PolicyIdCandidate]:
        deduped: list[PolicyIdCandidate] = []
        seen: set[str] = set()
        for candidate in candidates:
            if candidate.normalized in seen:
                continue
            seen.add(candidate.normalized)
            deduped.append(candidate)
        return deduped

    def _pick_best_candidate(self, candidates: list[PolicyIdCandidate]) -> PolicyIdCandidate | None:
        if not candidates:
            return None
        source_order = {
            "raw": 3,
            "raw_split": 2,
            "extracted": 1,
            "extracted_split": 0,
        }
        return max(candidates, key=lambda c: (source_order.get(c.source, -1), 1 if "explicit" in c.id else 0))


def set_policy_candidate_schema_field(schema: dict[str, Any], candidate_ids: list[str]) -> None:
    set_candidate_selection_schema_field(
        schema=schema,
        field_name="selected_policy_candidate_id",
        candidate_ids=candidate_ids,
        when_candidates_description="Select one policy candidate id from context when available.",
        when_empty_description="No policy candidate available this turn; use __NONE__.",
    )


def set_candidate_selection_schema_field(
    *,
    schema: dict[str, Any],
    field_name: str,
    candidate_ids: list[str],
    when_candidates_description: str,
    when_empty_description: str,
) -> None:
    properties = schema.get("properties", {})
    field = properties.get(field_name)
    if not isinstance(field, dict):
        return
    cleaned_ids: list[str] = []
    for candidate_id in candidate_ids:
        value = str(candidate_id).strip()
        if not value or value == PolicyIdCapture.CANDIDATE_NONE_SENTINEL or value in cleaned_ids:
            continue
        cleaned_ids.append(value)
    field.clear()
    if cleaned_ids:
        field.update(
            {
                "type": "string",
                "enum": [*cleaned_ids, PolicyIdCapture.CANDIDATE_NONE_SENTINEL],
                "description": when_candidates_description,
            }
        )
    else:
        field.update(
            {
                "type": "string",
                "enum": [PolicyIdCapture.CANDIDATE_NONE_SENTINEL],
                "description": when_empty_description,
            }
        )


def sanitize_candidate_selection(
    *,
    selected_candidate_id: str | None,
    candidate_ids: list[str],
) -> str | None:
    if not isinstance(selected_candidate_id, str):
        return None
    selected = selected_candidate_id.strip()
    if not selected or selected == PolicyIdCapture.CANDIDATE_NONE_SENTINEL:
        return None
    if selected not in candidate_ids:
        return None
    return selected
