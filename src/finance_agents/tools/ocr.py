"""Shared evidence extraction: native PDF text first, ONNX OCR for scanned pages."""

import asyncio
import io
import re
from decimal import Decimal, InvalidOperation

from finance_agents.contracts.documents import DocumentExtraction, ExtractedField

MONEY_LABELS = {
    "requested_amount": r"贷款金额|申请金额|requested_amount",
    "monthly_income": r"月收入|月薪|monthly_income",
    "monthly_debt_payment": r"月还款|每月还款|月负债偿还|monthly_debt_payment",
    "assets_total": r"总资产|assets_total",
    "liabilities_total": r"总负债|liabilities_total",
    "premium": r"保费|premium",
    "insured_amount": r"保额|保险金额|insured_amount",
}
TEXT_LABELS = {
    "subject_ref": r"申请人编号|申请人|subject_ref",
    "employment_status": r"就业状态|工作状态|employment_status",
    "policy_id": r"保单号|保单编号|policy_id",
    "policy_version": r"条款版本|保单版本|policy_version",
    "insurer": r"保险公司|insurer",
    "insurance_type": r"险种|insurance_type",
    "incident_type": r"事故类型|出险类型|incident_type",
    "incident_date": r"事故日期|出险日期|incident_date",
}


def parse_text_fields(
    text, document_id, page_number=1, source="text_parser", confidence=1.0, bounding_box=None
):
    fields = []
    original_text = text
    # Fullwidth digits are the same numeric symbols, not inferred replacements.
    # Translation preserves string length so evidence offsets remain unchanged.
    text = text.translate(str.maketrans("０１２３４５６７８９．，：", "0123456789.,:"))

    def add(name, value, quote):
        fields.append(
            ExtractedField(
                name=name,
                value=value,
                document_id=document_id,
                page_number=page_number,
                quote=quote,
                confidence=confidence,
                source=source,
                bounding_box=bounding_box,
                confirmed=False,
            )
        )

    for name, labels in MONEY_LABELS.items():
        pattern = rf"(?:{labels})\s*[:：=]?\s*(人民币|CNY|RMB|USD|美元|EUR|欧元|¥|￥|\$)?\s*([0-9][0-9,.]*)(?![0-9.,])\s*(万美元|万欧元|万元|万|元|CNY|RMB|人民币|USD|美元|EUR|欧元)?(?=$|[\s;；,，。/])"
        for match in re.finditer(pattern, text, re.I):
            if not re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?", match[2]):
                continue
            try:
                amount = Decimal(match[2].replace(",", ""))
            except InvalidOperation:
                continue
            unit = (match[1] or "") + (match[3] or "")
            currency = (
                "USD"
                if re.search(r"USD|美元|\$", unit, re.I)
                else "EUR"
                if re.search(r"EUR|欧元", unit, re.I)
                else "CNY"
                if re.search(r"CNY|RMB|人民币|元|¥|￥", unit, re.I)
                else "UNK"
            )
            if match[3] and match[3].startswith("万"):
                amount *= 10000
            add(
                name,
                {"amount": str(amount), "currency": currency},
                original_text[match.start() : match.end()],
            )
    for name, labels in TEXT_LABELS.items():
        pattern = rf"(?:{labels})\s*[:：=]\s*([^\n;；,，]+)"
        for match in re.finditer(pattern, text, re.I):
            value = match[1].strip()
            if name == "incident_date":
                value = (
                    value.replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-")
                )
            add(name, value, original_text[match.start() : match.end()])
    for name, labels in {
        "loan_term_months": r"贷款期限|贷款期数|loan_term_months",
        "employment_months": r"就业月数|工作月数|employment_months",
    }.items():
        for match in re.finditer(
            rf"(?:{labels})\s*[:：=]?\s*([0-9]+)\s*(个月|月|months|年|years)?", text, re.I
        ):
            value = int(match[1]) * (12 if match[2] in ("年", "years") else 1)
            add(name, value, original_text[match.start() : match.end()])
    return fields


class OCRUnavailable(RuntimeError):
    pass


