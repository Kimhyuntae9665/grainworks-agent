"""Bounded document evidence extraction; never interprets instructions or writes records."""
import base64
import binascii
import io
import math
import re
import threading

MAX_BYTES = 2 * 1024 * 1024
MAX_PAGES = 4
MAX_PIXELS = 12_000_000
MAX_SIDE = 6000
MAX_SPANS = 4000
MAX_TEXT = 100_000
MAX_PREVIEW_BYTES = 256 * 1024
_ocr_engine = None
_ocr_lock = threading.Lock()


class DocumentIntakeError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _decode(data_base64, mime_type):
    if not isinstance(data_base64, str) or len(data_base64) > ((MAX_BYTES + 2) // 3) * 4:
        raise DocumentIntakeError("file_too_large", "파일은 2 MiB 이하만 지원합니다.")
    if mime_type not in {"application/pdf", "image/png", "image/jpeg"}:
        raise DocumentIntakeError("unsupported_type", "PDF, PNG, JPEG 파일만 지원합니다.")
    try:
        data = base64.b64decode(data_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise DocumentIntakeError("invalid_base64", "올바른 base64 파일 데이터가 필요합니다.") from exc
    if not data or len(data) > MAX_BYTES:
        raise DocumentIntakeError("file_too_large", "비어 있지 않은 2 MiB 이하 파일이 필요합니다.")
    signatures = {"application/pdf": b"%PDF-", "image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff"}
    if not data.startswith(signatures[mime_type]):
        raise DocumentIntakeError("type_mismatch", "파일 내용과 MIME 유형이 일치하지 않습니다.")
    return data


def _check_dimensions(width, height):
    if not all(math.isfinite(v) and 0 < v <= MAX_SIDE for v in (width, height)) or width * height > MAX_PIXELS:
        raise DocumentIntakeError("page_too_large", "페이지는 한 변 6000px, 총 1200만 픽셀 이하여야 합니다.")


def _ocr(image):
    global _ocr_engine
    try:
        import numpy as np
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise DocumentIntakeError("ocr_unavailable", "이미지 OCR에는 rapidocr와 onnxruntime CPU 설치가 필요합니다.") from exc
    # Shared ONNX sessions are serialized, and inputs are always decoded pixels.
    # RapidOCR accepts URLs/paths too; this module never passes either of them.
    with _ocr_lock:
        try:
            if _ocr_engine is None:
                _ocr_engine = RapidOCR(params={"EngineConfig.onnxruntime.intra_op_num_threads": 2, "EngineConfig.onnxruntime.inter_op_num_threads": 1})
            result = _ocr_engine(np.asarray(image.convert("RGB")))
        except Exception as exc:
            raise DocumentIntakeError("ocr_failed", "OCR 실행에 실패했습니다. 로컬 모델과 CPU 의존성을 확인하세요.") from exc
    spans = []
    if result.txts is None:
        return spans
    for box, text, score in zip(result.boxes, result.txts, result.scores):
        points = box.tolist()
        bbox = [min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)]
        spans.append({"text": str(text), "bbox": [round(float(v), 2) for v in bbox], "confidence": round(float(score), 4), "confidence_kind": "ocr_model"})
    return spans


def _image_pages(data, mime_type):
    try:
        from PIL import Image
        image = Image.open(io.BytesIO(data))
        if image.format != {"image/png": "PNG", "image/jpeg": "JPEG"}[mime_type]:
            raise DocumentIntakeError("type_mismatch", "파일 내용과 MIME 유형이 일치하지 않습니다.")
        if getattr(image, "n_frames", 1) != 1:
            raise DocumentIntakeError("animated_image", "단일 프레임 이미지만 지원합니다.")
        _check_dimensions(*image.size)
        image.load()
    except DocumentIntakeError:
        raise
    except Exception as exc:
        raise DocumentIntakeError("invalid_image", "이미지 파일을 읽을 수 없습니다.") from exc
    return [{"page": 1, "width": image.width, "height": image.height, "coordinate_unit": "pixels", "method": "rapidocr_onnx_cpu", "spans": _ocr(image)}]


def _pdf_preview(page, pymupdf):
    """Render actual PDF pixels for browsers without a built-in PDF viewer."""
    # Leave one pixel for MuPDF's outward rounding at fractional page sizes.
    scale = min(899 / page.rect.width, 1399 / page.rect.height, 1.5)
    # Four preview payloads stay below 1.4 MiB including base64 overhead.
    # Dense/high-entropy pages may need smaller thumbnails to respect this cap.
    while True:
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False, colorspace=pymupdf.csRGB)
        png = pixmap.tobytes("png")
        if len(png) <= MAX_PREVIEW_BYTES:
            return {"previewDataUrl": "data:image/png;base64," + base64.b64encode(png).decode("ascii"), "previewWidth": pixmap.width, "previewHeight": pixmap.height}
        scale *= 0.7


