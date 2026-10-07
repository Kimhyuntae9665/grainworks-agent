/* Orders stay beside the live factory. Allocation is an explicit manual action. */
const G=window.Grainworks,O=window.GrainOrders;
const byId=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const kg=v=>Number(v||0).toLocaleString('ko-KR',{maximumFractionDigits:1});
const world=byId('world');
const panel=document.createElement('section');panel.id='order-panel';panel.className='order-panel';panel.setAttribute('aria-labelledby','order-heading');
panel.innerHTML=`<header class="order-header"><div><span class="eyebrow">ORDER / FACTORY</span><h3 id="order-heading">주문 관리</h3></div><button type="button" id="order-collapse" aria-expanded="true" aria-controls="order-content" aria-label="주문 패널 접기">−</button></header><div id="order-content"><div class="order-list" id="order-list" aria-label="주문 목록"></div><div class="order-detail" id="order-detail" hidden></div><p class="order-note">주문·납기는 합성 가정입니다. 준비 물량은 창고의 정상 Lot이며 출하 완료와 구분합니다.</p></div><p id="order-live" class="order-live" role="status" aria-live="polite"></p>`;
world.append(panel);
const narrow=window.matchMedia("(max-width:800px)");
function dock(){if(narrow.matches)world.after(panel);else world.append(panel);}
narrow.addEventListener("change",dock);dock();
let selected=null,signature='',listSignature='',editing=false;
function report(){return G.orderSnapshot();}
function orders(){const r=report();return Array.isArray(r)?r:r.orders||[];}
function due(o,state){const delta=Number(o.dueTick)-state.tick;return Number(o.remainingKg)<=0?'출하 완료':delta<0?`기한 ${kg(-delta)}분 경과`:`기한까지 ${kg(delta)}분`;}
function allocationLabel(a,l){if((a.stage||l?.stage)==='shipped')return a.shippedImpact||l?.status==='warning'?'출하 영향 확인':'출하 완료';if(a.needsConfirmation)return '품질 기록 확인 필요';if(a.blocked)return '품질 보류';return (a.stage||l?.stage)==='warehouse'?'창고 준비':'생산·검사 대기';}
function metrics(o){return `<dl class="order-metrics">${[['요청','requestedKg'],['출하','shippedKg'],['보류','heldKg'],['창고 준비','readyKg'],['미배정','unallocatedKg']].map(([label,key])=>`<div data-metric="${key}"><dt>${label}</dt><dd>${kg(o[key])}<small> kg</small></dd></div>`).join('')}</dl>`;}
function progress(o){const requested=Number(o.requestedKg)||1;return `<div class="order-progress" aria-hidden="true">${[['shipped','shippedKg'],['ready','readyKg'],['held','heldKg'],['pending','workInProgressKg']].map(([cls,key])=>`<i class="${cls}" style="width:${Math.min(100,Math.max(0,Number(o[key]||0)/requested*100))}%"></i>`).join('')}</div>`;}
function render(){
 const state=G.snapshot(),all=orders();if(selected&&!all.some(o=>o.id===selected))selected=null;
 const listHtml=all.map(o=>`<button type="button" class="order-row" data-order="${esc(o.id)}" aria-pressed="${selected===o.id}"><span class="order-row-heading"><strong>${esc(o.id)}</strong><span>${esc(due(o,state))}</span></span><small>${esc(o.customer)} · 가상 ${kg(o.dueTick)}분</small>${progress(o)}${metrics(o)}</button>`).join('')||'<p class="order-empty">주문이 없습니다. 호환되는 샘플을 초기화하거나 주문 데이터를 확인하세요.</p>';
 if(listHtml!==listSignature){const active=document.activeElement,focusId=active?.dataset.order;byId('order-list').innerHTML=listHtml;listSignature=listHtml;if(focusId)[...byId('order-list').querySelectorAll('[data-order]')].find(b=>b.dataset.order===focusId)?.focus({preventScroll:true});}
 const order=all.find(o=>o.id===selected);byId('order-list').hidden=!!order;byId('order-detail').hidden=!order;
 markScene(order,state);
 if(!order){signature='';return;}
 const liveDue=byId('order-detail').querySelector('.order-selected-title span');if(liveDue)liveDue.textContent=due(order,state);
 for(const metric of byId('order-detail').querySelectorAll('.order-metrics [data-metric] dd')){const value=order[metric.parentElement.dataset.metric];metric.innerHTML=kg(value)+'<small> kg</small>';}
 const sig=JSON.stringify([order,state.alerts]);if(sig===signature||editing||byId('order-allocation-form')?.contains(document.activeElement))return;signature=sig;
 const links=(order.linkedLotIds||order.lines.flatMap(l=>l.allocations.map(a=>a.lotId)));
 const alerts=state.alerts.filter(a=>!a.resolved&&links.includes(a.lotId));
 byId('order-detail').innerHTML=`<button type="button" class="order-back" id="order-back">← 주문 목록</button><div class="order-selected-title"><strong>${esc(order.id)}</strong><span>${esc(due(order,state))}</span></div><p class="order-customer">${esc(order.customer)} · 가상 ${kg(order.dueTick)}분 납기</p>${metrics(order)}${Number(order.shippedImpactKg)>0?`<p class="order-impact">이미 출하된 ${kg(order.shippedImpactKg)} kg에 영향이 있습니다. 미출하 보류와 별도로 확인하세요.</p>`:''}<p class="order-stage-note">생산·검사 대기 ${kg(order.workInProgressKg)} kg · 미출하 잔량 ${kg(order.remainingKg)} kg</p>${order.lines.map(line=>`<section class="order-line"><h4>${esc(line.product)} <small>요청 ${kg(line.requestedKg)} kg</small></h4>${line.allocations.map(a=>{const l=state.lots.find(l=>l.id===a.lotId),blocked=state.alerts.filter(alert=>alert.lotId===a.lotId&&!alert.resolved);if(a.blocked&&!blocked.length)blocked.push({title:a.needsConfirmation?'기록 확인 필요 · 저장 경보 없음':'품질 보류 근거',details:(a.blockingReasons||[]).join(' · ')||'현재 Lot 품질 기록을 확인하세요.'});return `<article class="order-allocation"><div><button type="button" data-order-lot="${esc(a.lotId)}">${esc(a.lotId)} ↗</button><strong>${kg(a.quantityKg)} kg</strong></div><p>${esc(window.FeedEngine.LABELS[l?.stage]||'Lot 없음')} · ${allocationLabel(a,l)}</p>${a.assetId?`<button type="button" class="order-truck" data-order-asset="${esc(a.assetId)}">${esc(a.assetId)} 출하 계획 ↗</button>`:'<small>트럭 미배정</small>'}${blocked.map(alert=>`<p class="order-cause">${esc(alert.title)} · ${esc(alert.details)}</p>`).join('')}${l&&l.stage!=='shipped'?`<button type="button" class="order-edit" data-order-edit="${esc(line.id)}" data-edit-lot="${esc(a.lotId)}">배정 수정</button>`:'<small class="order-history">출하 이력 · 수정 불가</small>'}</article>`;}).join('')||'<p class="order-empty">배정된 Lot이 없습니다.</p>'}<button type="button" class="order-add" data-order-edit="${esc(line.id)}">호환 Lot 배정</button></section>`).join('')}<div id="order-editor"></div>${alerts.length?`<details class="order-alerts"><summary>미해결 알림 ${alerts.length}건</summary>${alerts.map(a=>`<p>${esc(a.lotId)} · ${esc(a.title)}<br>${esc(a.details)}</p>`).join('')}</details>`:''}<button type="button" id="order-agent" class="order-ask">이 주문을 AI에 질문 ↗</button>`;
}
function markScene(order,state){
 const ids=new Set(order?.linkedLotIds||order?.lines.flatMap(l=>l.allocations.map(a=>a.lotId))||[]),stages=new Set(state.lots.filter(l=>ids.has(l.id)).map(l=>l.stage));
 document.querySelectorAll('[data-stage]').forEach(el=>el.classList.toggle('order-linked',stages.has(el.dataset.stage)));
 document.querySelectorAll('[data-lot]').forEach(el=>el.classList.toggle('order-linked-lot',ids.has(el.dataset.lot)));
 const assets=new Set(order?.linkedAssetIds||order?.lines.flatMap(l=>l.allocations.map(a=>a.assetId))||[]);
 document.querySelectorAll('.asset-tag').forEach(el=>el.classList.toggle('order-linked',assets.has(el.dataset.asset)));
}
function openEditor(lineId,lotId){
 const state=G.snapshot(),order=orders().find(o=>o.id===selected),line=order?.lines.find(l=>l.id===lineId);if(!line)return;
 const allocation=line.allocations.find(a=>a.lotId===lotId);
 const candidates=state.lots.filter(l=>l.product===line.product&&l.stage!=='shipped').filter(l=>{
  const used=state.orders.flatMap(o=>o.lines.flatMap(x=>x.allocations)).filter(a=>a.lotId===l.id).reduce((n,a)=>n+a.quantityKg,0);
  return l.quantity-used+(allocation?.lotId===l.id?allocation.quantityKg:0)>0||l.id===lotId;
 });
 if(!candidates.length){byId('order-live').textContent='배정 가능한 같은 제품의 미출하 Lot이 없습니다.';return;}
 editing=true;byId('order-editor').innerHTML=`<form id="order-allocation-form" data-line="${esc(lineId)}"><h4>${allocation?'배정 수정':'호환 Lot 배정'} · 수동 조작</h4><label for="order-edit-lot">생산 Lot</label><select id="order-edit-lot" ${allocation?'disabled':''}>${candidates.map(l=>`<option value="${esc(l.id)}" ${l.id===lotId?'selected':''}>${esc(l.id)} · ${kg(l.quantity)} kg</option>`).join('')}</select><label for="order-edit-quantity">배정 수량 (kg) · 0 입력 시 해제</label><input id="order-edit-quantity" type="number" min="${allocation?0:0.1}" step="0.1" required value="${allocation?.quantityKg??''}"><label for="order-edit-truck">출하 계획 트럭</label><select id="order-edit-truck" required><option value="" disabled ${allocation?'':'selected'}>트럭 선택</option>${window.FeedAssets.definitions.filter(a=>a.kind==='truck').map(a=>`<option value="${esc(a.id)}" ${a.id===allocation?.assetId?'selected':''}>${esc(a.id)} · ${esc(a.name)}</option>`).join('')}</select><p>같은 제품·미출하 Lot만 배정합니다. 다른 주문과 합산한 수량이 Lot 수량을 넘을 수 없습니다.</p><p id="order-edit-error" role="alert"></p><div><button type="submit">배정 저장</button><button type="button" id="order-edit-cancel">취소</button></div></form>`;
 byId('order-edit-quantity').focus({preventScroll:true});byId('order-editor').scrollIntoView({block:'nearest'});
}
panel.addEventListener('click',e=>{
 const b=e.target.closest('button');if(!b)return;
 if(b.dataset.order){selected=b.dataset.order;editing=false;signature='';render();byId('order-content').scrollTop=0;byId('order-back').focus({preventScroll:true});window.dispatchEvent(new CustomEvent('grainworks:orderselected',{detail:{orderId:selected}}));}
 if(b.id==='order-back'){selected=null;editing=false;signature='';render();byId('order-content').scrollTop=0;byId('order-list').querySelector('button')?.focus({preventScroll:true});}
 if(b.id==='order-collapse'){const content=byId('order-content');content.hidden=!content.hidden;b.setAttribute('aria-expanded',String(!content.hidden));b.textContent=content.hidden?'+':'−';b.setAttribute('aria-label',content.hidden?'주문 패널 펼치기':'주문 패널 접기');}
 if(b.dataset.orderLot)G.selectLot(b.dataset.orderLot);
 if(b.dataset.orderAsset)G.selectAsset(b.dataset.orderAsset);
 if(b.dataset.orderEdit)openEditor(b.dataset.orderEdit,b.dataset.editLot);
 if(b.id==='order-edit-cancel'){editing=false;signature='';render();byId('order-back').focus({preventScroll:true});}
 if(b.id==='order-agent'){document.querySelector('[data-agent-task="orders"]').click();byId('agent-question').value=selected+'의 요청·출하·보류·창고 준비·미배정 물량과 가상 납기 상태를 관련 Lot·트럭·알림 근거로 확인해 주세요. 이미 출하된 영향과 미출하 보류를 구분해 주세요.';byId('agent-workbench').scrollIntoView({block:'start',behavior:'smooth'});byId('agent-question').focus({preventScroll:true});}
});
panel.addEventListener('submit',e=>{
 if(e.target.id!=='order-allocation-form')return;e.preventDefault();const form=e.target;if(!form.reportValidity())return;
 const result=G.setAllocation({orderId:selected,lineId:form.dataset.line,lotId:byId('order-edit-lot').value,quantityKg:Number(byId('order-edit-quantity').value),assetId:byId('order-edit-truck').value});
 if(!result.ok){byId('order-edit-error').textContent=result.error||'배정 조건을 확인하세요.';return;}
 editing=false;signature='';document.activeElement.blur();render();byId('order-content').scrollTop=0;byId('order-live').textContent='수동 배정을 저장했습니다. 주문과 트럭 계획에 반영했습니다.';byId('order-back').focus({preventScroll:true});
});
window.GrainOrderUI=Object.freeze({selectedId:()=>selected,select(id){selected=id;editing=false;signature='';byId('order-content').hidden=false;byId('order-collapse').setAttribute('aria-expanded','true');byId('order-collapse').textContent='−';byId('order-collapse').setAttribute('aria-label','주문 패널 접기');render();}});
window.addEventListener('grainworks:orders',render);
window.addEventListener('grainworks:render',()=>markScene(orders().find(o=>o.id===selected),G.snapshot()));
byId('confirm-reset').addEventListener('click',()=>{editing=false;selected=null;signature='';render();});
render();setInterval(()=>{if(!document.hidden)render();},700);
