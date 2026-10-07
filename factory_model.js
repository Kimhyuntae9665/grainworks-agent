/* Independent synthetic capacity model. It never calls or replaces FeedEngine.step. */
(function(root){
 'use strict';
 const Orders=typeof module!=='undefined'&&module.exports?require('./order_model.js'):root.GrainOrders;
 const stages=['intake','mixing','quality','packing','warehouse','shipped'];
 const definitions=[
  {id:'REC-01',title:'원료 입고 설비',stage:'intake',capacityKgPerMinute:1200},
  {id:'MIX-01',title:'사료 배합기',stage:'mixing',capacityKgPerMinute:600},
  {id:'QC-01',title:'품질 검사 설비',stage:'quality',capacityKgPerMinute:1000},
  {id:'PACK-01',title:'완제품 포장기',stage:'packing',capacityKgPerMinute:1000}
 ];
 const scope='독립 합성 용량 모델: 설비별 단일 처리 FIFO, 저장된 로트 진행률 반영. 저장된 보류/차단 로트는 이동 없이 제외하며 다른 로트는 처리 가능. 포장 완료 시 창고에 적재; 창고와 기존 출하 로트는 이동하지 않음. 신규 입고·손실·새 품질 판정·재검사·해제·실제 설비/센서 연결 없음. 실제 납기/출하 예측 없음. 가상 주문 마감과 포장 준비 물량만 비교. 기존 로트 엔진 시간 진행과 별개.';
 const finite=n=>typeof n==='number'&&Number.isFinite(n);
 const clone=value=>JSON.parse(JSON.stringify(value));
 const sum=lots=>lots.reduce((n,l)=>n+l.quantity,0);
 function validate(state){
  if(!state||!Array.isArray(state.lots)||state.lots.length>1000)throw Error('Factory state requires at most 1000 lots');
  const ids=new Set();
  for(const lot of state.lots){
   if(!lot||typeof lot.id!=='string'||!lot.id||ids.has(lot.id)||!stages.includes(lot.stage)||!finite(lot.quantity)||lot.quantity<=0||!finite(lot.progress)||lot.progress<0||lot.progress>1)throw Error('Invalid factory lot');
   ids.add(lot.id);
  }
  if(state.factory!==undefined&&(!state.factory||typeof state.factory!=='object'||Array.isArray(state.factory)))throw Error('Invalid factory configuration');
  Orders.validate(state);
 }
 function equipment(state){
  const configured=state.factory?.stations||{};
  if(!configured||typeof configured!=='object'||Array.isArray(configured)||Object.keys(configured).some(id=>!definitions.some(d=>d.id===id)))throw Error('Factory stations must use known station IDs');
  return definitions.map(d=>{
   const config=configured[d.id]||{};
   if(typeof config!=='object'||Array.isArray(config)||Object.keys(config).some(k=>!['capacityKgPerMinute','status'].includes(k)))throw Error('Invalid station configuration');
   const capacity=config.capacityKgPerMinute??d.capacityKgPerMinute;
   if(!finite(capacity)||capacity<=0||capacity>1000000)throw Error('Capacity must be a finite number >0 and <=1000000 kg/min');
   if(config.status!==undefined&&!['idle','running','hold','down'].includes(config.status))throw Error('Invalid station status');
   return {...d,capacityKgPerMinute:capacity,disabled:config.status==='down',paused:config.status==='hold'};
  });
 }
 function blocked(state,lot){return lot.status==='hold'||(state.alerts||[]).some(a=>a.lotId===lot.id&&!a.resolved&&a.blocking);}
 function snapshot(state){
  validate(state);
  return {synthetic:true,scope,stations:equipment(state).map(d=>{
   const lots=state.lots.filter(l=>l.stage===d.stage),held=lots.filter(l=>blocked(state,l));
   const active=!d.disabled&&!d.paused?lots.find(l=>!blocked(state,l)):null;
   return {id:d.id,title:d.title,stage:d.stage,capacityKgPerMinute:d.capacityKgPerMinute,
    queuedKg:sum(lots.filter(l=>l!==active)),workloadKg:sum(lots),heldKg:sum(held),
    activeLotId:active?.id||null,status:d.disabled?'down':d.paused?'hold':active?'running':held.length?'hold':'idle',
    affectedLotIds:lots.map(l=>l.id)};
  })};
 }
 function run(source,equipmentConfig,horizon,downtimeId,downtime){
  const state=clone(source),queues=equipmentConfig.map(d=>state.lots.filter(l=>l.stage===d.stage&&!blocked(state,l)));
  const active=equipmentConfig.map(()=>null),throughputByStage=Object.fromEntries(stages.map(s=>[s,0]));
  let time=0;
  function start(){equipmentConfig.forEach((d,i)=>{
   if(active[i]||d.disabled||d.paused||(d.id===downtimeId&&time<downtime)||!queues[i].length)return;
   const lot=queues[i].shift();active[i]={lot,remaining:lot.quantity*(1-lot.progress)/d.capacityKgPerMinute};
  });}
  // At most four completions per lot plus the single downtime boundary.
  for(let events=0;events<=state.lots.length*4+2;events++){
   start();
   let dt=Math.min(...active.map(a=>a?a.remaining:Infinity));
   if(time<downtime&&downtimeId)dt=Math.min(dt,downtime-time);
   if(!Number.isFinite(dt)||time+dt>horizon){
    const left=horizon-time;
    active.forEach((a,i)=>{if(a){a.remaining-=left;a.lot.progress=Math.min(1,1-a.remaining*equipmentConfig[i].capacityKgPerMinute/a.lot.quantity);}});
    time=horizon;break;
   }
   time+=dt;active.forEach((a,i)=>{if(a){a.remaining-=dt;a.lot.progress=Math.min(1,1-a.remaining*equipmentConfig[i].capacityKgPerMinute/a.lot.quantity);}});
   const finished=active.map((a,i)=>a&&a.remaining<=1e-9?i:-1).filter(i=>i>=0);
   for(const i of finished){
    const lot=active[i].lot;throughputByStage[lot.stage]+=lot.quantity;
    lot.stage=stages[stages.indexOf(lot.stage)+1];lot.progress=0;active[i]=null;
    if(i<3)queues[i+1].push(lot);
   }
   if(time>=horizon)break;
  }
  const backlogByStage=Object.fromEntries(stages.map(s=>[s,sum(state.lots.filter(l=>l.stage===s))]));
  state.tick=(source.tick||0)+horizon;
  return {throughputKg:throughputByStage.packing,throughputByStage,backlogByStage,
   totalKg:sum(state.lots),heldKg:sum(state.lots.filter(l=>l.stage!=='shipped'&&blocked(state,l))),
   shippedImpactKg:sum(state.lots.filter(l=>l.stage==='shipped'&&blocked(state,l))),
   lots:state.lots.map(l=>({id:l.id,quantity:l.quantity,stage:l.stage,progress:l.progress,status:l.status})),
   orders:Orders.snapshot(state),
   massConserved:Math.abs(sum(state.lots)-sum(source.lots))<=Math.max(1e-7,sum(source.lots)*1e-12)};
 }
 function simulate(state,options){
  validate(state);
  if(!options||typeof options!=='object'||Array.isArray(options)||Object.keys(options).some(k=>!['stationId','downtimeMinutes','horizonMinutes'].includes(k)))throw Error('Explicit stationId, downtimeMinutes and horizonMinutes required');
  const {stationId,downtimeMinutes,horizonMinutes}=options;
  if(!definitions.some(d=>d.id===stationId))throw Error('Unknown station ID');
  if(!finite(downtimeMinutes)||downtimeMinutes<0||downtimeMinutes>240)throw Error('Downtime minutes must be finite 0..240');
  if(!finite(horizonMinutes)||horizonMinutes<1||horizonMinutes>240)throw Error('Horizon minutes must be finite 1..240');
  const original=JSON.stringify(state),config=equipment(state);
  const baseline=run(state,config,horizonMinutes,null,0),branch=run(state,config,horizonMinutes,stationId,downtimeMinutes);
  const delta={throughputKg:branch.throughputKg-baseline.throughputKg,
   backlogByStage:Object.fromEntries(stages.map(s=>[s,branch.backlogByStage[s]-baseline.backlogByStage[s]]))};
  return {synthetic:true,stationId,downtimeMinutes,horizonMinutes,scope,limitations:scope,
   assumptions:{queue:'FIFO arrival then original state order; held lots skipped without release',initialProgress:'saved progress',
    downtimeStartMinute:0,effectiveDowntimeMinutes:Math.min(downtimeMinutes,horizonMinutes),
    capacities:config.map(d=>({stationId:d.id,capacityKgPerMinute:d.capacityKgPerMinute,status:d.disabled?'down':d.paused?'hold':'available'})),
    throughputDefinition:'whole-lot packing completions to warehouse, not shipment',newArrivals:false},
   baseline,branch,delta,originalUnchanged:JSON.stringify(state)===original,massConserved:baseline.massConserved&&branch.massConserved};
 }
 const api={definitions:clone(definitions),stages:[...stages],scope,snapshot,simulate};
 root.GrainFactory=api;if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
