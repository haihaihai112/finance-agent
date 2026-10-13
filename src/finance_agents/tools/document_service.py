"""Authorized, deduplicated OCR with bounded work and threaded storage IO."""

import asyncio

from finance_agents.tools.ocr import DocumentOCR


class DocumentService:
    def __init__(self, store, ocr=None, max_concurrency=2):
        if (
            isinstance(max_concurrency, bool)
            or not isinstance(max_concurrency, int)
            or max_concurrency < 1
        ):
            raise ValueError("max_concurrency must be a positive integer")
        self.store = store
        self.ocr = ocr or DocumentOCR()
        self.max_concurrency = max_concurrency
        self._slots = asyncio.Semaphore(max_concurrency)
        self._inflight = {}

    async def extract(self, user_id, document_id):
        # Every caller authorizes before joining shared work, including a hit.
        info = await asyncio.to_thread(self.store.get_info, user_id, document_id)
        key = (user_id, document_id)
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._extract(user_id, document_id, info))
            self._inflight[key] = task
            task.add_done_callback(lambda done: self._finished(key, done))
        # Cancelling a request cannot terminate to_thread's OCR worker. Keep its
        # slot and cache ownership until actual completion; other callers share
        # that completion without receiving the same mutable result object.
        result = await asyncio.shield(task)
        return result.model_copy(deep=True)

    def _finished(self, key, task):
        if self._inflight.get(key) is task:
            del self._inflight[key]
        if not task.cancelled():
            task.exception()  # Retrieve failures even if every caller cancelled.

    async def _extract(self, user_id, document_id, info):
        cached = await asyncio.to_thread(self.store.get_extraction, user_id, document_id)
        if cached is not None:
            return cached
        async with self._slots:
            content = await asyncio.to_thread(self.store.read, user_id, document_id)
            result = await self.ocr.extract(document_id, content, info["media_type"])
            await asyncio.to_thread(self.store.save_extraction, user_id, document_id, result)
        return result