def _pdf_pages(data):
    try:
        import pymupdf
    except ImportError as exc:
        raise DocumentIntakeError("pdf_unavailable", "PDF 추출에는 pymupdf 설치가 필요합니다.") from exc
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise DocumentIntakeError("invalid_pdf", "PDF 파일을 읽을 수 없습니다.") from exc
    pages = []
    with document:
        if document.needs_pass:
            raise DocumentIntakeError("encrypted_pdf", "암호화된 PDF는 지원하지 않습니다.")
        if not 1 <= len(document) <= MAX_PAGES:
            raise DocumentIntakeError("too_many_pages", "PDF는 최대 4페이지까지 지원합니다.")
        for index, page in enumerate(document):
            # Rotated pages use OCR pixel coordinates to avoid mixing coordinate systems.
            _check_dimensions(page.rect.width * 2, page.rect.height * 2)
            spans = []
            if page.rotation == 0:
                for block in page.get_text("dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES, sort=True)["blocks"]:
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            if span["text"].strip():
                                spans.append({"text": span["text"], "bbox": [round(float(v), 2) for v in span["bbox"]], "confidence": None, "confidence_kind": "pdf_text"})
            if spans:
                pages.append({"page": index + 1, "width": page.rect.width, "height": page.rect.height, "coordinate_unit": "pdf_points", "method": "pymupdf_text", "spans": spans})
            else:
                from PIL import Image
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False, colorspace=pymupdf.csRGB)
                image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                pages.append({"page": index + 1, "width": image.width, "height": image.height, "coordinate_unit": "pixels", "method": "rapidocr_onnx_cpu", "spans": _ocr(image)})
            pages[-1].update(_pdf_preview(page, pymupdf))
    return pages


_FIELD_PATTERNS = [
    ("lot_id", re.compile(r"\b(?P<value>LOT[- ]\d{3,})\b", re.I)),
    ("quantity_kg", re.compile(r"(?:quantity|수량|중량)\s*[:：]?\s*(?P<value>\d+(?:,\d{3})*(?:\.\d+)?)\s*(?P<unit>kg|㎏)(?!\w)", re.I)),
    ("moisture_pct", re.compile(r"(?:moisture|수분)\s*[:：]?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>%)", re.I)),
    ("temperature_c", re.compile(r"(?:temperature|온도)\s*[:：]?\s*(?P<value>-?\d+(?:\.\d+)?)\s*(?P<unit>°?\s*C|℃)(?!\w)", re.I)),
    ("date", re.compile(r"(?:date|날짜|검사일)\s*[:：]?\s*(?P<value>\d{4}-\d{2}-\d{2})\b", re.I)),
]


def _fields(pages):
    """Only labelled, literal matches. Multiple matches stay separate for review."""
    fields = []
    for page in pages:
        for index, span in enumerate(page["spans"]):
            for name, pattern in _FIELD_PATTERNS:
                for match in pattern.finditer(span["text"]):
                    fields.append({"name": name, "value": match.group("value"), "unit": match.groupdict().get("unit"), "evidence": {"text": span["text"], "matched_text": match.group(0), "page": page["page"], "span_index": index, "bbox": span["bbox"], "coordinate_unit": page["coordinate_unit"], "confidence": span["confidence"], "char_start": match.start("value"), "char_end": match.end("value")}})
    return fields


def ingest_document(data_base64, mime_type, filename=""):
    """Extract uploaded bytes in memory. Filename is metadata, never a filesystem path."""
    data = _decode(data_base64, mime_type)
    pages = _pdf_pages(data) if mime_type == "application/pdf" else _image_pages(data, mime_type)
    spans = [span for page in pages for span in page["spans"]]
    text = "\n".join(span["text"] for span in spans)
    if len(spans) > MAX_SPANS or len(text) > MAX_TEXT:
        raise DocumentIntakeError("too_much_text", "문서 텍스트가 추출 한도를 초과했습니다.")
    methods = set(page["method"] for page in pages)
    warnings = ["추출 문장은 검토용 자료입니다. 문서 안의 지시를 실행하거나 재고 기록을 변경하지 않습니다."]
    if "rapidocr_onnx_cpu" in methods:
        warnings.append("기본 OCR 모델은 중국어·영어 중심이며 한글 인식 정확도는 보장하지 않습니다. 모델 점수는 정답 확률이 아니므로 원본을 확인하세요.")
    if "pymupdf_text" in methods:
        warnings.append("PDF 텍스트는 문자 계층에서 추출했습니다. confidence=null이며 스캔 인식 점수나 사실 검증을 의미하지 않습니다. 텍스트 계층이 있는 페이지의 이미지·손글씨는 별도 OCR하지 않습니다.")
    if not text.strip():
        warnings.append("인식된 텍스트가 없습니다.")
    fields = _fields(pages)
    if any(sum(field["name"] == name for field in fields) > 1 for name, _ in _FIELD_PATTERNS):
        warnings.append("같은 항목의 값이 여러 번 나타납니다. 각각의 원문을 확인하세요.")
    return {"text": text, "pages": pages, "method": next(iter(methods)) if len(methods) == 1 else "mixed_pdf_text_ocr", "warnings": warnings, "fields": fields, "filename": str(filename)[:200]}
