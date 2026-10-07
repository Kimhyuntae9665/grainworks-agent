'use strict';
const assert=require('node:assert/strict'),F=require('./factory_model.js'),E=require('./engine.js');
let count=0;function test(name,fn){fn();count++;console.log('PASS '+name);}
function lot(id,stage='mixing',quantity=600,progress=0,status='ok'){return {id,stage,quantity,progress,status};}
function state(lots){return {lots,alerts:[]};}
function compare(s,options={}){return F.simulate(s,{stationId:'MIX-01',downtimeMinutes:0,horizonMinutes:2,...options});}
test('FIFO contention consumes a single station capacity, not per-lot parallelism',()=>{
 const s=state([lot('A'),lot('B'),lot('C')]),before=JSON.stringify(s),r=compare(s,{horizonMinutes:1.5});
 assert.equal(r.baseline.throughputByStage.mixing,600);assert.equal(r.baseline.lots[1].progress,.5);
 assert.equal(r.baseline.lots[2].progress,0);assert.equal(r.baseline.backlogByStage.mixing,1200);
 assert.equal(JSON.stringify(s),before);assert.deepEqual(r.baseline,r.branch);
});
test('downstream queues retain existing arrivals before upstream completions',()=>{
 const s=state([lot('M'),lot('Q','quality',1000)]),r=compare(s,{horizonMinutes:1.2});
 assert.equal(r.baseline.throughputByStage.quality,1000);assert.equal(r.baseline.lots[0].stage,'quality');
 assert.ok(Math.abs(r.baseline.lots[0].progress-1/3)<1e-8);
});
test('stored progress reduces remaining service without reducing full-lot mass',()=>{
 const r=compare(state([lot('A','mixing',600,.5)]),{horizonMinutes:1});
 assert.equal(r.baseline.throughputByStage.mixing,600);assert.equal(r.baseline.backlogByStage.quality,600);
 assert.equal(r.baseline.totalKg,600);assert.ok(r.massConserved);
});
test('a completion exactly at horizon preserves other station partial progress',()=>{
 const s=state([lot('A','packing',1000),lot('B','mixing',1200)]);
 const r=compare(s,{stationId:'REC-01',downtimeMinutes:0,horizonMinutes:1});
 for(const branch of [r.baseline,r.branch]){
  assert.equal(branch.lots[0].stage,'warehouse');assert.equal(branch.throughputKg,1000);
  assert.equal(branch.lots[1].stage,'mixing');assert.equal(branch.lots[1].progress,.5);
  assert.equal(branch.backlogByStage.mixing,1200);assert.equal(branch.totalKg,2200);
 }
 assert.ok(r.massConserved&&r.originalUnchanged);
});
test('holds and blocking alerts never release, other eligible lots proceed',()=>{
 const s=state([lot('H','mixing',600,.7,'hold'),lot('B'),lot('C')]);s.alerts=[{lotId:'B',blocking:true,resolved:false}];
 const r=compare(s,{horizonMinutes:20}),snap=F.snapshot(s).stations[1];
 assert.equal(snap.activeLotId,'C');assert.equal(snap.heldKg,1200);assert.equal(snap.queuedKg,1200);
 assert.equal(r.branch.lots[0].stage,'mixing');assert.equal(r.branch.lots[0].progress,.7);
 assert.equal(r.branch.lots[1].stage,'mixing');assert.equal(r.branch.throughputKg,600);
});
test('downtime is clipped to horizon and longer horizon can catch up',()=>{
 const s=state([lot('P','packing',1000)]);
 const short=compare(s,{stationId:'PACK-01',downtimeMinutes:5,horizonMinutes:2});
 assert.equal(short.baseline.throughputKg,1000);assert.equal(short.branch.throughputKg,0);
 assert.equal(short.assumptions.effectiveDowntimeMinutes,2);assert.equal(short.delta.throughputKg,-1000);
 const long=compare(s,{stationId:'PACK-01',downtimeMinutes:5,horizonMinutes:6});
 assert.equal(long.branch.throughputKg,1000);
});
test('warehouse and shipped records stay in place; reports conserve mass by stage',()=>{
 const s=state([lot('W','warehouse',500),lot('S','shipped',200,1),lot('P','packing',1000)]);
 const r=compare(s,{horizonMinutes:20});
 for(const branch of [r.baseline,r.branch]){
  assert.equal(branch.totalKg,1700);assert.equal(Object.values(branch.backlogByStage).reduce((n,k)=>n+k,0),1700);
  assert.deepEqual(branch.lots.slice(0,2),s.lots.slice(0,2));assert.equal(branch.backlogByStage.shipped,200);
 }
 assert.ok(r.originalUnchanged&&r.massConserved);
});
test('station configuration is bounded and down or hold disables processing',()=>{
 const s=state([lot('P','packing',1000)]);s.factory={stations:{'PACK-01':{capacityKgPerMinute:500,status:'down'}}};
 assert.equal(F.snapshot(s).stations[3].status,'down');assert.equal(compare(s).baseline.throughputKg,0);
 s.factory.stations['PACK-01'].status='hold';assert.equal(F.snapshot(s).stations[3].status,'hold');
 assert.equal(compare(s).baseline.throughputKg,0);
 s.factory.stations['PACK-01'].status='idle';assert.equal(compare(s).baseline.throughputKg,1000);
});
test('validation rejects invalid inputs instead of silently using default assumptions',()=>{
 const s=state([lot('A')]);
 for(const o of [{stationId:'invented'},{downtimeMinutes:'5'},{downtimeMinutes:NaN},{downtimeMinutes:-1},{horizonMinutes:0},{horizonMinutes:241},{horizonMinutes:Infinity},{downtimeMinutes:true},{extra:1}])assert.throws(()=>compare(s,o));
 assert.throws(()=>F.simulate(s,{stationId:'MIX-01'}));
 assert.throws(()=>F.snapshot(state([lot('A'),lot('A')])));
 assert.throws(()=>F.snapshot({...s,factory:{stations:{'MIX-01':{capacityKgPerMinute:0}}}}));
 assert.throws(()=>F.snapshot({...s,factory:{stations:{'X':{}}}}));
});
test('seeded engine ledger integrates and repeated comparisons are deterministic',()=>{
 const s=E.createState(),before=JSON.stringify(s),options={stationId:'MIX-01',downtimeMinutes:10,horizonMinutes:15};
 const first=F.simulate(s,options);assert.deepEqual(F.simulate(s,options),first);assert.equal(JSON.stringify(s),before);
 assert.deepEqual(F.snapshot(s).stations.map(s=>s.id),['REC-01','MIX-01','QC-01','PACK-01']);
 assert.ok(first.massConserved&&first.originalUnchanged);
});
test('already shipped warning impact is separate from held production mass',()=>{
 const s=E.createState();E.inject(s,'moisture');const r=compare(s,{horizonMinutes:20});
 assert.equal(r.baseline.heldKg,8400);assert.equal(r.baseline.shippedImpactKg,4000);
 assert.equal(r.branch.heldKg,8400);assert.equal(r.branch.shippedImpactKg,4000);
 assert.equal(r.branch.backlogByStage.shipped,9000);
});
test('capacity comparison projects order readiness without simulating shipping or changing source',()=>{
 const s=E.createState(),before=JSON.stringify(s);
 const r=compare(s,{stationId:'PACK-01',downtimeMinutes:10,horizonMinutes:5});
 assert.equal(r.baseline.orders.orders[2].readyKg,6000);assert.equal(r.branch.orders.orders[2].readyKg,0);
 assert.equal(r.baseline.orders.totals.shippedKg,9000);assert.equal(r.branch.orders.totals.shippedKg,9000);
 assert.equal(r.baseline.orders.tick,5);assert.equal(JSON.stringify(s),before);
});
console.log(count+' factory tests passed');
