"""运行持久化、可信会话、乐观锁、幂等和显式模拟查询授权。"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, MetaData, String, Table
from sqlalchemy import UniqueConstraint, and_, func, insert, inspect, select, text, update
from sqlalchemy.exc import IntegrityError

from finance_agents.workflow.state import SessionState

metadata = MetaData()
sessions = Table(
    "runtime_sessions",
    metadata,
    Column("session_id", String(64), primary_key=True),
    Column("user_id", String(64), nullable=False),
    Column("token_hash", String(64), unique=True),
    Column("expires_at", DateTime, nullable=False),
)
runs = Table(
    "runtime_runs",
    metadata,
    Column("run_id", String(64), primary_key=True),
    Column("user_id", String(64), nullable=False),
    Column("version", Integer, nullable=False),
    Column("state", JSON, nullable=False),
    Column("created_at", DateTime, nullable=False, default=lambda: utcnow()),
)
consents = Table(
    "runtime_consents",
    metadata,
    Column("consent_ref", String(64), primary_key=True),
    Column("user_id", String(64), nullable=False),
    Column("run_id", String(64), nullable=False),
    Column("expires_at", DateTime, nullable=False),
    Column("active", Boolean, nullable=False),
)
requests = Table(
    "runtime_requests",
    metadata,
    Column("user_id", String(64), nullable=False),
    Column("request_id", String(128), nullable=False),
    Column("payload_hash", String(64), nullable=False),
    Column("result", JSON),
    Column("run_id", String(64)),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    UniqueConstraint("user_id", "request_id", name="uq_runtime_request"),
)
events = Table(
    "runtime_events",
    metadata,
    Column("event_id", String(64), primary_key=True),
    Column("run_id", String(64), nullable=False),
    Column("event_type", String(64), nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("payload", JSON, nullable=False),
)


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class RuntimeStore:
    def __init__(self, engine):
        self.engine = engine
        metadata.create_all(engine)
        self._migrate_run_creation_time()
        from finance_agents.storage.runtime_migrations import migrate_runtime_requests

        migrate_runtime_requests(engine)

    def _migrate_run_creation_time(self):
        """Legacy event time is inferred; absent history is explicitly unknown (epoch)."""
        if "created_at" not in {
            c["name"] for c in inspect(self.engine).get_columns("runtime_runs")
        }:
            with self.engine.begin() as connection:
                connection.execute(text("ALTER TABLE runtime_runs ADD COLUMN created_at DATETIME"))
        with self.engine.begin() as connection:
            earliest = (
                select(func.min(events.c.created_at))
                .where(events.c.run_id == runs.c.run_id)
                .scalar_subquery()
            )
            connection.execute(
                update(runs)
                .where(runs.c.created_at.is_(None))
                .values(created_at=func.coalesce(earliest, datetime(1970, 1, 1)))
            )

    def create_session(self, lifetime=timedelta(hours=24)):
        token = secrets.token_urlsafe(32)
        result = {
            "session_id": secrets.token_hex(16),
            "user_id": secrets.token_hex(16),
            "token": token,
        }
        with self.engine.begin() as connection:
            connection.execute(
                insert(sessions).values(
                    session_id=result["session_id"],
                    user_id=result["user_id"],
                    token_hash=hashlib.sha256(token.encode()).hexdigest(),
                    expires_at=utcnow() + lifetime,
                )
            )
        return result

    def authenticate(self, token):
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(sessions).where(
                        sessions.c.token_hash == hashlib.sha256(token.encode()).hexdigest(),
                    )
                )
                .mappings()
                .first()
            )
        if row is None or row["expires_at"] <= utcnow():
            raise PermissionError("会话凭据无效或已过期")
        return {"user_id": row["user_id"], "session_id": row["session_id"]}

    def create_run(self, state: SessionState):
        with self.engine.begin() as connection:
            connection.execute(
                insert(runs).values(
                    run_id=state.run_id,
                    user_id=state.user_id,
                    version=state.state_version,
                    state=state.model_dump(mode="json"),
                )
            )

    def get_run(self, run_id, user_id):
        with self.engine.connect() as connection:
            row = connection.execute(select(runs).where(runs.c.run_id == run_id)).mappings().first()
        if row is None:
            raise LookupError("运行不存在")
        if row["user_id"] != user_id:
            raise PermissionError("不能访问其他用户的运行")
        return SessionState.model_validate(row["state"])

    def save_run(self, state: SessionState, expected_version: int):
        with self.engine.begin() as connection:
            self._save_run(connection, state, expected_version)
        state.state_version = expected_version + 1

    def _save_run(self, connection, state, expected_version):
        data = state.model_copy(update={"state_version": expected_version + 1}).model_dump(
            mode="json"
        )
        result = connection.execute(
            update(runs)
            .where(
                and_(
                    runs.c.run_id == state.run_id,
                    runs.c.user_id == state.user_id,
                    runs.c.version == expected_version,
                )
            )
            .values(state=data, version=expected_version + 1)
        )
        if result.rowcount != 1:
            raise ValueError("运行版本冲突，请重新加载后提交")

    def list_runs(self, user_id, limit=50, offset=0):
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("分页参数超出范围")
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(runs.c.state)
                .where(runs.c.user_id == user_id)
                .order_by(runs.c.created_at.desc(), runs.c.run_id.desc())
                .limit(limit)
                .offset(offset)
            ).all()
        return [row[0] for row in rows]

    def grant_consent(self, user_id, run_id, lifetime=timedelta(minutes=30)):
        self.get_run(run_id, user_id)
        reference = secrets.token_hex(20)
        with self.engine.begin() as connection:
            connection.execute(
                insert(consents).values(
                    consent_ref=reference,
                    user_id=user_id,
                    run_id=run_id,
                    expires_at=utcnow() + lifetime,
                    active=True,
                )
            )
        return reference

    def check_consent(self, reference, user_id, run_id):
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(consents).where(
                        and_(
                            consents.c.consent_ref == reference,
                            consents.c.user_id == user_id,
                            consents.c.run_id == run_id,
                            consents.c.active.is_(True),
                        )
                    )
                )
                .mappings()
                .first()
            )
        return row is not None and row["expires_at"] > utcnow()

    def revoke_consent(self, reference, user_id):
        with self.engine.begin() as connection:
            connection.execute(
                update(consents)
                .where(
                    and_(
                        consents.c.consent_ref == reference,
                        consents.c.user_id == user_id,
                    )
                )
                .values(active=False)
            )

    def revoke_run_consents(self, user_id, run_id):
        self.get_run(run_id, user_id)
        with self.engine.begin() as connection:
            connection.execute(
                update(consents)
                .where(
                    and_(
                        consents.c.user_id == user_id,
                        consents.c.run_id == run_id,
                    )
                )
                .values(active=False)
            )

    def begin_request(self, user_id, request_id, payload):
        digest = hashlib.sha256(payload.encode()).hexdigest()
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    insert(requests).values(
                        user_id=user_id,
                        request_id=request_id,
                        payload_hash=digest,
                        result=None,
                        status="processing",
                        created_at=utcnow(),
                        updated_at=utcnow(),
                    )
                )
            return None
        except IntegrityError:
            with self.engine.connect() as connection:
                row = (
                    connection.execute(
                        select(requests).where(
                            and_(
                                requests.c.user_id == user_id,
                                requests.c.request_id == request_id,
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
            if row["payload_hash"] != digest:
                raise ValueError("相同请求 ID 不能对应不同内容")
            if row["status"] == "interrupted":
                raise ValueError("请求已中断，不能自动重放外部工具")
            if row["result"] is None:
                raise ValueError("请求正在处理或前次执行中断，请查询运行状态")
            return row["result"]

    def lookup_request(self, user_id, request_id, payload):
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(requests).where(
                        and_(
                            requests.c.user_id == user_id,
                            requests.c.request_id == request_id,
                        )
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        if row["payload_hash"] != hashlib.sha256(payload.encode()).hexdigest():
            raise ValueError("相同请求 ID 不能对应不同内容")
        if row["status"] == "interrupted":
            raise ValueError("请求已中断，不能自动重放外部工具")
        if row["result"] is None:
            raise ValueError("请求正在处理或前次执行中断，请查询运行状态")
        return row["result"]

    def finish_request(self, user_id, request_id, result):
        with self.engine.begin() as connection:
            changed = connection.execute(
                update(requests)
                .where(
                    and_(
                        requests.c.user_id == user_id,
                        requests.c.request_id == request_id,
                        requests.c.status == "processing",
                    )
                )
                .values(result=result, status="completed", updated_at=utcnow())
            )
            if changed.rowcount != 1:
                raise ValueError("请求已关闭，拒绝迟到结果")

    def _request_filter(self, user_id, request_id):
        return and_(requests.c.user_id == user_id, requests.c.request_id == request_id)

    def begin_run_request(self, state, request_id, payload, expected_version=None, result=None):
        """Reserve idempotency and persist the checkpoint in one transaction.

        A tool-free copy supplies its final response here and commits directly
        as completed, without exposing an intermediate processing checkpoint.
        """
        cached = self.lookup_request(state.user_id, request_id, payload)
        if cached is not None:
            return cached
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    insert(requests).values(
                        user_id=state.user_id,
                        request_id=request_id,
                        payload_hash=hashlib.sha256(payload.encode()).hexdigest(),
                        run_id=state.run_id,
                        status="completed" if result is not None else "processing",
                        result=result,
                        created_at=utcnow(),
                        updated_at=utcnow(),
                    )
                )
                if expected_version is None:
                    connection.execute(
                        insert(runs).values(
                            run_id=state.run_id,
                            user_id=state.user_id,
                            version=state.state_version,
                            state=state.model_dump(mode="json"),
                        )
                    )
                else:
                    self._save_run(connection, state, expected_version)
        except IntegrityError:
            # A concurrent registration may have won. Do not suppress unrelated errors.
            cached = self.lookup_request(state.user_id, request_id, payload)
            if cached is None:
                raise
            return cached
        if expected_version is not None:
            state.state_version = expected_version + 1
        return None

    def complete_run_request(self, state, request_id, expected_version, result):
        """CAS state and close the still-active request atomically."""
        with self.engine.begin() as connection:
            # Consistent request -> run row locking also used by interruption.
            active = connection.execute(
                select(requests.c.status)
                .where(
                    self._request_filter(state.user_id, request_id),
                    requests.c.run_id == state.run_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if active != "processing":
                raise ValueError("请求已关闭，拒绝迟到结果")
            self._save_run(connection, state, expected_version)
            changed = connection.execute(
                update(requests)
                .where(
                    self._request_filter(state.user_id, request_id),
                    requests.c.run_id == state.run_id,
                    requests.c.status == "processing",
                )
                .values(result=result, status="completed", updated_at=utcnow())
            )
            if changed.rowcount != 1:
                raise ValueError("请求已关闭，拒绝迟到结果")
        state.state_version = expected_version + 1

    def get_request(self, user_id, request_id):
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(
                        requests.c.request_id,
                        requests.c.run_id,
                        requests.c.status,
                        requests.c.created_at,
                        requests.c.updated_at,
                    ).where(self._request_filter(user_id, request_id))
                )
                .mappings()
                .first()
            )
        if row is None:
            raise LookupError("请求不存在")
        return dict(row)

    def interrupt_request(self, user_id, request_id):
        from finance_agents.workflow.state import RunStatus

        cutoff = utcnow() - timedelta(minutes=5)
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    select(requests).where(
                        self._request_filter(user_id, request_id),
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise LookupError("请求不存在")
            if row["status"] != "processing" or row["updated_at"] > cutoff:
                raise ValueError("仅可关闭超过 5 分钟无更新的 processing 请求")
            changed = connection.execute(
                update(requests)
                .where(
                    self._request_filter(user_id, request_id),
                    requests.c.status == "processing",
                    requests.c.updated_at <= cutoff,
                )
                .values(status="interrupted", updated_at=utcnow())
            )
            if changed.rowcount != 1:
                raise ValueError("请求状态已变化，请重新查询")
            if row["run_id"] is not None:
                saved = (
                    connection.execute(
                        select(runs).where(
                            runs.c.run_id == row["run_id"],
                            runs.c.user_id == user_id,
                        )
                    )
                    .mappings()
                    .one()
                )
                state = SessionState.model_validate(saved["state"])
                state.status = RunStatus.CANCELLED
                state.wait_reason = None
                state.pending_fields = []
                state.last_question = "请求已人工关闭；外部工具不会自动重放。"
                state.consent_ref = None
                if state.credit_form:
                    state.credit_form.consent_ref = None
                self._save_run(connection, state, saved["version"])
                connection.execute(
                    update(consents)
                    .where(
                        consents.c.user_id == user_id,
                        consents.c.run_id == state.run_id,
                    )
                    .values(active=False)
                )
                connection.execute(
                    insert(events).values(
                        event_id=secrets.token_hex(16),
                        run_id=state.run_id,
                        event_type="request_interrupted",
                        created_at=utcnow(),
                        payload={"request_id": request_id},
                    )
                )
        return self.get_request(user_id, request_id)

    def append_event(self, run_id, event_type, payload):
        with self.engine.begin() as connection:
            connection.execute(
                insert(events).values(
                    event_id=secrets.token_hex(16),
                    run_id=run_id,
                    event_type=event_type,
                    created_at=utcnow(),
                    payload=payload,
                )
            )

    def get_events(self, run_id, user_id):
        self.get_run(run_id, user_id)
        with self.engine.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    select(events).where(events.c.run_id == run_id).order_by(events.c.created_at),
                ).mappings()
            ]
