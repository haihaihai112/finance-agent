"""User-scoped file metadata and extraction cache, backed by SQLAlchemy."""

import hashlib
import io
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Column, Integer, MetaData, String, Table, Text, select, update


class DocumentStore:
    def __init__(self, engine, storage_dir, max_size_bytes=10 * 1024 * 1024):
        self.engine = engine
        self.storage_dir = Path(storage_dir).resolve()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.max_size_bytes = max_size_bytes
        self.metadata = MetaData()
        self.documents = Table(
            "uploaded_documents",
            self.metadata,
            Column("document_id", String(64), primary_key=True),
            Column("user_id", String(255), nullable=False, index=True),
            Column("filename", String(255), nullable=False),
            Column("media_type", String(100), nullable=False),
            Column("size", Integer, nullable=False),
            Column("sha256", String(64), nullable=False),
            Column("extraction_json", Text, nullable=True),
        )
        self.metadata.create_all(engine)

    def save(self, user_id, filename, content, media_type):
        if not user_id or not content or len(content) > self.max_size_bytes:
            raise ValueError("Empty file/user or upload exceeds size limit")
        types = {
            "application/pdf": {".pdf"},
            "image/png": {".png"},
            "image/jpeg": {".jpg", ".jpeg"},
        }
        filename = Path(str(filename).replace("\\", "/")).name
        if media_type not in types or Path(filename).suffix.lower() not in types[media_type]:
            raise ValueError("Only matching PDF, PNG and JPEG formats are supported")
        if media_type == "application/pdf":
            if not content.startswith(b"%PDF-"):
                raise ValueError("Invalid PDF signature")
            try:
                import fitz

                with fitz.open(stream=content, filetype="pdf") as pdf:
                    if not pdf.page_count or pdf.is_encrypted or pdf.page_count > 30:
                        raise ValueError("PDF is empty, encrypted or exceeds page limit")
            except ImportError as exc:
                raise RuntimeError("PDF uploads require pymupdf") from exc
            except Exception as exc:
                raise ValueError("Invalid or unsupported PDF") from exc
        else:
            from PIL import Image

            try:
                with Image.open(io.BytesIO(content)) as img:
                    expected = "PNG" if media_type == "image/png" else "JPEG"
                    if img.format != expected or img.width * img.height > 25_000_000:
                        raise ValueError("Image format mismatch or exceeds pixel limit")
                    img.verify()
            except Exception as exc:
                raise ValueError("Invalid or unsupported image") from exc
        document_id = uuid4().hex
        record = {
            "document_id": document_id,
            "user_id": user_id,
            "filename": filename[:255],
            "media_type": media_type,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        path = self.storage_dir / document_id
        path.write_bytes(content)
        try:
            with self.engine.begin() as conn:
                conn.execute(self.documents.insert().values(**record))
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return record

    def get_info(self, user_id, document_id):
        with self.engine.connect() as conn:
            row = (
                conn.execute(
                    select(self.documents).where(
                        self.documents.c.document_id == document_id,
                        self.documents.c.user_id == user_id,
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise PermissionError("Document unavailable for current user")
        return dict(row)

    def read(self, user_id, document_id):
        info = self.get_info(user_id, document_id)
        content = (self.storage_dir / info["document_id"]).read_bytes()
        if hashlib.sha256(content).hexdigest() != info["sha256"]:
            raise ValueError("Document integrity check failed")
        return content

    def save_extraction(self, user_id, document_id, extraction):
        self.get_info(user_id, document_id)
        with self.engine.begin() as conn:
            conn.execute(
                update(self.documents)
                .where(
                    self.documents.c.document_id == document_id, self.documents.c.user_id == user_id
                )
                .values(extraction_json=extraction.model_dump_json())
            )

    def get_extraction(self, user_id, document_id):
        from finance_agents.contracts.documents import DocumentExtraction

        cached = self.get_info(user_id, document_id)["extraction_json"]
        return DocumentExtraction.model_validate(json.loads(cached)) if cached else None
