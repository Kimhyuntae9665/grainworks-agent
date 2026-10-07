'use strict';
const assert=require('node:assert/strict'),O=require('./order_model.js'),E=require('./engine.js'),F=require('./factory_model.js');
let count=0;function test(name,fn){fn();count++;console.log('PASS '+name);}
function change(s,quantityKg,extra={}){return O.setAllocation(s,{orderId:'SO-001',lineId:'SO-001-L1',lotId:'LOT-001',quantityKg,assetId:'TRK-01',...extra});}
function conserved(snapshot){
 for(const order of snapshot.orders)for(const record of [order,...order.lines]){
  assert.equal(record.shippedKg+record.readyKg+record.heldKg+record.workInProgressKg,record.allocatedKg);
  assert.equal(record.allocatedKg+record.unallocatedKg,record.requestedKg);
  assert.equal(record.shippedKg+record.remainingKg,record.requestedKg);
  assert.equal(record.readyKg+record.notReadyKg,record.remainingKg);
  assert.equal(record.status,record.state);assert.ok(record.shippedImpactKg<=record.shippedKg);
 }
}
test('five deterministic seeded orders match products, exact Lot mass and IDs',()=>{
 const s=E.createState(),snap=O.snapshot(s);assert.equal(O.validate(s),true);
 assert.deepEqual(s.orders.map(o=>o.id),['SO-001','SO-002','SO-003','SO-004','SO-005']);
 assert.deepEqual(s.orders.map(o=>o.lines[0].id),s.orders.map(o=>o.id+'-L1'));
 assert.deepEqual(s.orders.map(o=>o.lines[0].requestedKg),[13000,8200,11000,9700,3200]);
 assert.equal(snap.totals.requestedKg,45100);assert.equal(snap.totals.allocatedKg,44500);
 assert.equal(snap.totals.unallocatedKg,600);assert.equal(snap.totals.shippedKg,9000);
 assert.equal(snap.totals.readyKg,7700);assert.equal(snap.totals.workInProgressKg,27800);
 assert.deepEqual(O.seed(s),s.orders);conserved(snap);
});
test('linked warehouse readiness, held mass and already shipped impact stay distinct',()=>{
 const s=E.createState();E.inject(s,'moisture');E.hold(s,'LOT-007','합성 확인');
 const snap=O.snapshot(s),chicken=snap.orders[0],layers=snap.orders[3];
 assert.equal(chicken.heldKg,8400);assert.equal(chicken.shippedKg,4000);assert.equal(chicken.shippedImpactKg,4000);
 assert.equal(layers.readyKg,0);assert.equal(layers.heldKg,4500);assert.equal(layers.status,'held');
 assert.equal(snap.totals.shippedImpactKg,4000);assert.equal(snap.totals.allocatedKg,44500);
 assert.equal(chicken.lines[0].allocations[2].status,'shipped');assert.equal(chicken.lines[0].allocations[2].shippedImpact,true);
 conserved(snap);
});
test('warning-only shipped Lot impact remains shipped and never enters held quantity',()=>{
 const s=E.createState();s.lots[8].status='warning';
 const order=O.snapshot(s,'SO-001').orders[0];assert.equal(order.heldKg,0);assert.equal(order.shippedImpactKg,4000);
 assert.equal(order.shippedKg,4000);conserved(O.snapshot(s));
});
test('warehouse readiness checks match truck QC blocks even before an alert is recorded',()=>{
 const A=require('./assets.js');
 for(const mutate of [l=>l.qc.stale=true,l=>l.qc.moisture=null,l=>l.qc.temperature=null,l=>l.qc.moisture=14.01,l=>l.qc.temperature=28.01]){
  const s=E.createState();mutate(s.lots[7]);
  assert.equal(O.snapshot(s,'SO-005').orders[0].readyKg,0);
  assert.equal(O.snapshot(s,'SO-005').orders[0].heldKg,3200);
  assert.equal(O.snapshot(s,'SO-005').orders[0].lines[0].allocations[0].needsConfirmation,true);
  assert.deepEqual(O.snapshot(s,'SO-005').orders[0].lines[0].allocations[0].alertIds,[]);
  assert.ok(A.list(s).filter(a=>a.kind==='truck').some(a=>a.lots.some(l=>l.id==='LOT-008')&&a.heldKg>=3200));
 }
 const s=E.createState();s.lots[7].qc.moisture=14;s.lots[7].qc.temperature=28;
 assert.equal(O.snapshot(s,'SO-005').orders[0].readyKg,3200);
 delete s.lots[7].qc;assert.equal(O.snapshot(s,'SO-005').orders[0].heldKg,3200);
 const custom=E.createState();custom.settings.moistureLimit=12;custom.settings.storageLimit=25;
 assert.equal(O.snapshot(custom,'SO-005').orders[0].readyKg,0);assert.equal(O.snapshot(custom,'SO-005').orders[0].heldKg,3200);
 const unknown=E.createState();delete unknown.settings;
 assert.equal(O.snapshot(unknown,'SO-005').orders[0].heldKg,3200);
 const shipped=E.createState();shipped.lots[8].qc.moisture=NaN;
 assert.equal(O.snapshot(shipped,'SO-001').orders[0].shippedImpactKg,4000);assert.equal(O.snapshot(shipped,'SO-001').orders[0].heldKg,0);
});
test('allocation replacement and removal emit events without changing Lot quantity or clock',()=>{
 const s=E.createState(),lots=JSON.stringify(s.lots),tick=s.tick,count=s.events.length;
 assert.ok(change(s,2400,{assetId:'TRK-02'}).ok);assert.equal(s.orders[0].lines[0].allocations[0].quantityKg,2400);
 assert.equal(O.snapshot(s).totals.unallocatedKg,3000);assert.equal(s.events.length,count+1);
 assert.ok(change(s,0).ok);assert.equal(s.orders[0].lines[0].allocations.length,2);
 assert.equal(s.tick,tick);assert.equal(JSON.stringify(s.lots),lots);conserved(O.snapshot(s));
});
test('partial same-Lot cross-order allocation accepts only globally bounded quantity atomically',()=>{
 const s=E.createState();assert.ok(change(s,2400).ok);
 s.orders.push({id:'SO-PART',customer:'합성 부분 배분',dueTick:60,lines:[{id:'SO-PART-L1',product:s.lots[0].product,requestedKg:3000,allocations:[]}]});
 const input={orderId:'SO-PART',lineId:'SO-PART-L1',assetId:'TRK-02'};
 assert.ok(change(s,2000,input).ok);const before=JSON.stringify(s);
 assert.equal(change(s,2401,input).ok,false);assert.equal(JSON.stringify(s),before);
 assert.ok(change(s,2400,input).ok);assert.equal(O.snapshot(s).totals.allocatedKg,44500);conserved(O.snapshot(s));
});
test('invalid set inputs, product mismatch, line excess and shipped edits leave whole state unchanged',()=>{
 const inputs=[{quantityKg:NaN},{quantityKg:Infinity},{quantityKg:-1},{quantityKg:'2'},{lotId:'UNKNOWN'},
  {assetId:'FL-01'},{lotId:'LOT-002'},{quantityKg:5000},{lotId:'LOT-009',quantityKg:0},
  {lotId:'LOT-009',quantityKg:4000},{orderId:'UNKNOWN'},{lineId:'UNKNOWN'},{extra:1}];
 for(const input of inputs){const s=E.createState(),before=JSON.stringify(s);assert.equal(change(s,10,input).ok,false,JSON.stringify(input));assert.equal(JSON.stringify(s),before);}
 const s=E.createState();s.orders[0].lines[0].requestedKg=12400;assert.ok(change(s,4799).ok);
 // Free 1 kg in the Lot but no corresponding room in a second requested line.
 s.orders.push({id:'SO-SMALL',customer:'합성',dueTick:60,lines:[{id:'SO-SMALL-L1',product:s.lots[0].product,requestedKg:.5,allocations:[]}]});
 const before=JSON.stringify(s);assert.equal(change(s,1,{orderId:'SO-SMALL',lineId:'SO-SMALL-L1'}).ok,false);assert.equal(JSON.stringify(s),before);
});
test('all persisted orders validate duplicate IDs, duplicate Lot links and non-finite/unknown allocation',()=>{
 const mutations=[
  s=>s.orders.push(JSON.parse(JSON.stringify(s.orders[0]))),
  s=>s.orders[1].lines[0].id=s.orders[0].lines[0].id,
  s=>s.orders[0].lines.push(JSON.parse(JSON.stringify(s.orders[0].lines[0]))),
  s=>s.orders[0].lines[0].allocations.push({...s.orders[0].lines[0].allocations[0]}),
  s=>s.orders[0].lines[0].allocations[0].quantityKg=NaN,
  s=>s.orders[0].lines[0].allocations[0].quantityKg=0,
  s=>s.orders[0].lines[0].allocations[0].lotId='UNKNOWN',
  s=>s.orders[0].lines[0].allocations[0].lotId='LOT-002',
  s=>s.orders[0].lines[0].requestedKg=0,
  s=>s.orders[0].dueTick=Infinity,
  s=>s.lots.push({...s.lots[0]})
 ];
 for(const mutate of mutations){const s=E.createState();mutate(s);assert.throws(()=>O.validate(s));assert.throws(()=>O.snapshot(s));const before=JSON.stringify(s);assert.equal(change(s,1).ok,false);assert.equal(JSON.stringify(s),before);}
 assert.throws(()=>O.snapshot(E.createState(),'SO-MISSING'));
});
test('due ticks report remaining work only and reset restores deterministic allocations',()=>{
 const s=E.createState();s.tick=60;assert.equal(O.snapshot(s).orders[0].overdue,false);
 s.tick=61;assert.ok(O.snapshot(s).orders.every(o=>o.overdue));
 E.step(s,100);const snap=O.snapshot(s);assert.equal(snap.orders[1].status,'shipped');assert.equal(snap.orders[1].overdue,false);
 assert.equal(snap.orders[0].remainingKg,600);assert.equal(snap.orders[0].overdue,true);
 assert.deepEqual(E.createState().orders,O.seed(E.createState()));conserved(snap);
});
test('order, total-line and total-allocation resource bounds reject oversized data',()=>{
 const s=E.createState(),line=i=>({id:'L-'+i,product:s.lots[0].product,requestedKg:1,allocations:[]});
 s.orders=Array.from({length:1001},(_,i)=>({id:'O-'+i,customer:'합성',dueTick:60,lines:[line(i)]}));assert.throws(()=>O.validate(s),/최대 1000/);
 s.orders=[{id:'O',customer:'합성',dueTick:60,lines:Array.from({length:1001},(_,i)=>line(i))}];assert.throws(()=>O.validate(s),/주문행/);
 s.lots=Array.from({length:1000},(_,i)=>({id:'LOT-'+i,product:'합성',quantity:1,stage:'intake'}));
 s.orders=[{id:'O',customer:'합성',dueTick:60,lines:Array.from({length:11},(_,i)=>({id:'L-'+i,product:'합성',requestedKg:1,allocations:s.lots.map(l=>({lotId:l.id,quantityKg:.00001,assetId:'TRK-01'}))}))}];
 assert.throws(()=>O.validate(s),/배분/);
});
test('legacy states stay untouched on read; ensure seeds only existing matching Lots without clock/event action',()=>{
 const legacy=E.createState();delete legacy.orders;const before=JSON.stringify(legacy);
 assert.equal(O.validate(legacy),true);assert.deepEqual(O.snapshot(legacy).orders,[]);assert.equal(JSON.stringify(legacy),before);
 legacy.lots=legacy.lots.filter(l=>l.id!=='LOT-003');legacy.lots[0].product='다른 합성 제품';
 const tick=legacy.tick,events=JSON.stringify(legacy.events);O.ensure(legacy);
 const links=legacy.orders.flatMap(o=>o.lines.flatMap(l=>l.allocations));
 assert.ok(!links.some(a=>['LOT-001','LOT-003'].includes(a.lotId)));assert.equal(legacy.tick,tick);assert.equal(JSON.stringify(legacy.events),events);
 assert.equal(O.validate(legacy),true);const copy=JSON.stringify(legacy.orders);O.ensure(legacy);assert.equal(JSON.stringify(legacy.orders),copy);
 const empty={lots:[],alerts:[]};O.ensure(empty);assert.equal(O.snapshot(empty).totals.allocatedKg,0);
});
test('copied capacity comparisons project readiness with future tick and preserve source orders/mass',()=>{
 const s=E.createState(),before=JSON.stringify(s),result=F.simulate(s,{stationId:'PACK-01',downtimeMinutes:10,horizonMinutes:5});
 assert.equal(result.baseline.orders.tick,5);assert.equal(result.branch.orders.tick,5);
 assert.equal(result.baseline.orders.totals.shippedKg,9000);assert.equal(result.branch.orders.totals.shippedKg,9000);
 assert.equal(result.baseline.orders.orders[2].readyKg,6000);assert.equal(result.branch.orders.orders[2].readyKg,0);
 assert.equal(result.baseline.orders.orders[2].shippedKg,5000);assert.equal(result.branch.orders.orders[2].shippedKg,5000);
 assert.equal(JSON.stringify(s),before);assert.ok(result.originalUnchanged&&result.massConserved);
 conserved(result.baseline.orders);conserved(result.branch.orders);
 const legacy={...s};delete legacy.orders;const comparison=F.simulate(legacy,{stationId:'PACK-01',downtimeMinutes:10,horizonMinutes:5});assert.deepEqual(comparison.baseline.orders.orders,[]);
});
console.log(count+' order tests passed');
