/* Synthetic sales orders over the Lot ledger. Allocation never changes Lot mass. */
(function(root){
 'use strict';
 const stages=['intake','mixing','quality','packing','warehouse','shipped'];
 const assets=['TRK-01','TRK-02'];
 const scope='합성 판매주문·Lot 배분 데모. 납기는 가상 분이며 트럭 ID는 배분 연결만 표시합니다. 실제 고객·ERP·운송 경로·배송 완료 예측이 아닙니다. 창고 준비와 기존 출하 기록을 구분합니다.';
 const finite=n=>typeof n==='number'&&Number.isFinite(n);
 const text=(v,max=80)=>typeof v==='string'&&v.trim().length>0&&v.length<=max;
 const clone=value=>JSON.parse(JSON.stringify(value));
 const fields=['requestedKg','allocatedKg','unallocatedKg','shippedKg','readyKg','workInProgressKg','heldKg','shippedImpactKg','remainingKg','notReadyKg'];
 const empty=()=>Object.fromEntries(fields.map(key=>[key,0]));
 const hasOrders=state=>Object.prototype.hasOwnProperty.call(state,'orders');
 function validate(state){
  if(!state||typeof state!=='object')throw Error('주문 상태 형식을 확인하세요.');
  // Legacy Lot-only snapshots do not acquire orders during read or comparison.
  if(!hasOrders(state))return true;
  if(!Array.isArray(state.orders)||state.orders.length>1000||!Array.isArray(state.lots)||state.lots.length>1000)throw Error('주문·Lot 목록은 각각 최대 1000개입니다.');
  if(state.tick!==undefined&&(!finite(state.tick)||state.tick<0))throw Error('가상 시간은 0 이상의 유한 숫자여야 합니다.');
  const lots=new Map(),orderIds=new Set(),lineIds=new Set(),used=new Map();let lineCount=0,allocationCount=0;
  for(const lot of state.lots){
   if(!lot||!text(lot.id)||lots.has(lot.id)||!text(lot.product,200)||!finite(lot.quantity)||lot.quantity<=0||lot.quantity>1e9||!stages.includes(lot.stage))throw Error('Lot ID·제품·수량·단계를 확인하세요.');
   lots.set(lot.id,lot);
  }
  for(const order of state.orders){
   if(!order||!text(order.id)||orderIds.has(order.id)||!text(order.customer,200)||!finite(order.dueTick)||order.dueTick<0||!Array.isArray(order.lines)||!order.lines.length)throw Error('중복 주문 ID 또는 잘못된 주문 정보입니다.');
   orderIds.add(order.id);
   for(const line of order.lines){
    if(++lineCount>1000||!line||!text(line.id)||lineIds.has(line.id)||!text(line.product,200)||!finite(line.requestedKg)||line.requestedKg<=0||line.requestedKg>1e9||!Array.isArray(line.allocations))throw Error('중복 주문행 ID 또는 잘못된 제품·요청 수량입니다.');
    lineIds.add(line.id);const seen=new Set();let allocated=0;
    for(const allocation of line.allocations){
     if(++allocationCount>10000||!allocation||!text(allocation.lotId)||seen.has(allocation.lotId)||!finite(allocation.quantityKg)||allocation.quantityKg<=0||!assets.includes(allocation.assetId))throw Error('중복 Lot 배분 또는 잘못된 수량·트럭 ID입니다.');
     const lot=lots.get(allocation.lotId);
     if(!lot)throw Error('배분 Lot을 찾을 수 없습니다: '+allocation.lotId);
     if(lot.product!==line.product)throw Error('주문 제품과 Lot 제품이 다릅니다.');
     seen.add(lot.id);allocated+=allocation.quantityKg;used.set(lot.id,(used.get(lot.id)||0)+allocation.quantityKg);
     if(used.get(lot.id)>lot.quantity)throw Error('전체 주문의 Lot 배분 합계가 Lot 수량을 초과합니다.');
    }
    if(allocated>line.requestedKg)throw Error('주문행 배분 합계가 요청 수량을 초과합니다.');
   }
  }
  return true;
 }
 function seed(state){
  const groups=[
   ['육계 성장 사료',13000,[['LOT-001',4800],['LOT-003',3600],['LOT-009',4000]]],
   ['양돈 비육 사료',8200,[['LOT-002',4200],['LOT-005',4000]]],
   ['한우 비육 사료',11000,[['LOT-006',6000],['LOT-010',5000]]],
   ['산란계 사료',9700,[['LOT-004',5200],['LOT-007',4500]]],
   ['양돈 자돈 사료',3200,[['LOT-008',3200]]]
  ];
  const lots=new Map((state.lots||[]).map(l=>[l.id,l]));
  return groups.map(([product,requestedKg,links],i)=>{
   const id='SO-'+String(i+1).padStart(3,'0');
   return {id,customer:'합성 거래처 '+String.fromCharCode(65+i),dueTick:60,
    lines:[{id:id+'-L1',product,requestedKg,allocations:links.filter(([lotId])=>lots.has(lotId)&&lots.get(lotId).product===product).map(([lotId,quantity])=>({lotId,quantityKg:Math.min(quantity,lots.get(lotId).quantity),assetId:i%2?'TRK-02':'TRK-01'}))}]};
  });
 }
 function ensure(state){
  if(!hasOrders(state)){
   const orders=seed(state);validate({...state,orders});state.orders=orders;
  }else validate(state);
  return state.orders;
 }
 function condition(state,lot){
  const qc=lot.qc,moistureLimit=state.settings?.moistureLimit,storageLimit=state.settings?.storageLimit;
  const alerts=(state.alerts||[]).filter(a=>a.lotId===lot.id&&!a.resolved&&a.blocking),reasons=[],qcReasons=[];
  if(lot.status==='hold')reasons.push(lot.holdReason||'저장된 Lot 보류 상태');
  for(const alert of alerts)reasons.push(alert.title||'저장된 차단 경보');
  if(!qc)qcReasons.push('품질 측정 기록 확인 필요');
  else{
   if(qc.stale)qcReasons.push('측정 기록 신선도 확인 필요');
   if(!finite(qc.moisture))qcReasons.push('수분 측정값 확인 필요');
   if(!finite(qc.temperature))qcReasons.push('온도 측정값 확인 필요');
   if(!finite(moistureLimit))qcReasons.push('저장된 수분 기준 확인 필요');
   else if(finite(qc.moisture)&&qc.moisture>moistureLimit)qcReasons.push('수분 기록 '+qc.moisture+'% / 저장된 기준 '+moistureLimit+'% 초과');
   if(!finite(storageLimit))qcReasons.push('저장된 보관 온도 기준 확인 필요');
   else if(finite(qc.temperature)&&qc.temperature>storageLimit)qcReasons.push('온도 기록 '+qc.temperature+'°C / 저장된 기준 '+storageLimit+'°C 초과');
  }
  return {blocked:reasons.length>0||qcReasons.length>0,blockingReasons:[...new Set([...reasons,...qcReasons])],alertIds:alerts.map(a=>a.id),needsConfirmation:qcReasons.length>0&&!alerts.length};
 }
 function status(record){return record.remainingKg===0?'shipped':record.heldKg>0?'held':record.readyKg===record.remainingKg?'ready':record.workInProgressKg>0||record.shippedKg>0||record.readyKg>0?'in_progress':'unallocated';}
 function finish(record,dueTick,tick){
  record.unallocatedKg=Math.max(0,record.requestedKg-record.allocatedKg);
  record.remainingKg=Math.max(0,record.requestedKg-record.shippedKg);
  record.notReadyKg=Math.max(0,record.remainingKg-record.readyKg);
  record.dueTick=dueTick;record.overdue=tick>dueTick&&record.remainingKg>0;
  record.status=status(record);record.state=record.status;return record;
 }
 function snapshot(state,orderId){
  validate(state);const tick=state.tick||0,lots=new Map((state.lots||[]).map(l=>[l.id,l]));
  if(orderId!==undefined&&!(state.orders||[]).some(o=>o.id===orderId))throw Error('주문을 찾을 수 없습니다: '+orderId);
  const orders=(state.orders||[]).filter(o=>orderId===undefined||o.id===orderId).map(order=>{
   const lines=order.lines.map(line=>{
    const record={id:line.id,product:line.product,...empty(),requestedKg:line.requestedKg,allocations:[],linkedLotIds:[],linkedAssetIds:[]};
    for(const allocation of line.allocations){
     const lot=lots.get(allocation.lotId),current=condition(state,lot),isBlocked=current.blocked,shipped=lot.stage==='shipped',quantity=allocation.quantityKg;
     const disposition=shipped?'shipped':isBlocked?'held':lot.stage==='warehouse'?'ready':'work_in_progress';
     record.allocatedKg+=quantity;
     record[disposition==='work_in_progress'?'workInProgressKg':disposition+'Kg']+=quantity;
     const shippedImpact=shipped&&(isBlocked||lot.status==='warning');
     if(shippedImpact)record.shippedImpactKg+=quantity;
     record.allocations.push({...allocation,product:lot.product,lotQuantityKg:lot.quantity,stage:lot.stage,lotStatus:lot.status,status:disposition,state:disposition,...current,shippedImpact});
    }
    record.linkedLotIds=record.allocations.map(a=>a.lotId);record.linkedAssetIds=[...new Set(record.allocations.map(a=>a.assetId))];
    return finish(record,order.dueTick,tick);
   });
   const record={id:order.id,customer:order.customer,lines,...empty(),linkedLotIds:[],linkedAssetIds:[]};
   for(const line of lines)for(const field of fields)record[field]+=line[field];
   record.linkedLotIds=[...new Set(lines.flatMap(l=>l.linkedLotIds))];record.linkedAssetIds=[...new Set(lines.flatMap(l=>l.linkedAssetIds))];
   return finish(record,order.dueTick,tick);
  });
  const totals=empty();for(const order of orders)for(const field of fields)totals[field]+=order[field];
  return {synthetic:true,scope,tick,orders,totals};
 }
 function setAllocation(state,input){
  try{
   validate(state);
   if(!input||typeof input!=='object'||Array.isArray(input)||Object.keys(input).some(k=>!['orderId','lineId','lotId','quantityKg','assetId'].includes(k))||!text(input.orderId)||!text(input.lineId)||!text(input.lotId)||!finite(input.quantityKg)||input.quantityKg<0||!assets.includes(input.assetId))throw Error('주문·행·Lot·수량·트럭 입력을 확인하세요.');
   const orders=clone(state.orders||[]),order=orders.find(o=>o.id===input.orderId),line=order?.lines.find(l=>l.id===input.lineId),lot=state.lots.find(l=>l.id===input.lotId);
   if(!line)throw Error('주문행을 찾을 수 없습니다.');
   if(!lot)throw Error('Lot을 찾을 수 없습니다.');
   if(lot.product!==line.product)throw Error('주문 제품과 Lot 제품이 다릅니다.');
   if(lot.stage==='shipped')throw Error('이미 출하된 Lot의 배분은 편집할 수 없습니다.');
   const index=line.allocations.findIndex(a=>a.lotId===input.lotId);
   if(input.quantityKg===0){if(index>=0)line.allocations.splice(index,1);}
   else{
    const allocation={lotId:input.lotId,quantityKg:input.quantityKg,assetId:input.assetId};
    if(index>=0)line.allocations[index]=allocation;else line.allocations.push(allocation);
   }
   validate({...state,orders});
   state.orders=orders;
   if(!Array.isArray(state.events))state.events=[];
   if(!Number.isSafeInteger(state.nextEventId)||state.nextEventId<1)state.nextEventId=state.events.reduce((n,e)=>Math.max(n,Number(String(e.id).replace(/^EVT-/,''))||0),0)+1;
   state.events.push({id:'EVT-'+state.nextEventId++,tick:state.tick||0,kind:'order_allocation',lotId:input.lotId,orderId:input.orderId,lineId:input.lineId,message:'합성 주문 배분: '+input.quantityKg+' kg · '+input.assetId});
   if(state.events.length>500)state.events.splice(0,state.events.length-500);
   return {ok:true};
  }catch(error){return {ok:false,error:error.message};}
 }
 const api={scope,assets:[...assets],seed,ensure,validate,snapshot,setAllocation};
 if(root)root.GrainOrders=api;if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
