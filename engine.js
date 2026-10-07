(function (root) {
  'use strict';
  const Orders = typeof module !== 'undefined' && module.exports ? require('./order_model.js') : root.GrainOrders;
  // Synthetic factory records. No live sensor, ERP, regulatory or LLM connection.
  const STAGES = ['intake', 'mixing', 'quality', 'packing', 'warehouse', 'shipped'];
  const LABELS = { intake: '원료 입고', mixing: '배합', quality: '품질 검사', packing: '포장', warehouse: '창고', shipped: '출하' };
  const DURATIONS = { intake: 4, mixing: 8, quality: 3, packing: 5, warehouse: 8 };
  const MAX_LOTS = 1000;
  const fail = error => ({ ok: false, error });
  const good = extra => Object.assign({ ok: true }, extra || {});
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const reasonText = reason => typeof reason === 'string' && reason.trim().length > 0 && reason.trim().length <= 500;
  function event(s, kind, lotId, message) {
    s.events.push({ id: 'EVT-' + s.nextEventId++, tick: s.tick, kind, lotId: lotId || null, message });
    if (s.events.length > 500) s.events.splice(0, s.events.length - 500);
  }
  function lotRecord(id, rawId, product, quantity, stage, moisture, temperature, progress) {
    return { id, rawId, product, quantity, stage, progress: progress || 0, status: 'ok',
      qc: { moisture, temperature, stale: false }, shipment: null, holdReason: null,
      qualityChecked: false, storageChecked: false, imported: false };
  }
  function createState() {
    const s = { version: 1, synthetic: true, tick: 0, running: true, speed: 1,
      settings: { productionRate: 100, moistureLimit: 14, storageLimit: 28 },
      lots: [], alerts: [], events: [], nextId: 11, nextAlertId: 1, nextEventId: 1 };
    const seed = [
      ['RAW-2401', '육계 성장 사료', 4800, 'intake', 12.3, 22, .35],
      ['RAW-2402', '양돈 비육 사료', 4200, 'intake', 12.6, 23, .1],
      ['RAW-2401', '육계 성장 사료', 3600, 'mixing', 12.3, 24, .5],
      ['RAW-2403', '산란계 사료', 5200, 'mixing', 11.9, 23, .15],
      ['RAW-2402', '양돈 비육 사료', 4000, 'quality', 12.6, 24, .4],
      ['RAW-2404', '한우 비육 사료', 6000, 'packing', 12.1, 25, .55],
      ['RAW-2403', '산란계 사료', 4500, 'warehouse', 11.9, 25, .2],
      ['RAW-2405', '양돈 자돈 사료', 3200, 'warehouse', 12.8, 26, .65],
      ['RAW-2401', '육계 성장 사료', 4000, 'shipped', 12.3, 24, 1],
      ['RAW-2404', '한우 비육 사료', 5000, 'shipped', 12.1, 25, 1]
    ];
    s.lots = seed.map((row, i) => lotRecord('LOT-' + String(i + 1).padStart(3, '0'), ...row));
    s.lots.filter(l => l.stage === 'shipped').forEach(l => { l.shipment = { destination: '데모 거래처 ' + (l.id === 'LOT-009' ? 'A' : 'B'), time: '초기 출하 기록', tick: 0 }; });
    s.orders = Orders.seed(s);
    event(s, 'init', null, '합성 데이터 10개 로트를 불러왔습니다. 실공장 데이터가 아닙니다.');
    return s;
  }
  function blocking(s, id) { return s.alerts.filter(a => a.lotId === id && !a.resolved && a.blocking); }
  function addAlert(s, lot, type, title, details) {
    const existing = s.alerts.find(a => a.lotId === lot.id && a.type === type && !a.resolved);
    if (existing) return existing;
    // Resolved history is bounded; open issues are never silently discarded.
    // Five issue types per at most 1000 lots bounds all open issues at 5000.
    if (s.alerts.length >= 5000) {
      const old = s.alerts.findIndex(a => a.resolved);
      if (old >= 0) s.alerts.splice(old, 1);
      else return null;
    }
    const alert = { id: 'ALT-' + s.nextAlertId++, lotId: lot.id, type, title, details,
      resolved: false, tick: s.tick, blocking: true, shippedImpact: lot.stage === 'shipped', resolution: null };
    s.alerts.push(alert);
    if (lot.stage === 'shipped') lot.status = 'warning';
    else { lot.status = 'hold'; lot.holdReason = title; }
    event(s, 'alert', lot.id, title + (alert.shippedImpact ? ' · 이미 출하된 로트 영향 확인 필요' : ' · 이동 보류'));
    return alert;
  }
  function checkQuality(s, lot) {
    if (lot.qualityChecked) return;
    lot.qualityChecked = true;
    if (!lot.rawId) addAlert(s, lot, 'missing', '원료 연결 누락', '원료 ID가 없어 상위 원료 기록을 연결할 수 없습니다.');
    if (lot.qc.stale || lot.qc.moisture === null || lot.qc.temperature === null) {
      addAlert(s, lot, 'sensor', '측정값 확인 필요', '측정값이 없거나 오래된 상태입니다. 재측정 기록을 확인해야 합니다.');
    } else if (lot.qc.moisture > s.settings.moistureLimit) {
      addAlert(s, lot, 'moisture', '수분 기준 초과', '기록 ' + lot.qc.moisture + '% / 데모 기준 ' + s.settings.moistureLimit + '%');
    }
  }
  function checkStorage(s, lot) {
    if (lot.storageChecked) return;
    lot.storageChecked = true;
    if (!lot.qc.stale && lot.qc.temperature !== null && lot.qc.temperature > s.settings.storageLimit) {
      addAlert(s, lot, 'temperature', '보관 온도 기준 초과', '기록 ' + lot.qc.temperature + '°C / 데모 기준 ' + s.settings.storageLimit + '°C');
    }
  }
  function step(s, minutes) {
    if (!finite(minutes) || minutes < 0 || minutes > 1440) return fail('시간은 0~1440분 범위의 유한 숫자여야 합니다.');
    if (!s.running || minutes === 0) return good();
    const startTick = s.tick;
    s.lots.forEach(lot => {
      let left = minutes;
      let consumed = 0;
      while (left > 1e-9 && lot.stage !== 'shipped') {
        if (lot.stage === 'quality') checkQuality(s, lot);
        if (lot.stage === 'warehouse') checkStorage(s, lot);
        if (lot.status === 'hold' || blocking(s, lot.id).length) break;
        const rate = lot.stage === 'warehouse' ? 1 : s.settings.productionRate / 100;
        const needed = (1 - lot.progress) * DURATIONS[lot.stage] / rate;
        if (left + 1e-9 < needed) { lot.progress = Math.min(1, lot.progress + left * rate / DURATIONS[lot.stage]); break; }
        left = Math.max(0, left - needed); consumed += needed;
        lot.stage = STAGES[STAGES.indexOf(lot.stage) + 1];
        lot.progress = lot.stage === 'shipped' ? 1 : 0;
        if (lot.stage === 'shipped') {
          lot.shipment = { destination: '데모 거래처 ' + (Number(lot.id.match(/\d+$/)?.[0] || 0) % 2 ? 'A' : 'B'),
            time: '가상 ' + (Math.round((startTick + consumed) * 10) / 10) + '분', tick: startTick + consumed };
        }
        event(s, 'transition', lot.id, LABELS[lot.stage] + ' 단계로 이동');
      }
    });
    s.tick += minutes;
    return good();
  }
  function hold(s, id, reason) {
    if (!reasonText(reason)) return fail('보류 사유를 1~500자로 입력하세요.');
    const lot = s.lots.find(l => l.id === id);
    if (!lot) return fail('로트를 찾을 수 없습니다.');
    if (lot.stage === 'shipped') return fail('이미 출하된 로트는 이동 보류할 수 없습니다. 영향 경보를 확인하세요.');
    lot.status = 'hold'; lot.holdReason = reason.trim();
    event(s, 'hold', id, '수동 보류: ' + reason.trim());
    return good();
  }
  function release(s, id, reason) {
    if (!reasonText(reason)) return fail('해제 사유를 1~500자로 입력하세요.');
    const lot = s.lots.find(l => l.id === id);
    if (!lot) return fail('로트를 찾을 수 없습니다.');
    if (lot.stage === 'shipped') return fail('이미 출하된 로트입니다.');
    if (blocking(s, id).length) return fail('미해결 차단 경보를 먼저 처리하세요.');
    if (lot.status !== 'hold') return fail('보류 중인 로트가 아닙니다.');
    if (!lot.rawId) return fail('원료 ID 연결을 먼저 복원하세요.');
    if (lot.qc.stale || lot.qc.moisture === null || lot.qc.temperature === null) return fail('신선한 수분·온도 재검사 기록이 필요합니다.');
    if (lot.qc.moisture > s.settings.moistureLimit || lot.qc.temperature > s.settings.storageLimit) return fail('현재 재검사 기록이 설정 기준을 초과합니다. 기준 이내의 기록을 확인하세요.');
    lot.status = 'ok'; lot.holdReason = null;
    event(s, 'release', id, '보류 해제: ' + reason.trim());
    return good();
  }
  function resolve(s, alertId, reason, recheck) {
    if (!reasonText(reason)) return fail('처리 사유를 1~500자로 입력하세요.');
    const alert = s.alerts.find(a => a.id === alertId);
    if (!alert) return fail('경보를 찾을 수 없습니다.');
    if (alert.resolved) return fail('이미 처리된 경보입니다.');
    if (recheck !== undefined && (!recheck || typeof recheck !== 'object' || Array.isArray(recheck))) return fail('재검사 기록 형식을 확인하세요.');
    recheck = recheck || {};
    const has = key => Object.prototype.hasOwnProperty.call(recheck, key);
    if (Object.keys(recheck).some(key => !['moisture', 'temperature', 'rawId'].includes(key))) return fail('지원하지 않는 재검사 필드입니다.');
    if (has('moisture') && (!finite(recheck.moisture) || recheck.moisture < 0 || recheck.moisture > 100)) return fail('재검사 수분은 0~100%의 숫자여야 합니다.');
    if (has('temperature') && (!finite(recheck.temperature) || recheck.temperature < -30 || recheck.temperature > 100)) return fail('재검사 온도는 -30~100°C의 숫자여야 합니다.');
    if (has('rawId') && (typeof recheck.rawId !== 'string' || !recheck.rawId.trim() || recheck.rawId.trim().length > 80)) return fail('재검사 원료 ID는 1~80자로 입력하세요.');
    const lot = s.lots.find(l => l.id === alert.lotId);
    if (lot && Object.keys(recheck).length) {
      const before = { moisture: lot.qc.moisture, temperature: lot.qc.temperature, stale: lot.qc.stale, rawId: lot.rawId };
      if (has('moisture')) lot.qc.moisture = recheck.moisture;
      if (has('temperature')) lot.qc.temperature = recheck.temperature;
      if (has('rawId')) lot.rawId = recheck.rawId.trim();
      if (has('moisture') && has('temperature')) lot.qc.stale = false;
      alert.reinspection = { tick: s.tick, before, recorded: Object.assign({}, recheck), reason: reason.trim() };
      lot.qualityChecked = true;
      event(s, 'reinspection', lot.id, '사용자 입력 재검사 기록: ' + JSON.stringify(recheck));
    }
    alert.resolved = true; alert.resolution = reason.trim(); alert.resolvedTick = s.tick;
    if (lot && lot.stage === 'shipped' && !blocking(s, lot.id).length) lot.status = lot.qc.stale || !lot.rawId || lot.qc.moisture === null || lot.qc.temperature === null || lot.qc.moisture > s.settings.moistureLimit || lot.qc.temperature > s.settings.storageLimit ? 'warning' : 'ok';
    event(s, 'resolve', alert.lotId, alert.title + ' 처리: ' + reason.trim());
    return good();
  }
  function inject(s, type) {
    if (!['moisture', 'missing', 'duplicate', 'sensor'].includes(type)) return fail('지원하지 않는 시나리오입니다.');
    if (s.alerts.filter(a => !a.resolved).length > 480) return fail('열린 경보를 먼저 처리하세요.');
    const pending = s.lots.find(l => l.stage !== 'shipped' && l.status !== 'hold') || s.lots.find(l => l.stage !== 'shipped');
    if (!pending) return fail('진행 중인 로트가 없습니다. CSV로 데모 로트를 추가할 수 있습니다.');
    let affected = [pending];
    if (type === 'moisture') {
      if (!pending.rawId) return fail('선택 가능한 원료 연결이 없습니다.');
      affected = s.lots.filter(l => l.rawId === pending.rawId);
      affected.forEach(l => {
        l.qc.moisture = Math.max(s.settings.moistureLimit + 1.8, l.qc.moisture || 0);
        l.qualityChecked = true;
        addAlert(s, l, type, '원료 연계 수분 초과', '합성 원료 ' + l.rawId + '의 연결 로트 · 기록 ' + l.qc.moisture.toFixed(1) + '% / 기준 ' + s.settings.moistureLimit + '%');
      });
    } else if (type === 'missing') {
      pending.previousRawId = pending.rawId; pending.rawId = '';
      addAlert(s, pending, type, '원료 연결 누락', '입고 기록의 원료 ID를 누락시킨 합성 시나리오입니다. 기존 연결: ' + pending.previousRawId);
      pending.qualityChecked = true;
    } else if (type === 'duplicate') {
      addAlert(s, pending, type, '입고 스캔 중복', pending.id + '의 중복 스캔을 감지했습니다. 수량은 추가하지 않았습니다.');
    } else {
      pending.qc.stale = true;
      addAlert(s, pending, type, '센서 기록 지연', '기존 수분·온도 기록을 오래된 값으로 표시했습니다. 실시간 측정 연결이 아닙니다.');
      pending.qualityChecked = true;
    }
    return good({ affectedIds: affected.map(l => l.id) });
  }
  function configure(s, key, value) {
    const bounds = { productionRate: [20, 200], moistureLimit: [8, 20], storageLimit: [18, 40], speed: [.25, 8] };
    if (!bounds[key] || !finite(value) || value < bounds[key][0] || value > bounds[key][1]) return fail('설정 범위를 확인하세요.');
    if (key === 'speed') s.speed = value;
    else s.settings[key] = value;
    if (key === 'moistureLimit') s.lots.forEach(l => { l.qualityChecked = false; if (STAGES.indexOf(l.stage) >= 2) checkQuality(s, l); });
    if (key === 'storageLimit') s.lots.forEach(l => { l.storageChecked = false; if (l.stage === 'warehouse') checkStorage(s, l); });
    event(s, 'configure', null, key + ' 설정: ' + value);
    return good();
  }
  function parseCsv(text) {
    const rows = []; let row = [], cell = '', quoted = false, closed = false;
    for (let i = 0; i <= text.length; i++) {
      const c = text[i];
      if (quoted) {
        if (c === undefined) throw new Error('닫히지 않은 CSV 따옴표입니다.');
        if (c === '"') { if (text[i + 1] === '"') { cell += '"'; i++; } else { quoted = false; closed = true; } }
        else cell += c;
      } else if (c === '"') {
        if (cell || closed) throw new Error('CSV 따옴표 위치가 잘못되었습니다.');
        quoted = true;
      } else if (c === ',' || c === '\n' || c === '\r' || c === undefined) {
        row.push(cell); cell = ''; closed = false;
        if (c !== ',') { if (row.some(v => v.length)) rows.push(row); row = []; if (c === '\r' && text[i + 1] === '\n') i++; }
      } else { if (closed) throw new Error('닫는 따옴표 뒤에는 구분자만 허용됩니다.'); cell += c; }
    }
    return rows;
  }
  function importCsv(s, text) {
    if (typeof text !== 'string' || new TextEncoder().encode(text).length > 1024 * 1024) return fail('CSV는 1MB 이하의 텍스트여야 합니다.');
    let rows;
    try { rows = parseCsv(text.replace(/^\uFEFF/, '')); } catch (e) { return fail(e.message); }
    const cols = ['id', 'rawId', 'product', 'quantity', 'moisture', 'temperature', 'stage'];
    if (!rows.length || rows[0].length !== cols.length || rows[0].some((v, i) => v.trim() !== cols[i])) return fail('CSV 열 순서: ' + cols.join(','));
    if (rows.length < 2 || rows.length - 1 > 1000 || s.lots.length + rows.length - 1 > MAX_LOTS) return fail('로트 수는 기존 항목 포함 최대 1000개이며 최소 1행이 필요합니다.');
    const ids = new Set(s.lots.map(l => l.id)); const incoming = [];
    for (let i = 1; i < rows.length; i++) {
      if (rows[i].length !== cols.length) return fail((i + 1) + '행: 열 수가 다릅니다.');
      const [id, rawId, product, quantityText, moistureText, temperatureText, stage] = rows[i].map(v => v.trim());
      const quantity = Number(quantityText), moisture = moistureText === '' ? null : Number(moistureText), temperature = temperatureText === '' ? null : Number(temperatureText);
      if (!id || id.length > 80 || !rawId || rawId.length > 80 || !product || product.length > 200) return fail((i + 1) + '행: ID·원료 ID·제품명을 확인하세요.');
      if (ids.has(id)) return fail((i + 1) + '행: 중복 로트 ID ' + id);
      if (!quantityText || !finite(quantity) || quantity <= 0 || quantity > 1e9) return fail((i + 1) + '행: 수량은 0 초과 10억 이하의 kg입니다.');
      if (moisture !== null && (!finite(moisture) || moisture < 0 || moisture > 100)) return fail((i + 1) + '행: 수분은 0~100%입니다.');
      if (temperature !== null && (!finite(temperature) || temperature < -30 || temperature > 100)) return fail((i + 1) + '행: 온도는 -30~100°C입니다.');
      if (!STAGES.includes(stage)) return fail((i + 1) + '행: 허용되지 않은 단계입니다.');
      const lot = lotRecord(id, rawId, product, quantity, stage, moisture, temperature, stage === 'shipped' ? 1 : 0);
      lot.imported = true;
      if (stage === 'shipped') lot.shipment = { destination: 'CSV 출하처 미기재', time: 'CSV 출하 시각 미기재', tick: null };
      incoming.push(lot); ids.add(id);
    }
    s.lots.push(...incoming);
    event(s, 'import', null, 'CSV ' + incoming.length + '개 로트 추가 · 원료 연결은 입력값이며 외부 검증되지 않음');
    incoming.forEach(l => { if (STAGES.indexOf(l.stage) >= 2) checkQuality(s, l); if (l.stage === 'warehouse') checkStorage(s, l); });
    return good({ imported: incoming.length });
  }
  function exportCsv(s) {
    const escape = v => '"' + String(v === null || v === undefined ? '' : v).replace(/"/g, '""') + '"';
    // CSV quoting alone does not prevent spreadsheet formulas. Only textual cells
    // are neutralized; legitimate negative numeric temperatures remain numeric.
    const safeText = value => {
      const text = String(value === null || value === undefined ? '' : value);
      return /^[\s\uFEFF]*[=+\-@]|^[\t\r\n]/.test(text) ? "'" + text : text;
    };
    return '\uFEFFid,rawId,product,quantity,moisture,temperature,stage\r\n' + s.lots.map(l => [safeText(l.id), safeText(l.rawId), safeText(l.product), l.quantity, l.qc.moisture, l.qc.temperature, safeText(l.stage)].map(escape).join(',')).join('\r\n');
  }
  function report(s) {
    const byStage = Object.fromEntries(STAGES.map(stage => [stage, { lots: 0, kg: 0 }]));
    let totalKg = 0, shippedKg = 0, heldKg = 0, activeKg = 0;
    s.lots.forEach(l => { totalKg += l.quantity; byStage[l.stage].lots++; byStage[l.stage].kg += l.quantity;
      if (l.stage === 'shipped') shippedKg += l.quantity; else if (l.status === 'hold') heldKg += l.quantity; else activeKg += l.quantity; });
    const open = s.alerts.filter(a => !a.resolved);
    return { totalKg, shippedKg, heldKg, activeKg, unresolved: open.length, byStage,
      affectedLots: [...new Set(open.map(a => a.lotId))].map(id => { const l = s.lots.find(x => x.id === id); return { id, stage: l.stage, kg: l.quantity, shipped: l.stage === 'shipped' }; }) };
  }
  function summary(s, id) {
    const lot = s.lots.find(l => l.id === id);
    if (!lot) return { title: '로트 없음', text: '선택한 로트를 찾을 수 없습니다.', evidence: [], nextSteps: ['목록에서 로트를 선택하세요.'] };
    const related = lot.rawId ? s.lots.filter(l => l.rawId === lot.rawId) : [lot];
    const open = s.alerts.filter(a => a.lotId === id && !a.resolved);
    const kg = related.reduce((sum, l) => sum + l.quantity, 0);
    const evidence = [
      '데이터 출처: ' + (lot.imported ? 'CSV 입력값 · 원료 연결 외부 미검증' : '합성 데모 기록'),
      '현재 단계: ' + LABELS[lot.stage] + ' · 수량 ' + lot.quantity.toLocaleString('ko-KR') + ' kg',
      '원료 ID: ' + (lot.rawId || '누락') + ' · 동일 입력 원료 ID ' + related.length + '개 로트 / ' + kg.toLocaleString('ko-KR') + ' kg',
      '수분: ' + (lot.qc.moisture === null ? '기록 없음' : lot.qc.moisture + '%') + ' / 데모 기준 ' + s.settings.moistureLimit + '%',
      '온도: ' + (lot.qc.temperature === null ? '기록 없음' : lot.qc.temperature + '°C') + ' / 보관 기준 ' + s.settings.storageLimit + '°C',
      '센서 기록: ' + (lot.qc.stale ? '지연 상태 · 재측정 필요' : '합성 또는 입력 측정값'),
      '미해결 경보: ' + open.length + '건'
    ];
    if (lot.shipment) evidence.push('출하 기록: ' + lot.shipment.destination + ' · ' + lot.shipment.time);
    const nextSteps = open.map(a => a.title + ': 근거 확인 후 처리 사유를 기록하세요.');
    if (lot.stage === 'shipped' && open.length) nextSteps.push('이미 출하된 물량이므로 거래처 영향 및 후속 대응을 별도로 확인하세요.');
    else if (lot.status === 'hold') nextSteps.push(open.length ? '차단 경보를 모두 처리한 뒤 해제 사유를 기록하세요.' : '검토 근거를 확인한 뒤 해제 사유를 기록하세요.');
    else if (!open.length) nextSteps.push('다음 단계 진행과 기록을 확인하세요.');
    if (s.alerts.some(a => a.lotId === id && a.resolved)) evidence.push('경보 처리는 사유 기록입니다. 별도 재검사 입력이 있는 경우에만 측정값이 갱신됩니다. 외부 품질 적합 증명은 아닙니다.');
    if (lot.qc.stale || !lot.rawId || lot.qc.moisture === null || lot.qc.temperature === null || lot.qc.moisture > s.settings.moistureLimit || lot.qc.temperature > s.settings.storageLimit) nextSteps.push('현재 기록의 연결·신선도·기준을 확인하세요. 기준 미달 기록은 경보 처리 후에도 해제할 수 없습니다.');
    return { title: lot.id + ' 기록 요약', text: lot.product + ' ' + lot.quantity.toLocaleString('ko-KR') + ' kg이 ' + LABELS[lot.stage] + '에 있습니다. ' +
      (open.length ? '미해결 경보 ' + open.length + '건으로 ' + (lot.stage === 'shipped' ? '출하 영향 검토가 필요합니다.' : '진행을 보류합니다.') : lot.status === 'hold' ? '수동 또는 처리 후 보류 상태입니다.' : '현재 미해결 경보가 없습니다.') +
      ' 이 요약은 저장된 기록을 규칙으로 정리한 데모입니다.', evidence, nextSteps };
  }
  const api = { STAGES, LABELS, DURATIONS, createState, step, inject, hold, release, resolve, configure, importCsv, exportCsv, report, summary };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.FeedEngine = api;
})(typeof window !== 'undefined' ? window : null);
