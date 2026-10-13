"""可选真实MySQL烟测：只使用显式配置的专用测试数据库。"""

import asyncio
import os
from uuid import uuid4

import pytest

from finance_agents.contracts.profiles import CreditForm
from finance_agents.core.settings import Settings
from finance_agents.workflow.service import WorkflowService
from finance_agents.workflow.state import SessionState


@pytest.mark.skipif(not os.getenv("MYSQL_TEST_URL"), reason="未配置真实MySQL专用测试库")
def test_mysql_real_runtime_and_four_stores(tmp_path):
    settings = Settings(
        data_dir=tmp_path, database_url=os.environ["MYSQL_TEST_URL"], llm_enabled=False
    )
    service = WorkflowService(settings)
    try:
        session = service.store.create_session()
        user_id = session["user_id"]
        state = SessionState(
            run_id=uuid4().hex,
            user_id=user_id,
            session_id=session["session_id"],
            credit_form=CreditForm(),
        )
        service.store.create_run(state)
        service.store.save_run(state, 1)
        assert service.store.get_run(state.run_id, user_id).state_version == 2
        demo = service.demo(user_id)
        assert len(demo["documents"]) == 2
        assert service.documents.read(user_id, demo["documents"][0]["document_id"]).startswith(
            b"%PDF"
        )
        assert service.claims.lookup_policies(user_id, "DEMO-POLICY-001", "demo-v1")
        first = asyncio.run(service.market.load_verified_snapshot())
        repeated = asyncio.run(service.market.load_verified_snapshot())
        assert first.rejected == 0
        assert repeated.inserted == 0 and repeated.updated == 0
        assert service.policy.search(
            "综合所得", "中国大陆", __import__("datetime").date(2026, 1, 1)
        )
    finally:
        service.engine.dispose()
