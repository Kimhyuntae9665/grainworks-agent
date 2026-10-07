'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const E=require('./engine.js'),A=require('./assets.js');
test('truck manifests partition warehouse lots without changing inventory',()=>{
 const s=E.createState(),before=JSON.stringify(s),one=A.snapshot(s,'TRK-01'),two=A.snapshot(s,'TRK-02'),warehouse=A.snapshot(s,'WH-01');
 assert.equal(one.quantity+two.quantity,warehouse.quantity);assert.equal(warehouse.quantity,7700);
 const ids=[...one.lots,...two.lots].map(l=>l.id);assert.equal(new Set(ids).size,ids.length);
 A.list(s);assert.equal(JSON.stringify(s),before);
});
test('raw moisture exception updates container and keeps shipped effects separate',()=>{
 const s=E.createState();E.inject(s,'moisture');const raw=A.snapshot(s,'CNT-01');
 assert.equal(raw.status,'검수 보류');assert.equal(raw.heldKg,4800);assert.equal(raw.alertCount,1);
 assert.equal(A.snapshot(s,'WH-01').heldKg,0);assert.equal(E.report(s).heldKg,8400);
});
test('warehouse hold propagates to the assigned truck and outbound view',()=>{
 const s=E.createState();E.hold(s,'LOT-007','시연 보류');const truck=A.list(s).find(a=>a.kind==='truck'&&a.lots.some(l=>l.id==='LOT-007'));
 assert.equal(truck.status,'검수 보류');assert.equal(truck.heldKg,4500);
 assert.equal(A.snapshot(s,'WH-01').heldKg,4500);assert.equal(A.snapshot(s,'CNT-02').heldKg,4500);
});
test('forklift waits, runs, and stops when every transfer lot is blocked',()=>{
 const s=E.createState();s.running=false;assert.equal(A.snapshot(s,'FL-01').status,'일시정지');
 s.running=true;assert.equal(A.snapshot(s,'FL-01',{speed:2.5}).speed,2.5);assert.ok(A.snapshot(s,'FL-01').moving);
 for(const l of s.lots.filter(l=>['packing','warehouse'].includes(l.stage)))E.hold(s,l.id,'이송 보류');
 const a=A.snapshot(s,'FL-01',{speed:2.5});assert.equal(a.status,'작업 보류');assert.equal(a.moving,false);assert.equal(a.speed,0);
});
test('empty, oversized, missing sensor and long runtime states are explicit',()=>{
 const s=E.createState();s.lots=s.lots.filter(l=>l.stage!=='warehouse');const empty=A.snapshot(s,'WH-01');assert.equal(empty.temperature,null);assert.equal(empty.status,'비어 있음');
 const source=E.createState().lots.find(l=>l.stage==='warehouse');s.lots.push({...source,quantity:20000});assert.equal(A.snapshot(s,'WH-01').overCapacity,true);
 s.lots.at(-1).qc={...source.qc,temperature:null};assert.equal(A.snapshot(s,'WH-01').blockedCount,1);
 s.tick=10000;assert.equal(A.snapshot(s,'FL-01').battery,25);assert.equal(A.snapshot(s,'unknown'),null);
});
