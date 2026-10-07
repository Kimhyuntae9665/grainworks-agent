# Document intake verification

`document_intake.ingest_document(data_base64, mime_type, filename='')` extracts actual uploaded PDF/PNG/JPEG bytes. It accepts no URL or local path, retains no upload, calls no LLM, and performs no inventory mutation. The route adapter should call this directly from `POST /api/documents/extract`.

Limits: decoded input 2 MiB, PDF maximum 4 pages, single-frame images only, maximum image/raster side 6000px and total 12 million pixels per page, output 4000 spans/100000 characters. PDF raster fallback uses 144dpi. `DocumentIntakeError` exposes `code` and a user-facing message. Encrypted PDFs, invalid type/signature, oversized input and unsupported formats fail explicitly.

Selectable, unrotated PDF text uses PyMuPDF and preserves span bounding boxes in PDF points. Its `confidence` is `null`: extraction does not measure recognition accuracy. Image-only or rotated PDF pages and PNG/JPEG files use RapidOCR with ONNX Runtime CPU and return pixel bounding boxes and the model's recognition score. A page with a PDF text layer is not also scanned for handwriting or images; mixed-page methods are reported. This is a bounded demo, not a general document understanding benchmark.

Each PDF page also returns `previewDataUrl`, `previewWidth`, and `previewHeight`: a PNG thumbnail rendered directly from that actual PDF page, without reconstructing its text. Dimensions are at most 900×1400, with at most 256 KiB of PNG bytes per page (four base64 thumbnails stay below 1.4 MiB). High-entropy pages are rendered at a smaller scale until they fit. Existing page `width`, `height`, and bbox coordinate units remain source coordinates; UI overlays must scale them to the preview dimensions. This supports browsers where blob PDF iframes do not display.

The output contains `text`, `pages`, `method`, `warnings`, `filename`, and `fields`. `fields` contains only literal labelled matches for lot ID, quantity in kg, moisture in %, temperature in C, and date. Values stay strings. Every field carries its source span, page, bounding box, exact matched text, value character offsets, and confidence. Duplicate values are retained separately with a warning; absent values remain absent. This does not validate a date's business meaning, prove document authenticity, or authorize record updates. Document instructions remain inert source text.

## Actual verification on 2026-10-07

Runtime: isolated `.venv`, Python 3.13; RapidOCR 3.9.2, ONNX Runtime 1.30.0, PyMuPDF 1.28.2, Pillow 12.3.0. The current bundled RapidOCR models use ONNX PP-OCRv6 small detection/recognition and v2 mobile orientation classification. No Torch/GPU inference was used. Initial model setup can download models from RapidOCR's configured model source; uploaded document contents are supplied as decoded local pixel arrays, never as remote URLs.

Synthetic fixture: `fixtures/demo-check.pdf` (selectable text) and `fixtures/demo-check.png` (900×780 raster). Both visibly say **SIMULATED DEMO - NOT AN ACTUAL RECORD** and contain only synthetic LOT-007/4500kg/12.0%/31C/2026-10-07. The fixture generator locally embeds and subsets Malgun Gothic if installed; it downloads no fonts.

Actual selectable-PDF extraction recovered all five expected fields and the Korean sentence `가상 검사 문서 · 실제 입고 기록 아님`. Actual PNG OCR recovered all five expected English/numeric fields; their model scores ranged from 0.9776 to 0.9972. The Korean sentence was **omitted** by the default OCR model. The UI and module warn that Korean OCR is not validated; the successful English/numeric fixture does not support a Korean recognition claim. Public-safe evidence is saved in `fixtures/demo-check.extraction.json`, with source SHA-256 and no machine paths.

Run `.venv/Scripts/python.exe -X utf8 document_intake.test.py` for actual PDF text/image OCR/scanned-PDF fallback, exact evidence, invalid input, page/pixel limits, and inert prompt-injection tests. No mocks substitute for the actual extraction/OCR success tests.

The separate Agent tool accepts four model-proposed fields (lot ID, moisture, temperature, inspection date), with numeric measurements and exact original source excerpts. It rejects omission of present, nonempty English line labels `Lot ID:`, `Moisture:`, `Temperature:`, `Inspection Date:` and asks the model to retry; it never fills their values itself. This narrow completeness check is reported as such and does not establish general Korean field understanding or full semantic accuracy. The deterministic intake's fifth quantity field remains separate from these four Agent fields.

## Primary references

- [RapidOCR official repository](https://github.com/RapidAI/RapidOCR): open-source OCR, default Chinese/English support, installation with `rapidocr onnxruntime`.
- [RapidOCR official usage documentation](https://rapidai.github.io/RapidOCRDocs/main/en/install_usage/rapidocr/usage/): CPU ONNX Runtime, `RapidOCROutput.boxes`, `txts`, and `scores`; current model defaults.
- [PyMuPDF official text extraction documentation](https://pymupdf.readthedocs.io/en/latest/app1.html): `dict` text extraction and bounding boxes.

PyMuPDF is offered under AGPL/commercial licensing; review that dependency's licensing before turning this local prototype into a distributed product. [Official PyMuPDF licensing](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright).
