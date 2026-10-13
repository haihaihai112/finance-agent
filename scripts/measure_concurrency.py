"""Finite DocumentService measurement on synthetic PDF or ONNX OCR fixtures."""

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import platform
import tempfile
from threading import Lock
import time

import fitz

from finance_agents.storage.database import create_database_engine
from finance_agents.storage.document_store import DocumentStore
from finance_agents.tools.document_service import DocumentService
from finance_agents.tools.ocr import DocumentOCR


class MeasuredOCR(DocumentOCR):
    def __init__(self):
        super().__init__()
        self.active = self.peak = self.calls = 0
        self.counter_lock = Lock()

    def _extract(self, *args):
        with self.counter_lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.calls += 1
        try:
            return super()._extract(*args)
        finally:
            with self.counter_lock:
                self.active -= 1


def fixture(mode):
    with fitz.open() as pdf:
        if mode == "image":
            page = pdf.new_page(width=900, height=200)
            page.insert_text((35, 130), "月收入：8000元", fontname="china-s", fontsize=52)
            return page.get_pixmap(alpha=False).tobytes("png"), "image/png", ".png"
        pdf.new_page().insert_text((30, 50), "monthly_income: 8000 CNY")
        return pdf.tobytes(), "application/pdf", ".pdf"


async def measure(args, scratch):
    # Explicitly cap the executor used by this finite measurement too.
    asyncio.get_running_loop().set_default_executor(
        ThreadPoolExecutor(max_workers=max(4, args.limit))
    )
    content, media_type, suffix = fixture(args.mode)
    engine = create_database_engine(f"sqlite:///{(scratch / 'measurement.db').as_posix()}")
    store = DocumentStore(engine, scratch / "files")
    ocr = MeasuredOCR()
    try:
        # ONNX model startup is reported separately and kept out of throughput.
        start = time.perf_counter()
        await ocr.extract("synthetic-warmup", content, media_type)
        warmup = time.perf_counter() - start
        ocr.calls = ocr.peak = 0
        ids = [
            store.save("synthetic-owner", f"{i}{suffix}", content, media_type)["document_id"]
            for i in range(args.documents)
        ]
        service = DocumentService(store, ocr, max_concurrency=args.limit)
        start = time.perf_counter()
        results = await asyncio.gather(
            *(
                service.extract("synthetic-owner", doc)
                for doc in ids
                for _ in range(args.duplicates)
            )
        )
        elapsed = time.perf_counter() - start
        start = time.perf_counter()
        await asyncio.gather(*(service.extract("synthetic-owner", doc) for doc in ids))
        cached = time.perf_counter() - start
        assert ocr.peak <= args.limit and ocr.calls == args.documents
        assert not service._inflight
        assert all(
            any(f.name == "monthly_income" and not f.confirmed for f in r.fields) for r in results
        )
        return {
            "workload": "synthetic_fixture_actual_document_service",
            "mode": args.mode,
            "engine": "RapidOCR ONNX" if args.mode == "image" else "PyMuPDF native text parser",
            "python": platform.python_version(),
            "system": platform.system(),
            "documents": args.documents,
            "requests": len(results),
            "duplicates_per_document": args.duplicates,
            "configured_limit": args.limit,
            "actual_worker_peak": ocr.peak,
            "actual_extraction_calls": ocr.calls,
            "warmup_seconds": round(warmup, 4),
            "cold_batch_seconds": round(elapsed, 4),
            "cached_batch_seconds": round(cached, 4),
            "request_rate_per_second": round(len(results) / elapsed, 2),
            "verified_bounds_and_dedup": True,
            "production_benchmark": False,
        }
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("text-pdf", "image"), default="text-pdf")
    parser.add_argument("--documents", type=int, choices=range(1, 17), default=6)
    parser.add_argument("--duplicates", type=int, choices=range(1, 5), default=2)
    parser.add_argument("--limit", type=int, choices=range(1, 9), default=2)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="finance-measure-") as directory:
        report = asyncio.run(measure(args, Path(directory)))
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
