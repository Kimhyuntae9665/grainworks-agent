# Grainworks Agent

**출하를 막는 기록을 AI가 찾고, 사람이 조치를 결정하는 제조 업무 프로토타입.**

Lot·검사·원료·모의 트럭 배정을 연결한 3D 운영 화면에 실제 로컬 오픈소스 LLM Agent를 붙였습니다. 표와 화면을 오가며 연결 대상을 찾는 불편을 가정하고 만든 독립적인 개인 프로젝트입니다. 현장 인터뷰나 회사 내부 요구사항을 확인한 프로젝트는 아닙니다.

![Grainworks 3D](docs/portfolio/assets/01-overview.png)

## 실제 AI가 하는 일

| 작업 | 모델 역할 | 코드가 확인하는 내용 |
| --- | --- | --- |
| 출하 확인 | 경보·Lot·배정 조회 도구 선택, 근거 설명과 수동 조치 초안 | 저장된 상태의 ID·수량·기준값, 제안 대상 근거와 해제 전제 |
| 인수인계 | 실제 기록 조회 후 초안 작성 | 존재하는 활동 기록과 미확인 항목 구분 |
| 장부 대조 | 두 CSV의 컬럼 의미와 단위 연결 | kg/t 환산, 수량·검사값 차이, 중복·누락·모호한 단위 차단 |
| 문서 확인 | 원문에 있는 필드를 도구 인자로 제안 | PDF 문자 추출 / RapidOCR 이미지 인식, 부분문자열·숫자·위치 근거 |
| 조건 비교 | 명시적으로 입력한 가정으로 시뮬레이션 도구 선택 | JS 엔진 복제 분기, 물량 보존·원본 유지 |

Qwen 모델이 Ollama의 native `tool_calls`를 반환하면 LangGraph가 허용된 조회·계산 도구를 실행하고 결과를 모델에 돌려줍니다. 임의 SQL·셸·파일 수정·재고 변경 도구는 제공하지 않습니다. 작업별 도구 집합, 최대 6회 모델 호출·14회 도구 호출, 추론 동시 실행 1개로 범위를 제한합니다. 모델 오류나 미연결 때 규칙 요약을 AI 답변으로 대신하지 않습니다.

근거 카드와 수량 계산은 도구 결과입니다. 모델의 자유 서술과 컬럼 의미 매핑은 완전 검증됐다고 주장하지 않습니다. 미확인 식별자·숫자가 들어간 설명은 보류하며, `summaryVerified:false`를 화면에 표시합니다. 도구 실행 기록은 내부 사고 과정이 아닙니다.

## 60초 시연과 포트폴리오

- [3페이지 포트폴리오](docs/portfolio/portfolio.pdf)
- [실제 화면으로 구성한 60초 시연](docs/portfolio/demo-60s.mp4)
- [실제 모델 평가 기록](docs/evaluation/live-model.json) · [평가 설명](docs/evaluation/README.md)
- [문서 추출 검증과 한글 OCR 한계](DOCUMENT_INTAKE.md)

## 실행

