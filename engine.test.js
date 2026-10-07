'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const E = require('./engine.js');
let passed = 0;
function test(name, fn) { fn(); passed++; console.log('PASS ' + name); }
function state() { return E.createState(); }
function invariants(s, total) {
  const r = E.report(s);
  assert.equal(r.totalKg, total);
  assert.equal(r.shippedKg + r.heldKg + r.activeKg, total);
  assert.equal(Object.values(r.byStage).reduce((sum, v) => sum + v.kg, 0), total);
  assert.equal(new Set(s.lots.map(l => l.id)).size, s.lots.length);
  s.lots.forEach(l => {
    assert.ok(E.STAGES.includes(l.stage));
    assert.ok(Number.isFinite(l.quantity) && l.quantity > 0);
    assert.ok(Number.isFinite(l.progress) && l.progress >= 0 && l.progress <= 1);
    assert.ok(['ok', 'warning', 'hold'].includes(l.status));
    if (l.stage === 'shipped') assert.ok(l.shipment);
    if (l.stage !== 'shipped' && s.alerts.some(a => a.lotId === l.id && a.blocking && !a.resolved)) assert.equal(l.status, 'hold');
  });
  assert.ok(s.events.length <= 500);
  assert.doesNotThrow(() => JSON.parse(JSON.stringify(s)));
}
test('seed is serializable synthetic data with raw provenance and exact conservation', () => {
  const s = state(), r = E.report(s);
  assert.equal(s.version, 1); assert.equal(s.synthetic, true); assert.equal(s.lots.length, 10);
  assert.equal(r.totalKg, 44500); assert.equal(r.shippedKg, 9000);
  invariants(s, 44500);
});
test('production moves fixed lots to shipping without inventing or losing material', () => {
  const s = state();
  for (let i = 0; i < 100; i++) { assert.ok(E.step(s, .5).ok); invariants(s, 44500); }
  assert.equal(E.report(s).shippedKg, 44500); assert.equal(s.lots.length, 10);
});
test('paused clock and invalid duration never mutate simulation', () => {
  const s = state(); s.running = false; const before = JSON.stringify(s);
  assert.ok(E.step(s, 20).ok); assert.equal(JSON.stringify(s), before);
  assert.equal(E.step(s, Infinity).ok, false); assert.equal(E.step(s, -1).ok, false);
  assert.equal(E.step(s, 1441).ok, false); assert.equal(JSON.stringify(s), before);
});
test('moisture fans out by raw ID including previously shipped impact', () => {
  const s = state(), out = E.inject(s, 'moisture');
  assert.deepEqual(out.affectedIds, ['LOT-001', 'LOT-003', 'LOT-009']);
  assert.equal(s.lots.find(l => l.id === 'LOT-009').status, 'warning');
  assert.equal(s.alerts.find(a => a.lotId === 'LOT-009').shippedImpact, true);
  assert.equal(E.report(s).heldKg, 8400); assert.equal(E.report(s).unresolved, 3);
  const p = s.lots.find(l => l.id === 'LOT-001').progress;
  E.step(s, 100); assert.equal(s.lots.find(l => l.id === 'LOT-001').progress, p);
  invariants(s, 44500);
});
test('release rejects unresolved issues; reason required; actual reinspection is logged', () => {
  const s = state(); E.inject(s, 'moisture');
  const lot = s.lots[0], a = s.alerts.find(x => x.lotId === lot.id), moisture = lot.qc.moisture;
  assert.equal(E.release(s, lot.id, '확인').ok, false);
  assert.equal(E.resolve(s, a.id, '').ok, false);
  assert.ok(E.resolve(s, a.id, '재검사 기록을 검토한 데모 승인', { moisture: 12.5, temperature: 24 }).ok);
  assert.equal(lot.status, 'hold'); assert.notEqual(lot.qc.moisture, moisture);
  assert.equal(a.reinspection.before.moisture, moisture);
  assert.equal(E.release(s, lot.id, ' ').ok, false);
  assert.ok(E.release(s, lot.id, '별도 검토 결과와 예외 승인 기록 확인').ok);
  assert.ok(s.events.some(e => e.kind === 'resolve'));
  assert.ok(s.events.some(e => e.kind === 'release'));
  E.step(s, 100); assert.equal(lot.stage, 'shipped');
});
test('acknowledging alert alone cannot release excessive, stale or missing records', () => {
  const s = state(); E.inject(s, 'moisture'); const lot = s.lots[0], alert = s.alerts.find(a => a.lotId === lot.id), before = lot.qc.moisture;
  assert.ok(E.resolve(s, alert.id, '확인 사유만 기록').ok); assert.equal(lot.qc.moisture, before);
  assert.equal(E.release(s, lot.id, '해제 시도').ok, false);
  const t = state(); E.inject(t, 'sensor'); const a = t.alerts[0];
  const snapshot = JSON.stringify(t); assert.equal(E.resolve(t, a.id, '재검사', { moisture: NaN }).ok, false); assert.equal(JSON.stringify(t), snapshot);
  assert.ok(E.resolve(t, a.id, '수분만 확인', { moisture: 12 }).ok); assert.equal(t.lots[0].qc.stale, true);
  assert.equal(E.release(t, t.lots[0].id, '해제 시도').ok, false);
  const m = state(); E.inject(m, 'missing');
  assert.ok(E.resolve(m, m.alerts[0].id, '입고 기록 대조', { rawId: 'RAW-2401', moisture: 12.3, temperature: 22 }).ok);
  assert.ok(E.release(m, m.lots[0].id, '연결과 재검사 확인').ok);
});
test('hold blocks warehouse shipping and requires explicit reason', () => {
  const s = state(), lot = s.lots.find(l => l.stage === 'warehouse');
  assert.equal(E.hold(s, lot.id, '').ok, false);
  assert.ok(E.hold(s, lot.id, '포장 표시 확인').ok); const p = lot.progress;
  E.step(s, 100); assert.equal(lot.stage, 'warehouse'); assert.equal(lot.progress, p);
  assert.ok(E.release(s, lot.id, '표시 확인 완료').ok); E.step(s, 20); assert.equal(lot.stage, 'shipped');
  assert.equal(E.hold(s, lot.id, '사후 확인').ok, false);
});
test('missing, duplicate and sensor scenarios retain meaningful distinct evidence', () => {
  const s = state();
  assert.ok(E.inject(s, 'missing').ok); assert.equal(s.lots[0].rawId, '');
  assert.ok(E.inject(s, 'duplicate').ok); assert.equal(s.lots.length, 10); assert.equal(E.report(s).totalKg, 44500);
  assert.ok(E.inject(s, 'sensor').ok); assert.ok(s.lots.some(l => l.qc.stale));
  assert.deepEqual(s.alerts.map(a => a.type), ['missing', 'duplicate', 'sensor']);
  invariants(s, 44500);
});
test('quality and storage limits create blocking exceptions grounded in measurements', () => {
  const s = state();
  assert.ok(E.configure(s, 'moistureLimit', 12).ok);
  assert.ok(s.alerts.some(a => a.type === 'moisture' && a.lotId === 'LOT-005'));
  assert.ok(E.configure(s, 'storageLimit', 24).ok);
  assert.ok(s.alerts.some(a => a.type === 'temperature' && a.lotId === 'LOT-007'));
  const before = JSON.stringify(s);
  assert.equal(E.configure(s, 'productionRate', 0).ok, false);
  assert.equal(E.configure(s, 'unknown', 1).ok, false); assert.equal(JSON.stringify(s), before);
  invariants(s, 44500);
});
test('CSV quoted product, BOM, CRLF and escaped quotes import transactionally', () => {
  const s = state();
  const csv = '\uFEFFid,rawId,product,quantity,moisture,temperature,stage\r\n"LOT-X","RAW-X","제품, ""시험""",1200,12,22,intake\r\n';
  const out = E.importCsv(s, csv); assert.ok(out.ok); assert.equal(out.imported, 1);
  assert.equal(s.lots.at(-1).product, '제품, "시험"'); assert.equal(s.lots.at(-1).imported, true);
  assert.ok(E.summary(s, 'LOT-X').evidence.some(x => x.includes('외부 미검증')));
  invariants(s, 45700);
});
test('CSV rejects duplicates and malformed fields atomically with no events or partial writes', () => {
  const header = 'id,rawId,product,quantity,moisture,temperature,stage\n';
  const invalidRows = [
    'NEW,RAW,제품,100,12,20,intake\nLOT-001,RAW,제품,100,12,20,intake',
    'NEW,RAW,제품,100,12,20,intake\nNEW,RAW,제품,100,12,20,intake',
    'NEW,RAW,제품,NaN,12,20,intake', 'NEW,RAW,제품,0,12,20,intake',
    'NEW,RAW,제품,100,101,20,intake', 'NEW,RAW,제품,100,12,-31,intake',
    'NEW,RAW,제품,100,12,20,unknown', 'NEW,,제품,100,12,20,intake',
    'NEW,RAW,"미완료,100,12,20,intake', 'NEW,RAW,"제품"x,100,12,20,intake',
    'NEW,RAW,제품,100,12,20,intake,extra'
  ];
  invalidRows.forEach(row => { const s = state(), before = JSON.stringify(s); assert.equal(E.importCsv(s, header + row).ok, false, row); assert.equal(JSON.stringify(s), before); });
  const s = state(), before = JSON.stringify(s);
  assert.equal(E.importCsv(s, 'a'.repeat(1024 * 1024 + 1)).ok, false);
  assert.equal(E.importCsv(s, header + Array.from({ length: 1001 }, (_, i) => `X${i},RAW,제품,1,12,20,intake`).join('\n')).ok, false);
  assert.equal(JSON.stringify(s), before);
});
test('missing imported measurements create a sensor block; exported data round trips', () => {
  const s = state(); assert.ok(E.importCsv(s, 'id,rawId,product,quantity,moisture,temperature,stage\nNEW,RAW,제품,100,,,quality').ok);
  assert.equal(s.lots.at(-1).status, 'hold'); assert.equal(s.alerts.at(-1).type, 'sensor');
  const fresh = state(); fresh.lots = []; const out = E.importCsv(fresh, E.exportCsv(s)); assert.ok(out.ok);
  assert.equal(fresh.lots.length, s.lots.length); assert.equal(E.report(fresh).totalKg, E.report(s).totalKg);
  assert.equal(E.importCsv(s, E.exportCsv(s)).ok, false);
});
test('CSV export neutralizes formula-capable text while preserving numerical values', () => {
  const header = 'id,rawId,product,quantity,moisture,temperature,stage\n';
  const s = state(); s.lots = [];
  assert.ok(E.importCsv(s, header + '@LOT,-RAW,"=SUM(1,2)",100,12,-10,intake').ok);
  const exported = E.exportCsv(s);
  assert.ok(exported.includes('"\'@LOT","\'-RAW","\'=SUM(1,2)","100","12","-10","intake"'));
  const imported = state(); imported.lots = [];
  assert.ok(E.importCsv(imported, exported).ok);
  assert.equal(imported.lots[0].product, "'=SUM(1,2)");
  assert.equal(imported.lots[0].qc.temperature, -10);
  ['=1+1', '+1+1', '-1+1', '@SUM(A1)', '  =1+1', '\t=1+1', '\r=1+1', '\tplain', '\rplain'].forEach(text => {
    s.lots[0].product = text;
    assert.ok(E.exportCsv(s).includes('"\'' + text + '"'), JSON.stringify(text));
  });
  s.lots[0].id = 'LOT-NORMAL'; s.lots[0].rawId = 'RAW-NORMAL'; s.lots[0].product = '일반 사료, 시험 배치';
  const ordinary = state(); ordinary.lots = [];
  assert.ok(E.importCsv(ordinary, E.exportCsv(s)).ok);
  assert.equal(ordinary.lots[0].product, s.lots[0].product);
  assert.equal(ordinary.lots[0].id, s.lots[0].id);
  assert.equal(ordinary.lots[0].rawId, s.lots[0].rawId);
});
test('sample CSV is valid and intentionally includes a quality exception', () => {
  const s = state(); const out = E.importCsv(s, fs.readFileSync(path.join(__dirname, 'sample-lots.csv'), 'utf8'));
  assert.equal(out.imported, 3); assert.equal(s.lots.find(l => l.id === 'LOT-103').status, 'hold');
  invariants(s, 51800);
});
test('summary is deterministic, record-grounded and reports shipped impacts', () => {
  const s = state(); E.inject(s, 'moisture');
  assert.deepEqual(E.summary(s, 'LOT-009'), E.summary(JSON.parse(JSON.stringify(s)), 'LOT-009'));
  const sum = E.summary(s, 'LOT-009');
  assert.ok(sum.text.includes('출하 영향')); assert.ok(sum.evidence.some(e => e.includes('12,400 kg')));
  assert.ok(sum.nextSteps.some(n => n.includes('거래처 영향')));
  assert.ok(sum.text.includes('규칙')); assert.equal(E.summary(s, 'MISSING').evidence.length, 0);
});
test('long mixed scenario preserves invariants, bounded events and writable persistence', () => {
  let s = state();
  for (let i = 0; i < 800; i++) {
    if (i % 7 === 0) E.configure(s, 'productionRate', 20 + i % 181);
    if (i % 19 === 0) E.inject(s, ['moisture', 'sensor', 'duplicate'][i % 3]);
    if (i % 23 === 0) {
      s.alerts.filter(a => !a.resolved).forEach(a => E.resolve(s, a.id, '합성 테스트 검토', { moisture: 12, temperature: 24 }));
      s.lots.filter(l => l.status === 'hold').forEach(l => E.release(s, l.id, '합성 테스트 검토 완료'));
    }
    E.step(s, .12); invariants(s, 44500);
    if (i % 50 === 0) s = JSON.parse(JSON.stringify(s));
  }
  for (let i = 0; i < 510; i++) E.configure(s, 'speed', 1 + i % 8);
  assert.equal(s.events.length, 500); assert.equal(s.lots.length, 10);
});
console.log(`${passed} tests passed.`);
