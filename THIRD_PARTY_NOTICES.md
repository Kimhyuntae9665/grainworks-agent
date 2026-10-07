# Dependencies and references

- Three.js: MIT. Bundled browser modules and upstream license in `vendor/`.
- LangGraph: MIT. Installed from PyPI; https://github.com/langchain-ai/langgraph
- Ollama: MIT. Install separately from https://ollama.com or its official GitHub releases.
- Qwen3-4B-Instruct-2507: Apache-2.0 model. Weights are NOT redistributed in this repository. https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507
- RapidOCR: Apache-2.0. https://github.com/RapidAI/RapidOCR ; default recognizer language coverage is limited. Refer to upstream model notices.
- ONNX Runtime: MIT. https://github.com/microsoft/onnxruntime
- PyMuPDF: AGPL-3.0 / commercial dual licensing. Installed separately, not bundled. https://pymupdf.readthedocs.io/en/latest/about.html
- Pillow: HPND. https://github.com/python-pillow/Pillow

The MIT license applies to the original project source. Dependency licenses continue to apply independently. PyMuPDF's licensing must be considered for distribution or deployment; this demo does not grant a commercial PyMuPDF license.

Visual references: [Port Wright](https://html.moldandyeast.com/?open=portwright) and [Airsup Warehouse](https://www.airsup.ai/lab/warehouse). No images or 3D models from those sites are redistributed. Geometry in this demo is programmatically created with Three.js.

Business reference: [Woosung's public company overview](https://www.woosungfeed.co.kr/m11.php). This independent prototype is not an internal Woosung system, authorized partner product, HACCP certification tool, or reproduction of its internal operating procedures.

Portfolio technology symbols: Python, LangGraph, Ollama and the QwenLM family mark are retained from official sources for identification. Logo/trademark terms remain separate from this repository MIT license and model/code licenses. Pinned sources, original asset SHA-256 and individual conditions are recorded in [the logo manifest](docs/portfolio/flow/assets-manifest.json).
