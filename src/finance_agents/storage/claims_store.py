"""Scoped policy histories with explicit source modes and versioned terms."""

import json
from datetime import date
from uuid import uuid4

from sqlalchemy import Column, MetaData, String, Table, Text, UniqueConstraint, select


class ClaimsStore:
    def __init__(self, engine):
        self.engine = engine
        metadata = MetaData()
        self.policies = Table(
            "claim_policy_history",
            metadata,
            Column("record_id", String(64), primary_key=True),
            Column("user_id", String(255), nullable=False, index=True),
            Column("policy_id", String(128), nullable=False),
            Column("policy_version", String(128), nullable=False),
            Column("payload_json", Text, nullable=False),
            UniqueConstraint(
                "user_id", "policy_id", "policy_version", name="uq_policy_owner_version"
            ),
        )
        metadata.create_all(engine)

    def put_policy(self, user_id, policy):
        policy = dict(policy)
        for key in ("policy_id", "policy_version", "effective_from", "effective_to", "data_mode"):
            if not policy.get(key):
                raise ValueError(f"Policy requires {key}")
        start, end = (
            date.fromisoformat(policy["effective_from"]),
            date.fromisoformat(policy["effective_to"]),
        )
        if end < start or policy["data_mode"] not in ("real", "redacted", "mock"):
            raise ValueError("Invalid policy dates or data_mode")
        if policy["data_mode"] != "mock" and not policy.get("source_uri"):
            raise ValueError("Real/redacted policies require a source URI")
        if not user_id:
            raise ValueError("User identity is required")
        where = (
            self.policies.c.user_id == user_id,
            self.policies.c.policy_id == policy["policy_id"],
            self.policies.c.policy_version == policy["policy_version"],
        )
        payload = json.dumps(policy, ensure_ascii=False)
        with self.engine.begin() as conn:
            existing = conn.execute(select(self.policies.c.record_id).where(*where)).first()
            if existing:
                conn.execute(self.policies.update().where(*where).values(payload_json=payload))
            else:
                conn.execute(
                    self.policies.insert().values(
                        record_id=uuid4().hex,
                        user_id=user_id,
                        policy_id=policy["policy_id"],
                        policy_version=policy["policy_version"],
                        payload_json=payload,
                    )
                )
        return policy

    def lookup_policies(self, user_id, policy_id=None, policy_version=None):
        stmt = select(self.policies.c.payload_json).where(self.policies.c.user_id == user_id)
        if policy_id:
            stmt = stmt.where(self.policies.c.policy_id == policy_id)
        if policy_version:
            stmt = stmt.where(self.policies.c.policy_version == policy_version)
        with self.engine.connect() as conn:
            return [json.loads(row[0]) for row in conn.execute(stmt)]

    def seed_demo(self, user_id):
        """Only explicit callers receive synthetic samples; never seed all users globally."""
        policy = self.put_policy(
            user_id,
            {
                "policy_id": "DEMO-POLICY-001",
                "policy_version": "demo-v1",
                "insurer": "Synthetic Demonstration Insurer",
                "insurance_type": "demo_medical",
                "effective_from": "2025-01-01",
                "effective_to": "2030-12-31",
                "data_mode": "mock",
                "source_title": "Synthetic policy and terms for local workflow demonstration",
                "clauses": [
                    {
                        "clause_id": "demo-hospital",
                        "incident_types": ["hospitalization", "住院"],
                        "text": "Synthetic demo clause: hospitalization during coverage dates may be covered; final evidence and review required.",
                    }
                ],
                "warnings": ["Synthetic sample; not a real insurance policy or legal contract."],
            },
        )
        return [policy]
