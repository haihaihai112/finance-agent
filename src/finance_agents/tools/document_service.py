"""Authorize each access before returning or caching shared OCR results."""

from finance_agents.tools.ocr import DocumentOCR


class DocumentService:
    def __init__(self, store, ocr=None):
        self.store = store
        self.ocr = ocr or DocumentOCR()

    async def extract(self, user_id, document_id):
        info = self.store.get_info(user_id, document_id)
        cached = self.store.get_extraction(user_id, document_id)
        if cached is not None:
            return cached
        content = self.store.read(user_id, document_id)
        result = await self.ocr.extract(document_id, content, info["media_type"])
        self.store.save_extraction(user_id, document_id, result)
        return result
