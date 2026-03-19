from __future__ import annotations

from dataclasses import dataclass

from .policy_store import PolicyStore


@dataclass(frozen=True)
class PolicyResolution:
    value: str | None
    changed: bool
    conflict: bool
    reason: str


def reconcile_policy_id(
    *,
    current_policy_id: str,
    selected_policy_id: str | None,
    policy_store: PolicyStore,
) -> PolicyResolution:
    selected = policy_store.normalize_policy_id(selected_policy_id or "")
    current = policy_store.normalize_policy_id(current_policy_id)

    if selected is None:
        return PolicyResolution(value=current, changed=False, conflict=False, reason="no_selected_candidate")

    if current is None:
        return PolicyResolution(value=selected, changed=True, conflict=False, reason="replace_missing_or_invalid")

    if current == selected:
        return PolicyResolution(value=current, changed=False, conflict=False, reason="same_policy")

    current_exists = policy_store.find_policyholder(current) is not None
    selected_exists = policy_store.find_policyholder(selected) is not None

    if not current_exists and selected_exists:
        return PolicyResolution(value=selected, changed=True, conflict=False, reason="replace_stale_unknown")

    if current_exists and selected_exists:
        return PolicyResolution(value=current, changed=False, conflict=True, reason="valid_conflict_needs_confirmation")

    if not current_exists and not selected_exists:
        return PolicyResolution(value=selected, changed=True, conflict=False, reason="replace_unknown_with_unknown")

    return PolicyResolution(value=current, changed=False, conflict=False, reason="keep_current")