class DocumentOCR:
    def __init__(self, max_pages=30, max_pixels=25_000_000):
        self.max_pages, self.max_pixels = max_pages, max_pixels
        self._engine = None

    async def extract(
        self, document_id: str, content: bytes | None = None, media_type: str | None = None
    ) -> DocumentExtraction:
        if content is None:
            raise ValueError(
                "Document bytes are required; use DocumentService for authorized reads"
            )
        return await asyncio.to_thread(self._extract, document_id, content, media_type)

    def _ocr(self, image):
        try:
            import numpy as np
            from rapidocr import EngineType, RapidOCR
        except ImportError as exc:
            raise OCRUnavailable(
                "Scanned PDF/image requires optional dependencies rapidocr and onnxruntime"
            ) from exc
        try:
            if self._engine is None:
                self._engine = RapidOCR(
                    params={
                        "Global.log_level": "error",
                        "Det.engine_type": EngineType.ONNXRUNTIME,
                        "Cls.engine_type": EngineType.ONNXRUNTIME,
                        "Rec.engine_type": EngineType.ONNXRUNTIME,
                    }
                )
            output = self._engine(np.asarray(image.convert("RGB")))
        except Exception as exc:
            raise OCRUnavailable(
                f"RapidOCR ONNX inference unavailable: {type(exc).__name__}: {exc}"
            ) from exc
        if getattr(output, "txts", None) is None:
            return []
        return [
            (
                str(text),
                float(score),
                tuple(
                    float(v)
                    for v in (box[:, 0].min(), box[:, 1].min(), box[:, 0].max(), box[:, 1].max())
                ),
            )
            for text, score, box in zip(output.txts, output.scores, output.boxes)
        ]

    def _extract(self, document_id, content, media_type):
        from PIL import Image

        lines, fields, warnings = [], [], []

        def record(items, page, source):
            if source == "ocr":
                # OCR may return a table label and its value as separate boxes.
                # Join only boxes sharing a visual row; preserve the union box and
                # the weakest recognition confidence of that row.
                rows = []
                for item in sorted(items, key=lambda value: (value[2][1], value[2][0])):
                    box = item[2]
                    center = (box[1] + box[3]) / 2
                    for row in rows:
                        reference = row[0][2]
                        tolerance = min(box[3] - box[1], reference[3] - reference[1]) / 2
                        if abs(center - (reference[1] + reference[3]) / 2) <= tolerance:
                            row.append(item)
                            break
                    else:
                        rows.append([item])
                items = []
                for row in rows:
                    row.sort(key=lambda value: value[2][0])
                    items.append(
                        (
                            " ".join(value[0] for value in row),
                            min(value[1] for value in row),
                            (
                                min(value[2][0] for value in row),
                                min(value[2][1] for value in row),
                                max(value[2][2] for value in row),
                                max(value[2][3] for value in row),
                            ),
                        )
                    )
            for text, score, box in items:
                lines.append(text)
                fields.extend(parse_text_fields(text, document_id, page, source, score, box))

        if media_type == "application/pdf" or content.startswith(b"%PDF-"):
            try:
                import fitz
            except ImportError as exc:
                raise OCRUnavailable("PDF extraction requires optional dependency pymupdf") from exc
            with fitz.open(stream=content, filetype="pdf") as pdf:
                if pdf.is_encrypted:
                    raise ValueError("Encrypted PDFs are unsupported")
                if pdf.page_count > self.max_pages:
                    raise ValueError(f"PDF exceeds {self.max_pages} pages")
                for index, page in enumerate(pdf):
                    blocks = page.get_text("blocks")
                    text_blocks = [
                        (str(b[4]), 1.0, tuple(float(v) for v in b[:4]))
                        for b in blocks
                        if len(b) > 6 and b[6] == 0 and str(b[4]).strip()
                    ]
                    if text_blocks:
                        record(text_blocks, index + 1, "text_parser")
                    else:
                        if page.rect.width * page.rect.height * 4 > self.max_pixels:
                            raise ValueError("Rendered PDF page exceeds pixel limit")
                        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                        image = Image.open(io.BytesIO(pix.tobytes("png")))
                        record(self._ocr(image), index + 1, "ocr")
        else:
            with Image.open(io.BytesIO(content)) as image:
                if image.width * image.height > self.max_pixels:
                    raise ValueError("Image exceeds pixel limit")
                record(self._ocr(image), 1, "ocr")
        if not lines:
            warnings.append("No readable text detected; upload a clearer document")
        if any(isinstance(f.value, dict) and f.value.get("currency") == "UNK" for f in fields):
            warnings.append("Currency absent in source; must be supplied and confirmed")
        warnings.append(
            "All extracted financial numbers require user confirmation before calculations"
        )
        return DocumentExtraction(
            document_id=document_id, text="\n".join(lines), fields=fields, warnings=warnings
        )
