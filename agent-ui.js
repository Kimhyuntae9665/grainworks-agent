(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const tasks = {
    rag: {question: "문서의 수분 기준과 재검사 및 보류 해제 절차, LOT-003 현재 수분과 SO-001 현재 보류량 및 기출하 영향량을 확인해줘."},
    orders: {question: "주문을 선택한 뒤 요청·출하·보류·창고 준비·미배정 물량과 가상 납기 상태를 Lot·트럭·알림 근거로 확인해 주세요."},
    factory: {question: "현재 공장의 설비 상태와 관련 Lot, 품질 보류 물량을 근거와 함께 확인해 주세요."},
    downtime: {question: "선택한 설비의 정지 조건을 비교하고, 포장 완료 물량과 남는 대기 상태를 가정과 함께 설명해 주세요."},
    shipment: {question: "RAW-2401에 연결된 LOT-003의 보류 이유와 LOT-001·LOT-009의 영향을 검사값·경보 근거로 확인하고, LOT-003의 재검사 검토안을 만들어 주세요. 미출하 보류와 기출하 영향을 구분하고 기록에 없는 원인은 추측하지 마세요."},
    reconcile: {question: "두 장부의 단위를 kg로 맞춰 Lot별 수량을 대조하고, 차이와 확인해야 할 항목을 알려주세요."},
    document: {question: "문서의 Lot·수분·온도·검사 날짜를 추출하고, 원문 근거와 현재 Lot 기록을 함께 보여주세요. 누락된 값은 채우지 마세요."},
    handover: {question: "현재 미해결 경보와 품질 보류를 정리하고, 다음 담당자가 먼저 확인할 작업과 근거를 알려주세요."},
    simulate: {question: "LOT-007의 모의 재검사 가정과 별도 해제에 따른 결과를 비교하고, 보류 상태와 남는 제약을 설명해 주세요. 원본 운영 상태는 변경하지 마세요."}
  };
  let task = "rag", available = false, busy = false, currentRun = null, runStart = 0, latestTrace = "", sourceUrl = null, extractedDocument = null;
  const taskButtons = [...document.querySelectorAll("[data-agent-task]")];
  const node = (tag, className, text) => {const el = document.createElement(tag); if (className) el.className = className; if (text !== undefined) el.textContent = String(text); return el;};
  const printable = (value) => typeof value === "string" ? value : JSON.stringify(value, null, 2);
  const list = (value) => Array.isArray(value) ? value : [];
  const number = (value) => Number.isFinite(Number(value)) ? Number(value).toLocaleString('ko-KR', {maximumFractionDigits: 2}) : String(value ?? '미확인');
  const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  function traceCounts(trace) {
    const model = trace.filter((entry) => ['ollama.chat', 'ollama.synthesis'].includes(entry.tool) || entry.tool === 'ollama.rag' && entry.result?.message).length;
    const requests = trace.filter((entry) => entry.tool === 'ollama.request').length;
    return {model, tools: trace.filter((entry) => !String(entry.tool).startsWith('ollama.')).length, pending: requests > model};
  }
  function displayRecord(record) {
    const id = String(record.id || ''), originalLabel = String(record.label || ''), value = record.value;
    const totals = {'TOTAL-totalKg':'총 기록 물량','TOTAL-shippedKg':'출하 완료 물량','TOTAL-heldKg':'품질 보류 물량','TOTAL-unresolved':'미해결 경보'};
    let label = totals[id] || originalLabel.replace(/\s*(?:kg|°C|%)\s*$/,'').trim();
    const orderLabels={requestedKg:'요청 물량',allocatedKg:'배정 물량',unallocatedKg:'미배정 물량',shippedKg:'출하 이력',readyKg:'창고 준비',workInProgressKg:'생산·검사 대기',heldKg:'미출하 보류',shippedImpactKg:'기출하 품질 영향'};
    const orderField=Object.keys(orderLabels).find(key=>id.startsWith('SO-')&&id.endsWith('-'+key));
    if(orderField)label=id.slice(0,-orderField.length-1)+' · '+orderLabels[orderField];
    if(id.startsWith('ALT-')&&value&&typeof value==='object'&&originalLabel.startsWith('주문 연계'))label=(value.shippedImpact?'주문 연계 기출하 영향 ':'주문 연계 현재 차단 ')+id;
    let displayed = printable(value);
    if (value === null) displayed = '미측정';
    else if (typeof value === 'number') {
      let unit = /(?:quantity|totalKg|shippedKg|heldKg)$/.test(id) || /kg\s*$/.test(originalLabel) ? ' kg' : /temperature|storageLimit/.test(id) || /°C\s*$/.test(originalLabel) ? '°C' : /moisture/.test(id) || /%\s*$/.test(originalLabel) ? '%' : /unresolved/.test(id) ? '건' : '';
      displayed = number(value) + unit;
    } else if (id.startsWith('ALT-')&&value&&typeof value==='object') displayed=[value.title,value.details].filter(Boolean).join(' · ');
    else if (/-status$/.test(id)) displayed = ({ok:'정상',hold:'품질 보류',warning:'영향 확인'})[value] || value;
    else if (/-stage$/.test(id)) displayed = ({intake:'원료 입고',mixing:'배합',quality:'품질 검사',packing:'포장',warehouse:'창고',shipped:'출하 완료'})[value] || value;
    return {label, value: displayed};
  }
  function evidencePriority(record, targets, moistureTargets = new Set()) {
    const id = String(record.id || ''), target = targets.has(record.lotId) || [...targets].some((lot) => id.startsWith(lot + '-'));
    const moisture = moistureTargets.has(record.lotId) || [...moistureTargets].some((lot) => id.startsWith(lot + '-'));
    if (target && /-temperature$/.test(id)) return moisture ? 105 : 120;
    if (target && /-status$/.test(id)) return 115;
    if (target && /-quantity$/.test(id)) return 110;
    if (target && /-moisture$/.test(id)) return moisture ? 120 : 105;
    if (record.assetId === 'TRK-02' && /-status$/.test(id)) return 104;
    if (id === 'SETTING-storageLimit') return 103;
    if (target && id.startsWith('ALT-')) return 102;
    if (record.assetId === 'TRK-02') return 101;
    if (target) return 90;
    return id.startsWith('TOTAL-') ? 60 : 50;
  }
  function updateRunButton() {$('agent-run').disabled = busy || !available || !window.Grainworks; $('agent-run').textContent = busy ? "실행 중…" : "Agent 실행 ↗";}
  function setError(message) {$('agent-input-error').hidden = !message; $('agent-input-error').textContent = message || "";}
  async function api(path, options) {
    const response = await fetch(path, options);
    let body;
    try {body = await response.json();} catch {throw Error("서버 응답을 읽지 못했습니다. 로컬 서버 상태를 확인하세요.");}
    if (!response.ok) throw Error(typeof body.error === "string" ? body.error : body.error?.message || body.detail || "요청을 처리하지 못했습니다.");
    return body;
  }
  async function status() {
    $('agent-status').dataset.state = "checking"; $('agent-status').textContent = "모델 연결 확인 중";
    try {
      const result = await api('/api/agent/status');
      available = result.available === true;
      $('agent-status').dataset.state = available ? "ready" : "unavailable";
      $('agent-status').textContent = available ? "실제 모델 연결됨" : "모델 실행 준비 필요";
      $('agent-model').textContent = [result.model, result.backend?.replace(' native tool calling', ''), result.detail].filter(Boolean).join(" · ") || "모델 상태 정보가 없습니다.";
    } catch (error) {
      available = false; $('agent-status').dataset.state = "unavailable"; $('agent-status').textContent = "모델 연결 없음"; $('agent-model').textContent = error.message;
    }
    updateRunButton();
    window.GrainRagUI?.refresh();
  }
  taskButtons.forEach((button) => button.addEventListener("click", () => {
    if (busy) return;
    task = button.dataset.agentTask;
    taskButtons.forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
    $('agent-question').value = task === 'orders' && window.GrainOrderUI?.selectedId() ? window.GrainOrderUI.selectedId() + '의 요청·출하·보류·창고 준비·미배정 물량과 가상 납기 상태를 Lot·트럭·알림 근거로 확인해 주세요. 기출하 영향과 미출하 보류를 구분해 주세요.' : tasks[task].question;
    $('agent-reconcile-inputs').hidden = task !== "reconcile";
    $('agent-document-inputs').hidden = task !== "document";
    $('agent-simulation-inputs').hidden = task !== "simulate";
    $('agent-downtime-inputs').hidden = task !== "downtime";
    $('agent-rag-inputs').hidden = task !== "rag";
    if(task==='simulate') {
      const select=$('agent-sim-lot');select.replaceChildren();
      for(const lot of window.Grainworks.snapshot().lots.filter(l=>l.status==='hold')) {const option=node('option','',lot.id);option.value=lot.id;select.append(option);}
      if(select.value)$('agent-question').value=tasks.simulate.question.replaceAll('LOT-007',select.value);
    }
    setError("");
  }));
  $('agent-refresh').addEventListener("click", status);
  $('agent-sim-lot').addEventListener('change', () => {
    if (task === 'simulate' && !busy) $('agent-question').value = tasks.simulate.question.replaceAll('LOT-007', $('agent-sim-lot').value);
  });
  window.addEventListener("grainworks:ready", updateRunButton);
  function resetCsvSample() {
    $('agent-left-csv').value = "lotId,quantity,unit\nLOT-007,4500,kg\nLOT-008,3200,kg\nLOT-010,5000,kg";
    $('agent-right-csv').value = "lotId,quantity,unit\nLOT-007,4.5,t\nLOT-008,4700,kg\nLOT-010,5000,kg\nLOT-010,5000,kg";
  }
  $('agent-csv-sample').addEventListener("click", resetCsvSample);
  resetCsvSample();
  function progress(title, description) {
    $('agent-progress').hidden = false; $('agent-progress-title').textContent = title; $('agent-progress-description').textContent = description;
  }
  function traceView(trace, parent) {
    const details = node('details', 'agent-trace');
    const counts = traceCounts(trace);
    details.append(node('summary', '', '실제 실행 기록 · 모델 응답 ' + counts.model + '회 / 도구 호출 ' + counts.tools + '회' + (counts.pending ? ' · 모델 응답 대기' : '')));
    const body = node('div', 'agent-trace-list');
    trace.filter((entry) => entry.tool !== 'ollama.request').forEach((entry, index) => {
      const item = node('div', 'agent-trace-item');
      const heading = node('div', 'agent-trace-heading');
      heading.append(node('strong', '', String(index + 1).padStart(2, '0') + ' · ' + (String(entry.tool).startsWith('ollama.') ? '모델 응답 · ' : '도구 · ') + entry.tool));
      heading.append(node('span', '', Number.isFinite(entry.elapsedMs) ? entry.elapsedMs + " ms" : "서버 기록"));
      item.append(heading);
      const payload = node('details', 'agent-trace-payload'); payload.append(node('summary', '', '입력과 반환값 보기'));
      payload.append(node('h5', '', '도구 입력'), node('pre', '', printable(entry.args ?? {})), node('h5', '', '도구 반환값'), node('pre', '', printable(entry.result ?? {})));
      item.append(payload); body.append(item);
    });
    if (!trace.length) body.append(node('p', '', '반환된 실행 기록이 없습니다.'));
    rawResult('요청 이벤트를 포함한 원본 실행 기록', trace, body);
    details.append(body); parent.append(details);
  }
  function evidenceRefs(ids, parent) {
    if (!list(ids).length) return;
    const refs = node('div', 'agent-evidence-refs'); refs.append(node('span', '', '근거'));
    ids.forEach((id) => {
      const link = node('a', '', id); link.href = '#agent-evidence-' + encodeURIComponent(id);
      link.addEventListener('click', () => {
        const target = document.getElementById('agent-evidence-' + encodeURIComponent(id)); if (!target) return;
        let details = target.closest('details'); while (details) {details.open = true; details = details.parentElement.closest('details');}
        requestAnimationFrame(() => {target.scrollIntoView({block:'nearest'}); target.tabIndex = -1; target.focus({preventScroll:true});});
      }); refs.append(link);
    });
    parent.append(refs);
  }
  function structuredView(title, value, parent) {
    if (value === null || value === undefined) return;
    const details = node('details', 'agent-structured'); details.open = true; details.append(node('summary', '', title));
    const render = (data, container, depth) => {
      if (data === null || typeof data !== 'object') {container.append(node('p', '', printable(data))); return;}
      if (Array.isArray(data)) {
        const rows = node('div', 'agent-structured-rows');
        data.forEach((item) => {const row = node('div', 'agent-structured-row'); render(item, row, depth + 1); rows.append(row);});
        if (!data.length) rows.append(node('p', '', '해당 항목 없음')); container.append(rows); return;
      }
      const dl = node('dl', 'agent-structured-fields');
      Object.entries(data).forEach(([key, item]) => {const row = node('div'); row.append(node('dt', '', key)); const dd = node('dd'); if (typeof item === 'object' && item !== null && depth < 3) render(item, dd, depth + 1); else dd.textContent = printable(item); row.append(dd); dl.append(row);});
      container.append(dl);
    };
    const content = node('div', 'agent-structured-content'); render(value, content, 0); details.append(content); parent.append(details);
  }
  function orderResultView(value, parent) {
    if (!value) return;
    const records=Array.isArray(value)?value:list(value.orders), section=node('section','agent-fact-section');
    section.append(node('h4','','주문 기록 · 도구 조회 결과'));
    records.forEach(order=>{
      const card=node('article','agent-order-card');card.append(node('h5','',order.id+' · '+(order.customer||'')));
      card.append(node('p','','요청 '+number(order.requestedKg)+' kg · 출하 '+number(order.shippedKg)+' kg · 보류 '+number(order.heldKg)+' kg · 창고 준비 '+number(order.readyKg)+' kg · 미배정 '+number(order.unallocatedKg)+' kg'));
      card.append(node('p','','생산·검사 대기 '+number(order.workInProgressKg)+' kg · 가상 납기 '+number(order.dueTick)+'분 · '+(order.overdue?'기한 경과':Number(order.remainingKg)===0?'출하 완료':'가상 기한 미경과')));
      if(Number(order.shippedImpactKg)>0)card.append(node('p','agent-warnings','기출하 영향 '+number(order.shippedImpactKg)+' kg. 미출하 보류와 별도로 확인하세요.'));
      const allocations=node('ul','agent-order-allocations');
      for(const line of list(order.lines))for(const allocation of list(line.allocations)){
        const status=allocation.stage==='shipped'?(allocation.shippedImpact?'기출하 영향 확인':'출하 완료'):allocation.needsConfirmation?'품질 기록 확인 필요':allocation.status==='held'?'미출하 보류':allocation.status==='ready'?'창고 준비':'생산·검사 대기';
        const row=node('li');row.append(node('strong','',allocation.lotId),node('span','',number(allocation.quantityKg)+' kg'),node('span','',status));allocations.append(row);
      }
      card.append(allocations);
      const controls=node('div');for(const lotId of list(order.linkedLotIds)){const b=node('button','quiet',lotId+' ↗');b.type='button';b.addEventListener('click',()=>GSelectLot(lotId));controls.append(b);}
      for(const assetId of list(order.linkedAssetIds)){const b=node('button','quiet',assetId+' 계획 ↗');b.type='button';b.addEventListener('click',()=>{window.Grainworks.selectAsset(assetId);$('factory-section').scrollIntoView({block:'start'});});controls.append(b);}
      const select=node('button','quiet','공장에서 '+order.id+' 보기');select.type='button';select.addEventListener('click',()=>{window.GrainOrderUI?.select(order.id);$('factory-section').scrollIntoView({block:'start'});});controls.append(select);card.append(controls);section.append(card);
    });
    section.append(node('p','agent-result-method','준비는 정상 창고 물량입니다. 가상 납기 상태는 현재 기록의 비교이며 납기 달성을 예측하지 않습니다.'));parent.append(section);
    rawResult('원본 주문 조회 데이터 보기',value,parent);
  }
  function GSelectLot(id){window.Grainworks.selectLot(id);$('factory-section').scrollIntoView({block:'start'});}
  function orderComparisonView(baseline, branch, parent){
    if(!baseline?.orders||!branch?.orders)return;
    const section=node('section','agent-fact-section');section.append(node('h4','','같은 주문의 준비 물량 비교'));
    const table=node('table','comparison-orders'),head=node('tr');for(const label of ['주문','정상 조건','정지 조건'])head.append(node('th','',label));const thead=node('thead');thead.append(head);table.append(thead);const tbody=node('tbody');
    baseline.orders.forEach(order=>{const compared=branch.orders.find(o=>o.id===order.id);if(!compared)return;const row=node('tr');row.append(node('td','',order.id));for(const o of [order,compared]){const cell=node('td','',number(o.readyKg)+' kg 준비');cell.append(node('small','',number(o.heldKg)+' kg 보류 · '+number(o.unallocatedKg)+' kg 미배정'),node('small','',o.overdue?'기한 경과':Number(o.remainingKg)===0?'출하 완료':'가상 기한 미경과'));row.append(cell);}tbody.append(row);});table.append(tbody);section.append(table,node('p','agent-result-method','복사본에서 창고 준비와 가상 기한을 비교합니다. 새 출하를 생성하거나 납기 달성을 약속하지 않습니다.'));parent.append(section);
  }
  function rawResult(title, value, parent) {
    const details = node('details', 'agent-raw-result'); details.append(node('summary', '', title), node('pre', '', printable(value))); parent.append(details);
  }
  function comparisonView(value, parent) {
    if (!value) return;
    const section = node('section', 'agent-comparison'); section.append(node('h4', '', '단위를 맞추고, 다른 행을 그대로 확인합니다.'), node('p', 'agent-comparison-intro', '원문 수량과 kg 환산값을 함께 표시합니다. 중복된 Lot은 각 행을 유지하며 합산하지 않습니다.'));
    const table = node('table', 'agent-comparison-table'); const caption = node('caption', '', '창고 장부와 출하 장부의 Lot별 대조 결과'); table.append(caption);
    const headers = ['Lot', '대조 상태', '창고 장부 · 원문 → kg', '출하 장부 · 원문 → kg', '확인할 차이'];
    const head = node('thead'), headingRow = node('tr'); headers.forEach((text) => {const th = node('th', '', text); th.scope = 'col'; headingRow.append(th);}); head.append(headingRow); table.append(head);
    const body = node('tbody');
    list(value.rows).forEach((row) => {
      const tr = node('tr'); tr.dataset.state = row.status;
      const cells = headers.map((text) => {const td = node('td'); td.dataset.label = text; tr.append(td); return td;});
      cells[0].append(node('strong', 'agent-comparison-lot', row.lotId || 'Lot ID 누락'));
      const names = {match: '일치', mismatch: '불일치', duplicate: '중복 · 판단 보류', missing: '한쪽 기록 없음'};
      cells[1].append(node('span', 'agent-comparison-state', names[row.status] || row.status));
      [list(row.left), list(row.right)].forEach((entries, side) => {
        const cell = cells[side + 2];
        if (!entries.length) cell.append(node('span', 'agent-comparison-missing', '기록 없음'));
        entries.forEach((entry) => {
          const record = node('div', 'agent-comparison-record'); record.append(node('span', 'agent-comparison-original', String(entry.originalQuantity) + ' ' + entry.originalUnit), node('strong', '', number(entry.kg) + ' kg'));
          if (entry.sourceRow !== undefined) record.append(node('small', '', '원문 ' + entry.sourceRow + '행')); cell.append(record);
        });
      });
      if (row.status === 'duplicate') cells[4].append(node('strong', '', '중복 행을 합산하지 않습니다.'), node('p', '', '창고 ' + list(row.left).length + '행 / 출하 ' + list(row.right).length + '행. 원본에서 중복 여부를 확인하세요.'));
      else if (row.status === 'match') cells[4].append(node('p', '', '동일한 kg 값으로 확인됩니다.'));
      else {
        const differences = list(row.differences); const labels = {kg: '수량', rawId: '원료 ID', moisture: '수분', temperature: '온도', 'missing on one side': '한쪽 기록 누락'};
        cells[4].append(node('p', '', differences.map((field) => labels[field] || field).join(' · ') || '원문 확인 필요'));
        if (differences.includes('kg') && list(row.left).length === 1 && list(row.right).length === 1) {
          const difference = row.right[0].kg - row.left[0].kg;
          cells[4].append(node('strong', 'agent-comparison-delta', '출하 장부 ' + (difference > 0 ? '+' : '') + number(difference) + ' kg'));
        }
      }
      body.append(tr);
    });
    table.append(body); section.append(table);
    if (!list(value.rows).length) section.append(node('p', 'agent-comparison-intro', '유효한 대조 행이 반환되지 않았습니다. 원문과 매핑 정보를 확인하세요.'));
    const note = node('p', 'agent-result-method', value.note || '단위 계산은 도구 결과입니다. 컬럼 의미와 매핑은 사람이 확인해야 합니다.'); section.append(note);
    if (list(value.issues).length) {const details = node('details', 'agent-comparison-issues'); details.open = true; details.append(node('summary', '', '입력에서 확인할 항목 · ' + value.issues.length + '건')); const ul = node('ul'); value.issues.forEach((issue) => ul.append(node('li', '', printable(issue)))); details.append(ul); section.append(details);}
    rawResult('원본 대조 결과와 컬럼 매핑 보기', value, section); parent.append(section);
  }
  function simulationView(value, parent) {
    if (!value) return;
    const section = node('section', 'agent-simulation'); const heading = node('div', 'agent-simulation-heading'); heading.append(node('h4', '', '같은 출발 상태에서, 두 가상 결과를 비교합니다.'));
    if (value.virtualMinutes !== undefined) heading.append(node('span', '', '가상 ' + number(value.virtualMinutes) + '분 후')); section.append(heading);
    const assumptions = list(value.assumptions);
    const assumptionText = assumptions.map((item) => item.lotId + ' · 수분 ' + number(item.moisture) + '% · 온도 ' + number(item.temperature) + '°C · 재검사와 별도 해제 완료 가정').join('\n');
    if (assumptionText) section.append(node('p', 'agent-simulation-assumptions', assumptionText));
    const cards = node('div', 'agent-simulation-cards');
    const segments = [{key: 'shippedKg', label: '출하 완료', className: 'shipped'}, {key: 'heldKg', label: '품질 보류', className: 'held'}, {key: 'activeKg', label: '진행 중', className: 'active'}];
    [['baseline', '현재 조건 유지', '현재 상태를 그대로 진행한 복사본'], ['branch', '재검사·별도 해제 가정', '입력한 가정을 적용한 복사본']].forEach(([key, title, description]) => {
      const data = value[key]; if (!data) return;
      const card = node('article', 'agent-simulation-card'); card.dataset.branch = key; card.append(node('span', 'agent-simulation-kicker', description), node('h5', '', title));
      const total = node('p', 'agent-simulation-total'); total.append(node('strong', '', number(data.shippedKg)), node('span', '', ' kg · 출하 완료')); card.append(total);
      if (Number.isFinite(data.totalKg) && data.totalKg > 0 && segments.every((segment) => Number.isFinite(data[segment.key]) && data[segment.key] >= 0)) {
        const bar = node('div', 'agent-simulation-bar'); bar.setAttribute('role', 'img'); bar.setAttribute('aria-label', segments.map((segment) => segment.label + ' ' + number(data[segment.key]) + 'kg').join(', '));
        segments.forEach((segment) => {const part = node('span', 'agent-simulation-segment ' + segment.className); part.style.width = Math.max(0, Math.min(100, data[segment.key] / data.totalKg * 100)) + '%'; part.title = segment.label + ' ' + number(data[segment.key]) + ' kg'; bar.append(part);}); card.append(bar);
      }
      const dl = node('dl', 'agent-simulation-metrics'); [{key:'totalKg',label:'전체 기록 물량',className:''},...segments.filter(segment=>segment.key!=='shippedKg')].forEach((segment) => {const row = node('div', segment.className); row.append(node('dt', '', segment.label), node('dd', '', number(data[segment.key]) + ' kg')); dl.append(row);});
      if (data.unresolved !== undefined) {const row = node('div'); row.append(node('dt', '', '미해결 알림'), node('dd', '', number(data.unresolved) + '건')); dl.append(row);} card.append(dl); cards.append(card);
    }); section.append(cards);
    section.append(node('p', 'agent-simulation-preservation', value.originalUnchanged === true ? '원본 운영 상태가 변경되지 않았습니다.' : value.originalUnchanged === false ? '원본 변경 여부를 확인해야 합니다.' : '복사본에서만 계산합니다. 실제 검사나 보류 해제가 아닙니다.'));
    if (value.limitations) section.append(node('p', 'agent-result-method', value.limitations));
    rawResult('계산 전 상태·가정·원본 계산 결과 보기', value, section); parent.append(section);
  }
  function renderResult(run) {
    const root = $('agent-results'); root.replaceChildren();
    const result = run.result || {};
    if (result.rag?.runtime?.modelInvocationSkipped) $('agent-run-meta').textContent = $('agent-run-meta').textContent.replace(run.model || '모델 확인 중', '모델 미호출');
    const records = list(result.evidence), recordMap = new Map(records.map((record) => [record.id, record]));
    const moistureTargets = new Set(records.filter(record => String(record.id).startsWith('ALT-') && /수분/.test(record.label + ' ' + record.value)).map(record => record.lotId));
    const targets = new Set(($('agent-question').value.match(/\bLOT-[A-Za-z0-9]+\b/g) || []));
    if (result.rag) window.GrainRagUI?.render(result.rag, root);
    if (!result.rag?.runtime?.modelInvocationSkipped) {
    const summary = node(result.rag ? 'details' : 'div', 'agent-answer'), answerHeading = node('div', 'agent-answer-heading');
    if (result.rag) summary.append(node('summary', '', 'AI 설명 초안 · 서술은 별도 검토'));
    answerHeading.append(node('span', 'eyebrow', 'MODEL ANSWER'));
    const verification = node('span', 'agent-summary-verification', 'AI 작성 초안 · 서술 검증 필요'); verification.dataset.verified = String(result.summaryVerified === true); answerHeading.append(verification);
    summary.append(answerHeading, node('h4', '', '조회 근거를 바탕으로 정리한 답변'), node('p', '', (result.rag ? result.rag.modelAnswer?.text : result.summary) || '모델 설명이 반환되지 않았습니다. 도구 호출과 근거를 확인하세요.'), node('small', 'agent-summary-note', 'AI의 서술과 도구가 반환한 기록은 구분해서 확인하세요. 답변 전체의 사실 일치가 검증된 것은 아닙니다.')); root.append(summary);
    }
    if (list(result.facts).length) {
      const section = node('section', 'agent-fact-section'); section.append(node('h4', '', '도구로 확인한 기록'));
      const orderFactIds=new Set(list(result.orders?.orders).flatMap(order=>['requestedKg','shippedKg','heldKg','readyKg','unallocatedKg','shippedImpactKg'].map(field=>order.id+'-'+field)));
      const selectedFacts=result.orders?result.facts.filter(fact=>list(fact.evidenceIds).some(id=>orderFactIds.has(id))):result.facts;
      const sorted = [...selectedFacts].sort((a,b) => evidencePriority(recordMap.get(list(b.evidenceIds)[0]) || b, targets, moistureTargets) - evidencePriority(recordMap.get(list(a.evidenceIds)[0]) || a, targets, moistureTargets));
      const facts = node('div', 'agent-facts');
      const addFact = (fact, parent) => {const record = recordMap.get(list(fact.evidenceIds)[0]) || {id:list(fact.evidenceIds)[0],...fact}; const displayed = displayRecord(record); const item = node('div', 'agent-fact'); item.append(node('span', '', displayed.label), node('strong', '', displayed.value)); evidenceRefs(fact.evidenceIds, item); parent.append(item);};
      sorted.slice(0,6).forEach((fact) => addFact(fact, facts)); section.append(facts);
      if (sorted.length > 6) {const extra = node('details', 'agent-extra-records'); extra.append(node('summary', '', '추가 조회 기록 ' + (sorted.length - 6) + '개 보기')); const more = node('div', 'agent-facts'); sorted.slice(6).forEach((fact) => addFact(fact, more)); extra.append(more); section.append(extra);} root.append(section);
    }
    comparisonView(result.comparison, root);
    simulationView(result.simulation, root);
    structuredView('문서 확인 결과', result.document, root);
    structuredView('설비 조회 근거', result.factory, root);
    orderResultView(result.orders, root);
    if (result.factorySimulation) {
      const r=result.factorySimulation,section=node('section','agent-fact-section');
      section.append(node('h4','','설비 정지 조건 비교 · 가정 모델'));
      const values=node('div','comparison-columns');
      for (const [label,key] of [['정상 조건','baseline'],['정지 조건','branch']]) {
        const item=node('div');item.append(node('small','',label),node('strong','',number(r[key]?.throughputKg)+' kg'));values.append(item);
      }
      section.append(values,node('p','','포장 완료 기준입니다. 품질 보류를 유지하고 원본 운영 기록은 변경하지 않습니다. 새 출하·납기 달성·수율을 계산하거나 약속하지 않습니다.'));root.append(section);
      orderComparisonView(r.baseline?.orders, r.branch?.orders, root);
      structuredView('비교 가정과 계산 근거',r,root);
    }
    if (list(result.warnings).length) {const warning = node('div', 'agent-warnings'); warning.append(node('h4', '', '확인이 필요한 항목')); const ul = node('ul'); result.warnings.forEach((text) => ul.append(node('li', '', printable(text)))); warning.append(ul); root.append(warning);}
    const proposals = list(result.proposals);
    if (proposals.length) {
      const section = node('section', 'agent-proposals'); section.append(node('h4', '', '사람이 검토할 제안'), node('p', 'agent-proposal-note', '제안은 즉시 적용되지 않습니다. 검토를 선택하면 현재 상태를 다시 확인한 뒤 기존 수동 조치 창을 엽니다.'));
      proposals.forEach((proposal) => {
        const item = node('article', 'agent-proposal');
        const heading = node('div', 'agent-proposal-heading'); const actionNames = {hold: '보류 검토', release: '해제 검토', resolve: '재검사·조치 검토'};
        heading.append(node('strong', '', proposal.lotId + ' · ' + (actionNames[proposal.action] || proposal.action)), node('span', '', '아직 적용되지 않음')); item.append(heading, node('p', '', proposal.reason)); evidenceRefs(proposal.evidenceIds, item);
        const controls = node('div', 'agent-proposal-actions'); const review = node('button', 'primary', '수동 조치 창에서 검토'); review.type = 'button'; const reject = node('button', 'quiet', '제안 제외'); reject.type = 'button'; const message = node('p', 'agent-review-status'); message.setAttribute('role', 'status');
        const decide = async (decision) => {
          review.disabled = true; reject.disabled = true; message.textContent = '현재 기록과 제안의 유효성을 확인하고 있습니다.';
          try {
            await window.Grainworks.save();
            const response = await api('/api/agent/proposals/' + encodeURIComponent(proposal.id) + '/review', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({decision})});
            if (decision === 'review') {window.Grainworks.openAction(response); message.textContent = '수동 조치 창을 열었습니다. 아직 운영 상태를 변경하지 않았습니다.';} else message.textContent = '제안을 제외했습니다.';
          } catch (error) {message.textContent = error.message + ' 새 작업을 실행해 최신 제안을 확인하세요.';}
        };
        review.addEventListener('click', () => decide('review')); reject.addEventListener('click', () => decide('reject')); controls.append(review, reject); item.append(controls, message); section.append(item);
      }); root.append(section);
    }
    if (records.length) {
      const evidence = node('section', 'agent-evidence'); evidence.append(node('h4', '', '조회한 원본 근거'));
      const important = (record) => (targets.has(record.lotId) && (/^ALT-/.test(record.id) || /-(?:temperature|moisture)$/.test(record.id))) || (record.assetId === 'TRK-02' && /-status$/.test(record.id));
      let visible = records.filter(result.orders?record=>String(record.id).startsWith('ALT-'):important).slice(0,4); if (!visible.length) visible = records.filter(record=>!result.orders||typeof record.value!=='object').slice(0,2);
      const visibleIds = new Set(visible.map((record) => record.id)), remaining = records.filter((record) => !visibleIds.has(record.id));
      const appendEvidence = (record, parent) => {
        const displayed = displayRecord(record); const item = node('article', 'agent-evidence-item'); item.id = 'agent-evidence-' + encodeURIComponent(record.id); item.append(node('span', 'agent-evidence-id', record.id), node('h5', '', displayed.label), node('p', '', displayed.value));
        const controls = node('div', 'agent-evidence-actions');
        if (record.lotId) {const button = node('button', 'quiet', record.lotId + ' 원본 보기'); button.type = 'button'; button.addEventListener('click', () => {window.Grainworks.selectLot(record.lotId); $('factory-section').scrollIntoView({block:'start'});}); controls.append(button);}
        if (record.assetId) {const button = node('button', 'quiet', record.assetId + ' 현장 보기'); button.type = 'button'; button.addEventListener('click', () => {window.Grainworks.selectAsset(record.assetId); $('factory-section').scrollIntoView({block:'start'});}); controls.append(button);}
        item.append(controls); parent.append(item);
      };
      visible.forEach((record) => appendEvidence(record, evidence));
      if (remaining.length) {const extra = node('details', 'agent-extra-records'); extra.append(node('summary', '', '전체 근거에서 추가 ' + remaining.length + '개 보기')); remaining.forEach((record) => appendEvidence(record, extra)); evidence.append(extra);} root.append(evidence);
    }
    traceView(list(run.trace), root);
  }
  $('agent-form').addEventListener('submit', async (event) => {
    event.preventDefault(); if (busy || !available || !window.Grainworks) return;
    setError(''); busy = true; updateRunButton(); taskButtons.forEach((button) => {button.disabled = true;}); $('agent-question').disabled = true; $('rag-enabled').disabled = true; $('rag-corpus').disabled = true; $('agent-results').replaceChildren(); latestTrace = ''; runStart = Date.now();
    try {
      progress('시뮬레이션을 멈추고 현재 상태를 저장합니다.', 'Agent가 읽는 원본과 화면의 현재 상태를 맞춥니다.');
      await window.Grainworks.save();
      const inputs = {};
      if (task === 'rag') {inputs.ragEnabled = $('rag-enabled').checked; inputs.corpus = $('rag-corpus').value;}
      if (task === 'reconcile') {inputs.leftCsv = $('agent-left-csv').value; inputs.rightCsv = $('agent-right-csv').value;}
      if (task === 'document') inputs.documentText = $('agent-document-text').value;
      if (task === 'downtime') {inputs.stationId=$('agent-downtime-station').value;inputs.downtimeMinutes=Number($('agent-downtime-minutes').value);inputs.horizonMinutes=Number($('agent-downtime-horizon').value);}
      let question = $('agent-question').value;
      if (task === 'simulate') {if(!$('agent-sim-lot').value)throw Error('현재 보류 Lot이 없습니다. 먼저 품질 이상 상황을 발생시키세요.');question=question.replaceAll('LOT-007',$('agent-sim-lot').value);question += '\n시연 가정: '+$('agent-sim-lot').value+', 재검사 수분 ' + Number($('agent-sim-moisture').value) + '%, 온도 ' + Number($('agent-sim-temperature').value) + '°C, 가상 시간 ' + Number($('agent-sim-minutes').value) + '분. 복사본에서 재검사와 별도 해제를 비교하세요.';}
      const started = await api('/api/agent/runs', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({task, question, inputs})});
      if (!started.runId) throw Error('서버가 실행 ID를 반환하지 않았습니다.');
      currentRun = started.runId;
      while (currentRun) {
        const run = await api('/api/agent/runs/' + encodeURIComponent(currentRun));
        const elapsed = run.status === 'completed' && Number.isFinite(run.elapsedMs) ? Math.round(run.elapsedMs / 1000) : Math.round((Date.now() - runStart) / 1000);
        $('agent-run-meta').textContent = [run.model || '모델 확인 중', elapsed + '초', '실행 ' + run.id].join(' · ');
        if (run.status === 'completed') {renderResult(run); $('agent-progress').hidden = true; currentRun = null; break;}
        if (run.status === 'failed') throw Error(typeof run.error === 'string' ? run.error : run.error?.message || 'Agent 실행에 실패했습니다. 모델과 도구 상태를 확인하세요.');
        const trace = list(run.trace), counts = traceCounts(trace); progress(run.status === 'queued' ? '서버가 실행을 준비하고 있습니다.' : task === 'rag' ? '문서 검색·기록 조회·답변 항목 대조를 진행하고 있습니다.' : '실제 모델이 기록을 확인하고 있습니다.', '경과 ' + elapsed + '초 · 모델 응답 ' + counts.model + '회 / 도구 호출 ' + counts.tools + '회. 결과를 기다리고 있습니다.');
        const serialized = JSON.stringify(trace);
        if (serialized !== latestTrace) {latestTrace = serialized; $('agent-results').replaceChildren(); if (trace.length) traceView(trace, $('agent-results'));}
        await delay(1300);
      }
    } catch (error) {currentRun = null; $('agent-progress').hidden = true; setError(error.message); if (!$('agent-results').childNodes.length) $('agent-results').append(node('p', 'agent-run-failed', '실행 결과가 없습니다. 연결 문제를 해결한 뒤 다시 실행하세요.'));}
    finally {busy = false; updateRunButton(); taskButtons.forEach((button) => {button.disabled = false;}); $('agent-question').disabled = false; $('rag-enabled').disabled = false; $('rag-corpus').disabled = false;}
  });
  function readBase64(file) {return new Promise((resolve, reject) => {const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.onerror = () => reject(Error('파일을 읽지 못했습니다.')); reader.readAsDataURL(file);});}
  $('agent-document-file').addEventListener('change', async (event) => {
    const file = event.target.files[0]; if (!file) return;
    const output = $('agent-document-status'); output.textContent = '';
    if (file.size > 2 * 1024 * 1024) {output.textContent = '2 MB 이하의 PDF·PNG·JPG를 선택하세요.'; return;}
    if (!['application/pdf', 'image/png', 'image/jpeg'].includes(file.type)) {output.textContent = 'PDF·PNG·JPG 파일을 선택하세요.'; return;}
    $('agent-document-file').disabled = true; $('agent-run').disabled = true; output.textContent = '원문에서 텍스트를 추출하고 있습니다. 완료 후 내용을 확인하세요.';
    try {
      const extracted = await api('/api/documents/extract', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({data_base64: await readBase64(file), mime_type: file.type, filename: file.name})});
      $('agent-document-text').value = extracted.text || ''; extractedDocument = {filename: file.name, method: extracted.method, pages: extracted.pages, warnings: extracted.warnings, fields: extracted.fields};
      const methodNames = {pymupdf_text:'PDF 문자 계층 추출',rapidocr_onnx_cpu:'이미지 OCR',mixed_pdf_text_ocr:'PDF 문자 계층·OCR 혼합'};
      output.textContent = [file.name, methodNames[extracted.method] || extracted.method || '텍스트 추출', list(extracted.pages).length + '페이지'].join(' · ');
      if (sourceUrl) URL.revokeObjectURL(sourceUrl); sourceUrl = URL.createObjectURL(file);
      const preview = $('agent-document-preview'); preview.replaceChildren(); preview.hidden = false; preview.classList.add('agent-document-intake');
      const sourceColumn = node('div', 'agent-document-source-column'), fieldColumn = node('section', 'agent-document-fields'); preview.append(sourceColumn,fieldColumn);
      const pageViews = new Map();
      function highlightSource(page, span, focusText) {
        const textarea = $('agent-document-text'), sourceText = span.text || '', index = textarea.value.indexOf(sourceText);
        if (focusText) textarea.focus(); if (index >= 0 && sourceText) textarea.setSelectionRange(index,index + sourceText.length);
        const pageView = pageViews.get(page?.page);
        if (pageView && page.width > 0 && page.height > 0 && Array.isArray(span.bbox) && span.bbox.length === 4 && span.bbox.every(Number.isFinite)) {
          pageViews.forEach((view) => {view.highlight.hidden=true;}); const [x0,y0,x1,y1]=span.bbox, highlight=pageView.highlight;
          highlight.style.left=(x0/page.width*100)+'%';highlight.style.top=(y0/page.height*100)+'%';highlight.style.width=((x1-x0)/page.width*100)+'%';highlight.style.height=((y1-y0)/page.height*100)+'%';highlight.hidden=false;pageView.wrapper.scrollIntoView({block:'nearest'});
        }
      }
      function addPagePreview(page, src, isPdf) {
        const pageContainer = node('section', 'agent-source-page');
        if (isPdf) pageContainer.append(node('h4', '', 'PDF 원문 · ' + page.page + '페이지'));
        const media = node('img'); media.src = src; media.alt = '업로드한 ' + file.name + (isPdf ? ' ' + page.page + '페이지의 실제 원문' : ' 원문');
        if (isPdf && Number.isFinite(page.previewWidth) && Number.isFinite(page.previewHeight)) {media.width = page.previewWidth; media.height = page.previewHeight;}
        const wrapper = node('div', 'agent-source-image'), highlight = node('div', 'agent-source-highlight'); highlight.hidden = true; highlight.setAttribute('aria-hidden', 'true'); wrapper.append(media, highlight); pageContainer.append(wrapper); sourceColumn.append(pageContainer); pageViews.set(page.page, {highlight, wrapper});
        media.addEventListener('error', () => {wrapper.replaceChildren(node('p', 'agent-source-preview-error', '이 페이지의 원문 이미지를 표시하지 못했습니다. 업로드한 파일에서 원문을 확인하세요.'));});
      }
      if (file.type === 'application/pdf') {
        list(extracted.pages).forEach((page) => {
          const src = page.previewDataUrl;
          if (typeof src === 'string' && src.length <= 512 * 1024 && /^data:image\/png;base64,[A-Za-z0-9+/=\r\n]+$/.test(src)) addPagePreview(page, src, true);
          else sourceColumn.append(node('p', 'agent-source-preview-error', page.page + '페이지의 원문 이미지가 반환되지 않았습니다. 추출 텍스트와 업로드한 PDF를 직접 대조하세요.'));
        });
        if (!list(extracted.pages).length) sourceColumn.append(node('p', 'agent-source-preview-error', '원문 페이지 이미지가 반환되지 않았습니다. 업로드한 PDF에서 원문을 확인하세요.'));
        const original = node('a', 'agent-original-download', '업로드한 PDF 원본 내려받기'); original.href = sourceUrl; original.download = file.name; sourceColumn.append(original);
      } else addPagePreview(list(extracted.pages)[0] || {page:1}, sourceUrl, false);
      fieldColumn.append(node('h4','','문서에서 읽은 입력 초안'),node('p','agent-document-field-caveat','텍스트 추출 결과 · 사실 판정 아님'));
      const fieldNames={lot_id:'Lot ID',quantity_kg:'수량',moisture_pct:'수분',temperature_c:'온도',date:'날짜'},fieldUnits={quantity_kg:' kg',moisture_pct:'%',temperature_c:'°C'};
      const fields=list(extracted.fields);
      fields.forEach((field)=>{
        const evidence=field.evidence||{},item=node('article','agent-document-field');
        const raw=String(field.value??''),numeric=Number(raw.replace(/,/g,''));let value=raw;
        if(fieldUnits[field.name]&&raw&&Number.isFinite(numeric))value=number(numeric)+fieldUnits[field.name];
        const heading=node('div','agent-document-field-heading');heading.append(node('span','',fieldNames[field.name]||field.name),node('strong','',value));item.append(heading);
        const info=Number.isFinite(evidence.confidence)?'OCR 모델 점수 '+(evidence.confidence*100).toFixed(1)+'%':'추출 신뢰도 미측정';
        item.append(node('small','',info));
        const source=node('button','agent-document-field-source','P'+evidence.page+' · '+(evidence.matched_text||evidence.text||'원문 위치 확인'));source.type='button';source.title='원문의 추출 위치 강조';source.addEventListener('click',()=>highlightSource(list(extracted.pages).find(page=>page.page===evidence.page),evidence,false));item.append(source);fieldColumn.append(item);
      });
      if(!fields.length)fieldColumn.append(node('p','agent-document-no-fields','라벨이 있는 입력 항목을 찾지 못했습니다. 누락값을 임의로 채우지 않습니다. 아래 추출 문장과 원문을 직접 확인하세요.'));
      const warnings=list(extracted.warnings),critical=warnings.filter(text=>/한글|인식된 텍스트가 없습니다/.test(String(text))),other=warnings.filter(text=>!critical.includes(text));
      if(critical.length){const notice=node('div','agent-document-ocr-warning');critical.forEach(text=>notice.append(node('p','',printable(text))));preview.append(notice);}
      if(other.length){const notices=node('details','agent-document-extraction-notes');notices.append(node('summary','','추출 범위와 주의사항 '+other.length+'개'));other.forEach(text=>notices.append(node('p','',printable(text))));preview.append(notices);}
      const details = $('agent-document-spans'), spanRoot = details.querySelector('div'); spanRoot.replaceChildren(); let count = 0;
      list(extracted.pages).forEach((page) => list(page.spans).forEach((span) => {
        if (!span.text) return; count++;
        const score = Number.isFinite(span.confidence) ? ' · OCR 모델 점수 ' + (span.confidence * 100).toFixed(1) + '%' : ' · 신뢰도 미측정';
        const button = node('button', 'agent-source-span', 'P' + page.page + ' · ' + span.text + score); button.type = 'button';
        button.addEventListener('click', () => highlightSource(page,span,true)); spanRoot.append(button);
      })); details.hidden = count === 0;
    } catch (error) {output.textContent = '추출 실패: ' + error.message; extractedDocument = null;}
    finally {$('agent-document-file').disabled = false; updateRunButton();}
  });
  status(); updateRunButton();
})();
