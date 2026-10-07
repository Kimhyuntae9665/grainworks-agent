/* Read-only logistics views over the existing synthetic Lot ledger. */
(function(root){
 'use strict';
 const definitions=[
  {id:'TRK-01',kind:'truck',name:'출하 트럭 01',stage:'shipped',location:'출하 도크 01',capacity:12000,destination:'데모 거래처 A'},
  {id:'TRK-02',kind:'truck',name:'출하 트럭 02',stage:'shipped',location:'출하 도크 02',capacity:12000,destination:'데모 거래처 B'},
  {id:'FL-01',kind:'forklift',name:'전동 지게차 01',stage:'packing',location:'포장 → 출하 이송로'},
  {id:'CNT-01',kind:'container',name:'원료 컨테이너',stage:'intake',location:'원료 반입 구역',capacity:24000},
  {id:'CNT-02',kind:'container',name:'출고 컨테이너',stage:'warehouse',location:'출고 대기 구역',capacity:24000},
  {id:'WH-01',kind:'warehouse',name:'완제품 창고',stage:'warehouse',location:'보관 구역 A',capacity:18000}
 ];
 const hash=id=>[...id].reduce((n,c)=>n+c.charCodeAt(0),0)%2;
 const sum=lots=>lots.reduce((n,l)=>n+l.quantity,0);
 function snapshot(state,id,runtime={}){
  const definition=definitions.find(a=>a.id===id);if(!definition)return null;
  let lots=state.lots.filter(l=>l.stage===(id==='CNT-01'?'intake':'warehouse'));
  if(definition.kind==='truck')lots=lots.filter(l=>hash(l.id)===(id==='TRK-01'?0:1));
  if(definition.kind==='forklift')lots=state.lots.filter(l=>['packing','warehouse'].includes(l.stage));
  const ids=new Set(lots.map(l=>l.id));
  const alerts=state.alerts.filter(a=>!a.resolved&&ids.has(a.lotId));
  const blocked=lots.filter(l=>l.status==='hold'||alerts.some(a=>a.lotId===l.id)||l.qc.stale||l.qc.moisture===null||l.qc.temperature===null||l.qc.moisture>state.settings.moistureLimit||l.qc.temperature>state.settings.storageLimit);
  const blockedIds=new Set(blocked.map(l=>l.id));const ready=lots.filter(l=>!blockedIds.has(l.id));
  const quantity=sum(lots),heldKg=sum(blocked),temperatureLots=lots.filter(l=>Number.isFinite(l.qc.temperature));
  const temperature=temperatureLots.length?temperatureLots.reduce((n,l)=>n+l.qc.temperature*l.quantity,0)/sum(temperatureLots):null;
  const overCapacity=!!definition.capacity&&quantity>definition.capacity;
  let status=blocked.length?'검수 보류':overCapacity?'기준 물량 초과':lots.length?'작업 대기':'배정 대기',tone=blocked.length||overCapacity?'warning':'idle';
  let moving=false;
  if(definition.kind==='truck'&&!blocked.length&&!overCapacity&&lots.length){status='상차 대기';tone='ready';}
  if(definition.kind==='container'&&!blocked.length&&!overCapacity&&lots.length){status=id==='CNT-01'?'입고 대기':'출고 대기';tone='ready';}
  if(definition.kind==='warehouse'&&!blocked.length&&!overCapacity){status=lots.length?'정상 보관':'비어 있음';tone=lots.length?'ready':'idle';}
  if(definition.kind==='forklift'){
   moving=state.running&&ready.length>0;
   status=ready.length?(moving?'이송 중':'일시정지'):blocked.length?'작업 보류':'작업 대기';
   tone=ready.length?(moving?'moving':'idle'):blocked.length?'warning':'idle';
  }
  return {...definition,lots,quantity,readyKg:sum(ready),heldKg,blockedCount:blocked.length,alertCount:alerts.length,temperature,overCapacity,status,tone,moving,
   occupancy:definition.capacity?quantity/definition.capacity:null,
   battery:definition.kind==='forklift'?Math.max(25,95-state.tick*.28):null,
   speed:definition.kind==='forklift'&&moving?Math.max(0,Number(runtime.speed)||0):0,
   job:ready[0]||null,clock:state.tick};
 }
 const api={definitions,snapshot,list:(state,runtime)=>definitions.map(a=>snapshot(state,a.id,runtime))};
 root.FeedAssets=api;if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