Python 3.13, Node.js 22 이상, [Ollama](https://ollama.com)가 필요합니다. 모델 가중치는 별도 다운로드이며 저장소에 포함하지 않습니다.

```powershell
git clone https://github.com/Kimhyuntae9665/grainworks-agent.git
cd grainworks-agent
./setup.ps1
ollama pull qwen3:4b-instruct
./start.ps1
```

브라우저에서 `http://127.0.0.1:8795`를 엽니다. Ollama가 별도로 실행 중이어야 합니다. 모델 API는 `127.0.0.1:11434`로 고정했습니다. 모델 설치가 없으면 3D와 수동 조치는 사용할 수 있고 Agent는 미연결 상태로 표시됩니다.

Linux/macOS:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
ollama pull qwen3:4b-instruct
.venv/bin/python server.py --port 8795 --db work/grainworks.sqlite3
```

문서 확인에 사용할 합성 PDF·이미지는 `fixtures/demo-check.pdf`, `fixtures/demo-check.png`에 있습니다. 데이터 대사 샘플은 화면에서 편집하거나 `fixtures/reconcile-*.csv`를 사용합니다. 모델 입력 자료는 로컬에서 처리하지만 Ollama 모델과 OCR 모델의 최초 설치에는 다운로드가 필요합니다.

## 같은 사건을 끝까지 추적하기

LOT-007의 4,500kg은 검사 온도 31°C가 **시연 기준 28°C**를 넘어 보류됩니다. 경보 ALT-1과 모의 배정 TRK-02를 따라 조회합니다. 창고 7,700kg은 LOT-007 4,500kg과 LOT-008 3,200kg을 포함한 값입니다. 트럭·컨테이너·창고는 같은 재고의 다른 보기이므로 합산하지 않습니다.

AI 초안의 ‘검토’를 누르면 최신 저장 상태의 해시와 제안의 단일 사용을 확인한 뒤 기존 수동 조치 창을 엽니다. 이는 재고를 바꾸는 승인이 아닙니다. 사람은 사유와 검사값을 입력하며, 기준 이내 재검사 이후에도 보류 해제를 별도로 수행합니다. 서버가 재시작되면 메모리의 Agent 실행·제안 기록은 사라지고 운영 상태는 SQLite에 남습니다. 실제 사용자 인증·조직 권한 체계를 구현한 시스템은 아닙니다.

조건 비교는 복제 상태에서 ‘보류 유지’와 ‘수분 12%·온도 25°C로 모의 재검사 및 별도 해제 완료를 가정한 분기’를 가상 60분 실행합니다. 기본 출하 40,000kg, 조치 가정 분기 44,500kg은 합성 엔진 결과입니다. 실제 납기 최적화나 생산 효율 개선 실적이 아닙니다.

## 구조

```mermaid
flowchart LR
    UI[3D 현장 · AI 작업대] --> API[로컬 Python API]
    API --> DB[(SQLite 운영 상태)]
    API --> GRAPH[LangGraph]
    GRAPH --> MODEL[Ollama · Qwen3 4B]
    MODEL --> TOOLS[허용된 읽기 전용 도구]
    TOOLS --> ENGINE[JS 규칙 · 복제 시뮬레이션]
    API --> DOC[PyMuPDF · RapidOCR]
    TOOLS --> REVIEW[근거 · 제안 · 상태 해시]
    REVIEW --> HUMAN[사람 검토 → 수동 조치 창]
```

## 검증

```powershell
node --test engine.test.js assets.test.js
./.venv/Scripts/python.exe server.test.py
./.venv/Scripts/python.exe agent_backend.test.py
./.venv/Scripts/python.exe agent_api.test.py
./.venv/Scripts/python.exe document_intake.test.py
./.venv/Scripts/python.exe evaluate_agent.py
```

마지막 명령은 **실제 모델**을 호출합니다. 나머지의 모의 전송 테스트는 서버 흐름·가드 검증이며 모델 정확도 시험으로 합산하지 않습니다. CI는 합성 데이터의 엔진·도구·API·문서 추출 테스트를 수행합니다. 실제 모델 평가의 대상·성공·실패·지연은 평가 파일에 함께 남깁니다.

## 기여와 출처

김현태는 제조 업무 프로젝트 방향, Port Wright·Airsup 참고 화면, 그래픽과 현장 대상 상태 조회, 포트폴리오 개선, 실제 AI 확장을 요구하고 피드백했습니다. Codex가 이 요구를 바탕으로 코드·테스트·문서·공개 저장소를 구현했습니다. 개인이 모든 코드를 직접 작성했거나 모델을 학습한 경험으로 주장하지 않습니다.

[우성 공개 MES 소개](https://www.woosungfeed.co.kr/m11.php)의 원료 입고부터 제품 출고까지 이어지는 흐름에 착안했습니다. 실제 우성 ERP/MES·센서·고객 데이터·내부 SOP와 연동하지 않았습니다. 모든 제조·검사·배정 데이터와 절차 문서는 합성 또는 직접 작성한 모의 자료입니다.

공개 연락 경로: [Kimhyuntae9665](https://github.com/Kimhyuntae9665). 의존성·모델·시각 참고 출처와 사용 조건은 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)에 구분했습니다.
