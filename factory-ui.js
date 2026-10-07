/* Production desk: original records and an explicitly separate copied-state comparison. */
const G=window.Grainworks,F=window.GrainFactory,$=id=>document.getElementById(id);
const escape=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const n=v=>Number(v||0).toLocaleString('ko-KR',{maximumFractionDigits:1});
const states={running:'처리 가능',hold:'검사 보류',idle:'작업 대기',down:'설비 정지'};
const main=document.querySelector('main'),studio=document.querySelector('.studio'),heading=$('factory-section'),agent=$('agent-workbench');
document.querySelector('.intro h1').innerHTML='생산 흐름을 보고,<br>문제의 영향을 근거로 확인합니다.';
document.querySelector('.intro p').innerHTML='가상 사료공정 · 설비 상태 · 품질 영향 · AI 운영 보조 <br>원료부터 출하까지, 같은 생산 기록을 연결합니다.';
main.insertBefore(heading,agent);main.insertBefore(studio,agent);
heading.querySelector('h2').textContent='원료가 제품이 되는 흐름을 살펴보세요.';
$('world').dataset.layer='process';
document.querySelectorAll('button[data-layer]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.layer==='process')));
$('factory').setAttribute('aria-label','가상 사료공장 생산 흐름. 입고, 배합, 검사, 포장 구역을 선택해 생산 묶음과 상태를 확인합니다. 방향키로 회전할 수 있습니다.');
const views=document.createElement('div');views.className='factory-view-switch';views.innerHTML='<button type="button" id="production-view" aria-pressed="true">생산 라인</button><button type="button" id="site-view" aria-pressed="false">전체 현장</button>';
heading.append(views);
const desk=document.createElement('section');desk.className='production-desk';desk.id='production-desk';desk.setAttribute('aria-labelledby','production-heading');
desk.innerHTML=`<div class="production-heading"><div><span class="eyebrow">PRODUCTION / EVIDENCE</span><h2 id="production-heading">설비에서 생산 묶음까지 연결합니다.</h2><p>Lot은 함께 생산·검사하는 묶음입니다. 설비 카드를 누르면 관련 기록을 볼 수 있습니다.</p></div><span class="model-note">합성 기록 · 가정한 설비 능력</span></div><div id="station-cards" class="station-cards"></div><div class="production-detail-grid"><article class="station-notebook"><span class="eyebrow">SELECTED STATION</span><div id="station-detail"></div></article><article class="downtime-lab"><span class="eyebrow">WHAT IF / COPIED STATE</span><h3>설비가 멈추면 대기 물량은?</h3><p>현재 Lot의 복사본을 같은 조건으로 비교합니다. 운영 화면의 시간 흐름과 별도인 가정 모델입니다.</p><form id="downtime-form"><div class="downtime-fields"><label>비교 설비<select id="downtime-station"><option value="MIX-01">MIX-01 · 배합</option><option value="PACK-01">PACK-01 · 포장</option><option value="REC-01">REC-01 · 입고</option><option value="QC-01">QC-01 · 검사</option></select></label><label>정지 시간 (분)<input id="downtime-minutes" type="number" min="0" max="240" value="10" required></label><label>비교 시간 (분)<input id="downtime-horizon" type="number" min="1" max="240" value="20" required></label></div><div class="downtime-actions"><button class="primary" type="submit">가상 조건 계산</button><button class="quiet" type="button" id="downtime-agent">같은 조건을 AI에 질문 ↗</button></div></form><p id="downtime-error" role="alert" hidden></p><div id="downtime-result" aria-live="polite"><p class="comparison-empty">정지 시간을 바꾸고 정상 조건과 비교해 보세요.</p></div></article></div><p class="factory-boundary">설비 능력·배치는 시연 가정입니다. 센서·PLC·실제 MES와 연결되지 않으며 생산성 개선을 측정한 결과가 아닙니다.</p>`;
main.insertBefore(desk,agent);
let selected='MIX-01',signature='';
function snapshot(){return F.snapshot(G.snapshot());}
function render(){
 const data=snapshot(),s=data.stations.find(x=>x.id===selected)||data.stations[0];
 const currentOrders=G.orderSnapshot().orders;
 const sig=JSON.stringify([data,selected,currentOrders]);if(sig===signature)return;signature=sig;$('station-cards').innerHTML=data.stations.map(st=>`<button class="station-card ${st.id===selected?'selected':''}" type="button" data-station="${escape(st.id)}" aria-pressed="${st.id===selected}"><span class="station-id">${escape(st.id)} <i data-status="${escape(st.status)}">${states[st.status]||escape(st.status)}</i></span><strong>${escape(st.title)}</strong><span class="station-load">${n(st.workloadKg)} <small>kg · 연결 물량</small></span><span class="station-bottom">${st.activeLotId?escape(st.activeLotId):'처리 가능한 Lot 없음'}<span>${n(st.capacityKgPerMinute)} kg/분</span></span></button>`).join('');
 const lots=G.snapshot().lots.filter(l=>s.affectedLotIds.includes(l.id));
 const linkedOrders=currentOrders.filter(o=>o.linkedLotIds.some(id=>s.affectedLotIds.includes(id)));
 $('station-detail').innerHTML=`<h3>${escape(s.title)} <span>${escape(s.id)}</span></h3><div class="station-keyfacts"><div><small>대기 물량 · 보류 포함</small><strong>${n(s.queuedKg)} kg</strong></div><div><small>품질 보류 물량</small><strong>${n(s.heldKg)} kg</strong></div></div><p>처리 능력 ${n(s.capacityKgPerMinute)} kg/분은 시연 가정입니다. ${s.activeLotId?'현재 처리 대상으로 '+escape(s.activeLotId)+'을 표시합니다.':'현재 처리 가능한 생산 묶음이 없습니다.'}</p><div class="station-lots">${lots.map(l=>`<button type="button" data-factory-lot="${escape(l.id)}"><span><b>${escape(l.id)}</b><small>${escape(l.product)}</small></span><span>${n(l.quantity)} kg<small>${l.status==='hold'?'품질 보류':'기록 확인 ↗'}</small></span></button>`).join('')||'<p>이 공정에 연결된 Lot이 없습니다.</p>'}</div><div class="station-orders"><h4>연결 주문</h4>${linkedOrders.map(o=>`<button type="button" class="quiet" data-factory-order="${escape(o.id)}">${escape(o.id)} · 준비 ${n(o.readyKg)} kg</button>`).join('')||'<p>연결 주문이 없습니다.</p>'}</div><button type="button" class="quiet" id="station-agent">이 설비와 관련 Lot을 AI에 질문 ↗</button>`;
}
function selection(id){selected=id;signature='';const s=snapshot().stations.find(x=>x.id===id);G.selectStage(s.stage);$('downtime-station').value=id;render();}
desk.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.station)selection(b.dataset.station);if(b.dataset.factoryLot)G.selectLot(b.dataset.factoryLot);if(b.dataset.factoryOrder){window.GrainOrderUI?.select(b.dataset.factoryOrder);$('factory-section').scrollIntoView({block:'start'});}if(b.id==='station-agent'){document.querySelector('[data-agent-task="factory"]').click();$('agent-question').value=selected+'의 현재 상태와 관련 Lot, 품질 보류 근거를 확인해 주세요.';agent.scrollIntoView({behavior:'smooth',block:'start'});}});
function orderComparison(baseline,branch){
 if(!baseline?.orders||!branch?.orders)return '';
 return `<table class="comparison-orders"><caption>주문별 창고 준비 · 같은 가상 시간 기준</caption><thead><tr><th scope="col">주문</th><th scope="col">정상 조건</th><th scope="col">정지 조건</th></tr></thead><tbody>${baseline.orders.map(o=>{const compared=branch.orders.find(x=>x.id===o.id);if(!compared)return '';return `<tr><td>${escape(o.id)}</td>${[o,compared].map(x=>`<td>${n(x.readyKg)} kg 준비<small>${n(x.heldKg)} kg 보류 · ${n(x.unallocatedKg)} kg 미배정</small><small>${x.overdue?'기한 경과':x.remainingKg===0?'출하 완료':'가상 기한 미경과'}</small></td>`).join('')}</tr>`;}).join('')}</tbody></table>`;
}
function assumptions(){return {stationId:$('downtime-station').value,downtimeMinutes:Number($('downtime-minutes').value),horizonMinutes:Number($('downtime-horizon').value)};}
$('downtime-form').addEventListener('submit',e=>{e.preventDefault();try{
 const result=F.simulate(G.snapshot(),assumptions());window.GrainFactoryLastComparison=result;
 const b=result.baseline,c=result.branch;
 $('downtime-result').innerHTML=`<div class="comparison-title"><strong>정상 조건과 정지 조건 비교</strong><span>${assumptions().horizonMinutes}분 · 포장 완료 기준</span></div><div class="comparison-columns"><div><small>정상 조건</small><strong>${n(b.throughputKg)}<em>kg</em></strong></div><div><small>${escape(assumptions().stationId)} ${assumptions().downtimeMinutes}분 정지</small><strong>${n(c.throughputKg)}<em>kg</em></strong></div></div><p>원본 기록을 변경하지 않았습니다. 품질 보류는 두 조건에서 유지합니다. 창고 준비 물량과 가상 기한을 비교합니다. 출하 완료·납기 달성 판정과 구분합니다.</p>${orderComparison(b.orders,c.orders)}`;
 $('downtime-error').hidden=true;
}catch(err){$('downtime-error').hidden=false;$('downtime-error').textContent=err.message;$('downtime-result').replaceChildren();}});
$('downtime-agent').addEventListener('click',()=>{document.querySelector('[data-agent-task="downtime"]').click();const a=assumptions();$('agent-downtime-station').value=a.stationId;$('agent-downtime-minutes').value=a.downtimeMinutes;$('agent-downtime-horizon').value=a.horizonMinutes;$('agent-question').value=a.stationId+'이 '+a.downtimeMinutes+'분 정지할 때 '+a.horizonMinutes+'분 동안 포장 완료 물량과 대기 상태를 비교해 주세요. 가정과 근거를 함께 설명해 주세요.';agent.scrollIntoView({behavior:'smooth',block:'start'});});
views.addEventListener('click',e=>{if(e.target.id==='production-view')G.productionView();else if(e.target.id==='site-view')G.siteView();else return;views.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b===e.target)));});
document.querySelector('[data-agent-task="factory"]').click();
render();setInterval(()=>{if(!document.hidden)render();},1000);
