from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path


@dataclass
class ExpiryInfo:
    due_date: date
    days_until_due: int
    is_expired: bool
    expires_soon: bool


class PolicyStore:
    _POLICY_ID_RE = re.compile(r"^POL-\d{4}$")

    def __init__(self, data_dir: Path | None = None) -> None:
        base_dir = data_dir or (Path(__file__).resolve().parent / "data")
        self._data_dir = base_dir
        self._policyholders_path = self._data_dir / "policyholders.json"
        self._plans_path = self._data_dir / "plans.json"

    def normalize_policy_id(self, value: str) -> str | None:
        candidate = value.strip().upper()
        if self._POLICY_ID_RE.fullmatch(candidate):
            return candidate
        return None

    def find_policyholder(self, policy_id: str) -> dict | None:
        normalized = self.normalize_policy_id(policy_id)
        if normalized is None:
            return None
        for holder in self._read_policyholders():
            if str(holder.get("policy_id", "")).strip().upper() == normalized:
                return holder
        return None

    def list_plans(self) -> list[dict]:
        plans = self._read_plans()
        return sorted(plans, key=lambda item: int(item.get("monthly_price_usd", 0)))

    def get_plan(self, plan_id: str) -> dict | None:
        for plan in self._read_plans():
            if str(plan.get("id", "")).strip() == plan_id.strip():
                return plan
        return None

    def list_plan_ids(self) -> list[str]:
        return [str(plan.get("id", "")).strip() for plan in self.list_plans() if str(plan.get("id", "")).strip()]

    def renewal_expiry_info(self, holder: dict, *, today: date) -> ExpiryInfo | None:
        base_date_raw = holder.get("last_renewal_date") or holder.get("policy_start_date")
        if not isinstance(base_date_raw, str) or not base_date_raw.strip():
            return None
        try:
            base_date = date.fromisoformat(base_date_raw)
        except ValueError:
            return None
        due_date = base_date + timedelta(days=365)
        days_until_due = (due_date - today).days
        return ExpiryInfo(
            due_date=due_date,
            days_until_due=days_until_due,
            is_expired=days_until_due < 0,
            expires_soon=0 <= days_until_due <= 30,
        )

    def can_change_clinic(self, holder: dict, *, today: date) -> tuple[bool, date | None]:
        raw = holder.get("last_clinic_change_date")
        if not isinstance(raw, str) or not raw.strip():
            return True, None
        try:
            last_changed = date.fromisoformat(raw)
        except ValueError:
            return False, None
        eligible_on = last_changed + timedelta(days=365)
        return today >= eligible_on, eligible_on

    def renew_policy(self, *, policy_id: str, new_plan_id: str, today: date) -> dict | None:
        normalized = self.normalize_policy_id(policy_id)
        if normalized is None:
            return None
        if self.get_plan(new_plan_id) is None:
            return None

        holders = self._read_policyholders()
        updated: dict | None = None
        for holder in holders:
            if str(holder.get("policy_id", "")).strip().upper() != normalized:
                continue
            holder["current_plan_id"] = new_plan_id
            holder["last_renewal_date"] = today.isoformat()
            holder["status"] = "active"
            updated = holder
            break

        if updated is None:
            return None
        self._policyholders_path.write_text(json.dumps(holders, indent=2), encoding="utf-8")
        return updated

    def update_clinic(self, *, policy_id: str, clinic_id: str, today: date) -> dict | None:
        normalized = self.normalize_policy_id(policy_id)
        if normalized is None:
            return None

        holders = self._read_policyholders()
        updated: dict | None = None
        for holder in holders:
            if str(holder.get("policy_id", "")).strip().upper() != normalized:
                continue
            holder["assigned_clinic_id"] = clinic_id
            holder["last_clinic_change_date"] = today.isoformat()
            updated = holder
            break

        if updated is None:
            return None
        self._policyholders_path.write_text(json.dumps(holders, indent=2), encoding="utf-8")
        return updated

    def _read_policyholders(self) -> list[dict]:
        if not self._policyholders_path.exists():
            return []
        payload = json.loads(self._policyholders_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else []

    def _read_plans(self) -> list[dict]:
        if not self._plans_path.exists():
            return []
        payload = json.loads(self._plans_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else []
