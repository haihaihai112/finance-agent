import asyncio
import threading

import fitz
import pytest

from finance_agents.core.settings import Settings
from finance_agents.storage.database import create_database_engine
from finance_agents.storage.document_store import DocumentStore
from finance_agents.tools.document_service import DocumentService


def test_keyed_locks_exclusion_independence_and_cleanup():
    from finance_agents.workflow.locks import KeyedLocks

    async def exercise():
        locks = KeyedLocks()
        active = peak = 0
        async def worker(key):
            nonlocal active, peak
            async with locks.hold(key):
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.01)
                active -= 1
        await asyncio.gather(*(worker("same") for _ in range(10)))
        assert peak == 1
        assert len(locks) == 0
        peak = 0
        await asyncio.gather(worker("a"), worker("b"))
        assert peak == 2
        assert len(locks) == 0

    asyncio.run(exercise())


def test_keyed_locks_cancelled_waiter_and_holder_cleanup():
    from finance_agents.workflow.locks import KeyedLocks

    async def exercise():
        locks = KeyedLocks()
        async with locks.hold("same"):
            async def wait():
                async with locks.hold("same"):
                    pytest.fail("cancelled waiter must not enter")
            waiter = asyncio.create_task(wait())
            await asyncio.sleep(0)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert len(locks) == 1
        assert len(locks) == 0
        entered = asyncio.Event()
        async def hold():
            async with locks.hold("holder"):
                entered.set()
                await asyncio.Event().wait()
        holder = asyncio.create_task(hold())
        await entered.wait()
        holder.cancel()
        with pytest.raises(asyncio.CancelledError):
            await holder
        assert len(locks) == 0

    asyncio.run(exercise())


class ObservedOCR:
    def __init__(self):
        self.calls = self.active = self.peak = 0
        self.entered = asyncio.Event()
        self.release = None

    async def extract(self, document_id, content, media_type):
        from finance_agents.contracts.documents import DocumentExtraction

        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.entered.set()
        try:
            if self.release is not None:
                await self.release.wait()
            else:
                await asyncio.sleep(0.02)
            return DocumentExtraction(document_id=document_id, text="synthetic", fields=[], warnings=[])
        finally:
            self.active -= 1


def documents(tmp_path, count):
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'docs.db').as_posix()}")
    store = DocumentStore(engine, tmp_path / "files")
    with fitz.open() as pdf:
        pdf.new_page().insert_text((30, 50), "monthly_income: 8000 CNY")
        content = pdf.tobytes()
    ids = [store.save("owner", f"{i}.pdf", content, "application/pdf")["document_id"] for i in range(count)]
    return engine, store, ids


def test_ocr_bounded_deduplicated_and_user_scoped(tmp_path):
    engine, store, ids = documents(tmp_path, 5)
    async def exercise():
        ocr = ObservedOCR()
        service = DocumentService(store, ocr, max_concurrency=2)
        results = await asyncio.gather(*(service.extract("owner", doc) for doc in ids for _ in range(3)))
        assert ocr.peak <= 2
        assert ocr.calls == len(ids)
        assert len(results) == 15
        assert results[0] is not results[1]
        with pytest.raises(PermissionError):
            await service.extract("other", ids[0])
        assert ocr.calls == len(ids)
        await service.extract("owner", ids[0])
        assert ocr.calls == len(ids)
        assert not service._inflight
    try:
        asyncio.run(exercise())
    finally:
        engine.dispose()


def test_cache_and_file_operations_run_off_event_loop(tmp_path):
    engine, store, ids = documents(tmp_path, 1)
    loop_thread = threading.get_ident()
    for name in ("get_info", "get_extraction", "read", "save_extraction"):
        original = getattr(store, name)
        def observed(*args, original=original, **kwargs):
            assert threading.get_ident() != loop_thread
            return original(*args, **kwargs)
        setattr(store, name, observed)
    try:
        asyncio.run(DocumentService(store, ObservedOCR()).extract("owner", ids[0]))
    finally:
        engine.dispose()


def test_cancellation_does_not_release_running_ocr_slot(tmp_path):
    engine, store, ids = documents(tmp_path, 2)
    async def exercise():
        ocr = ObservedOCR()
        ocr.release = asyncio.Event()
        service = DocumentService(store, ocr, max_concurrency=1)
        first = asyncio.create_task(service.extract("owner", ids[0]))
        await ocr.entered.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(service.extract("owner", ids[1]))
        await asyncio.sleep(0.03)
        assert ocr.calls == 1
        ocr.release.set()
        await second
        assert ocr.peak == 1
        assert not service._inflight
    try:
        asyncio.run(exercise())
    finally:
        engine.dispose()


def test_resource_configuration(monkeypatch):
    monkeypatch.setenv("OCR_MAX_CONCURRENCY", "3")
    assert Settings.from_env().ocr_max_concurrency == 3
    with pytest.raises(ValueError):
        DocumentService(None, max_concurrency=0)
