(function () {
  'use strict';
  const E=window.FeedEngine,$=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  if(!E){$('inspector-body').innerHTML='<div class="fault-panel">시뮬레이션 파일을 불러오지 못했습니다. engine.js가 같은 폴더에 있는지 확인하고 다시 열어주세요.</div>';return;}
  const A=window.FeedAssets;let selectedAsset=null,assetKind='all';
  const KEY='grainworks-state-v1',SYNC=KEY+'-unsynced';let state=E.createState();state.running=false;
  let selectedId='LOT-005',selectedStage='quality',desk='lot',bench='lots',filter='',stageFilter='all',statusFilter='all',action=null;
  let serverOnline=false,revision=0,savedRevision=-1,saving=false,toastTimer,renderTimer=0,persistTimer=0,lastFrame=0;
  const fmt=n=>Number(n||0).toLocaleString('ko-KR',{maximumFractionDigits:1});
  const time=t=>{let minutes=Math.floor(t)+480;return String(Math.floor(minutes/60)%24).padStart(2,'0')+':'+String(minutes%60).padStart(2,'0');};
  const stageName=s=>E.LABELS[s]||s;
  const lot=()=>state.lots.find(l=>l.id===selectedId)||state.lots[0];
  const statusName=l=>l.status==='hold'?'보류':l.status==='warning'?'영향 확인':l.stage==='shipped'?'출하 완료':'정상';
  const statusHtml=l=>`<span class="status ${esc(l.status==='ok'&&l.stage==='shipped'?'shipped':l.status)}">${statusName(l)}</span>`;
  function valid(s){return s&&s.version===1&&Array.isArray(s.lots)&&s.lots.length<=1000&&Array.isArray(s.events)&&Array.isArray(s.alerts)&&Number.isFinite(s.tick)&&s.settings&&s.lots.every(l=>l&&typeof l.id==='string'&&E.STAGES.includes(l.stage)&&Number.isFinite(l.quantity)&&l.quantity>0&&l.qc)&&Number.isFinite(s.nextEventId)&&Number.isFinite(s.nextAlertId);}
  function toast(text,error=false){$('toast').textContent=text;$('toast').className='toast'+(error?' error':'');$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,4200);}
  function dirty(){revision++;persistTimer=0;renderAll(true);}
  function loadLocal(){try{const s=JSON.parse(localStorage.getItem(KEY));if(valid(s)){state=s;state.running=false;return true;}}catch{}return false;}
  loadLocal();
  function saveLocal(){try{localStorage.setItem(KEY,JSON.stringify(state));localStorage.setItem(SYNC,'1');$('save-state').textContent=serverOnline?'저장 중':'브라우저 저장';return true;}catch{$('save-state').textContent='브라우저 저장 실패';return false;}}
  async function save(){if(saving||savedRevision===revision)return;saving=true;const r=revision,snapshot=JSON.stringify(state);const localStored=saveLocal();let persisted=localStored;try{if(serverOnline){const res=await fetch('/api/state',{method:'POST',headers:{'Content-Type':'application/json'},body:snapshot});if(!res.ok)throw Error('save');persisted=true;if(revision===r){try{localStorage.removeItem(SYNC);}catch{}}$('save-state').textContent='로컬 서버 저장';}if(persisted)savedRevision=r;}catch{serverOnline=false;if(localStored){savedRevision=r;$('save-state').textContent='브라우저 저장 · 서버 미동기화';}else $('save-state').textContent='저장 실패 · 보고서를 내려받으세요';}finally{saving=false;}}
  async function loadServer(){if(location.protocol==='file:'){$('save-state').textContent='브라우저 저장';return;}const startedRevision=revision;let unsynced=false;try{unsynced=localStorage.getItem(SYNC)==='1';}catch{}try{const res=await fetch('/api/state');if(!res.ok)throw Error('load');const payload=await res.json();serverOnline=true;const keepLocal=unsynced||revision!==startedRevision;if(!keepLocal&&valid(payload.state)){state=payload.state;state.running=false;selectedId=state.lots.find(l=>l.stage==='quality')?.id||state.lots[0]?.id;renderAll();}$('save-state').textContent=keepLocal?'브라우저 변경 복원·동기화 중':payload.state?'로컬 서버에서 복원':'로컬 서버 연결';dirty();save();}catch{$('save-state').textContent='브라우저 저장';}}
  function selectLot(id){closeAsset();if(!state.lots.some(l=>l.id===id))return;selectedId=id;selectedStage=lot().stage;scene.selected=selectedStage;setDesk('lot');renderAll();}
  function selectStage(stage){closeAsset();selectedStage=stage;scene.selected=stage;stageFilter=stage;let match=state.lots.find(l=>l.stage===stage);if(match)selectedId=match.id;else selectedId=null;setDesk('lot');renderAll();}
  function setDesk(name){desk=name;document.querySelectorAll('[data-desk]').forEach(b=>{b.setAttribute('aria-selected',b.dataset.desk===desk);b.tabIndex=b.dataset.desk===desk?0:-1;});$('inspector-body').setAttribute('aria-labelledby','desk-'+name);renderInspector(true);$('inspector-body').scrollTop=0;}
  function setBench(name){bench=name;document.querySelectorAll('[data-bench]').forEach(b=>{b.setAttribute('aria-selected',b.dataset.bench===bench);b.tabIndex=b.dataset.bench===bench?0:-1;});$('bench-body').setAttribute('aria-labelledby','bench-'+name);renderBench(true);}
  function buildLabels(){const names={intake:'입고',mixing:'배합',quality:'검사',packing:'포장',warehouse:'창고',shipped:'출하'};$('stage-labels').innerHTML=E.STAGES.map(s=>`<button data-stage="${s}" class="stage-tag" aria-label="${E.LABELS[s]} 구역 선택"><strong>${names[s]}</strong><small data-stage-count="${s}">0 LOT</small></button>`).join('');}
  buildLabels();buildAssetLabels();const scene=new FactoryScene($('factory'),$('stage-labels'),selectStage,selectAsset);
  const kindName={truck:'화물차',forklift:'지게차',container:'컨테이너',warehouse:'창고'};
  function buildAssetLabels(){$('asset-labels').innerHTML=A.definitions.map(a=>`<button class="asset-tag" data-asset="${a.id}" aria-label="${a.name} 상태 보기"><strong>${a.id}</strong><small>상태 확인</small></button>`).join('');}
  function selectAsset(id){if(!A.snapshot(state,id))return;selectedAsset=id;scene.selectAsset(id);$('asset-panel').hidden=false;$('asset-browser').hidden=true;$('asset-list-toggle').setAttribute('aria-expanded','false');$('world').classList.add('has-asset-panel');renderAssets();$('asset-detail').scrollTop=0;$('asset-close').focus({preventScroll:true});scene.draw(state,0);}
  function closeAsset(){if(!selectedAsset)return;selectedAsset=null;scene.selectAsset(null);$('asset-panel').hidden=true;$('world').classList.remove('has-asset-panel');}
  function renderAssets(){
    const assets=A.list(state,{speed:scene.assetSpeed});
    replaceContent($('asset-list'),assets.filter(a=>assetKind==='all'||a.kind===assetKind).map(a=>`<button data-asset="${a.id}" class="asset-row" data-tone="${a.tone}" aria-pressed="${a.id===selectedAsset}"><span><strong>${a.id}</strong><small>${a.name}</small></span><span class="asset-state">${a.status}</span></button>`).join(''));
    if(!selectedAsset)return;const a=assets.find(a=>a.id===selectedAsset);$('asset-category').textContent=kindName[a.kind]+' · 모의 현장';$('asset-heading').textContent=a.name;$('asset-code').textContent=a.id;
    const metric=(title,value)=>`<div><dt>${title}</dt><dd>${value}</dd></div>`;
    let facts=metric('현재 위치',esc(a.location));
    if(a.kind==='truck')facts+=metric('목적지(시연)',esc(a.destination))+metric('배정 물량',fmt(a.quantity)+' kg')+metric('배정 Lot',a.lots.length+'개');
    else if(a.kind==='forklift')facts+=metric('배터리(모의)',fmt(a.battery)+'%')+metric('이동 속도(모의)',fmt(a.speed)+' km/h')+metric('이송 대상',a.lots.length+'개 Lot');
    else facts+=metric(a.kind==='warehouse'?'재고 물량':'연결 물량',fmt(a.quantity)+' kg')+metric('관련 Lot',a.lots.length+'개')+metric('Lot 검사 온도',a.temperature===null?'측정 없음':fmt(a.temperature)+' °C');
    let html=`<div class="asset-status-line" data-tone="${a.tone}"><span class="asset-state">${a.status}</span><small>가상 ${time(a.clock)}</small></div><dl class="asset-facts">${facts}</dl>`;
    if(a.capacity){const percent=a.occupancy*100;html+=`<div class="asset-capacity"><div><strong>${a.kind==='warehouse'?'보관 기준':'배정 기준'} 대비 ${fmt(percent)}%</strong><span>${fmt(a.capacity)} kg · 시연 기준</span></div><meter min="0" max="100" value="${Math.min(100,percent)}" aria-label="${esc(a.name)} 기준 대비 물량">${fmt(percent)}%</meter></div>`;}
    html+=`<div class="asset-work"><h4>${a.kind==='forklift'?'현재 작업':'다음 작업'}</h4><p>${a.blockedCount?`${a.blockedCount}개 Lot · ${fmt(a.heldKg)} kg의 검사 상태를 확인해야 합니다. `:''}${a.overCapacity?'시연 기준을 넘었습니다. 배정 물량을 확인하세요.':a.kind==='truck'?'이 도크에 배정된 Lot입니다. 각 Lot의 검사 상태를 먼저 확인하세요.':a.kind==='forklift'?a.job?`${esc(a.job.id)} · ${esc(a.job.product)}의 ${a.job.stage==='packing'?'포장 → 창고':'창고 → 출하'} 이송 경로를 시연합니다.`:'이송 가능한 Lot을 기다립니다.':a.kind==='container'?a.id==='CNT-01'?'원료 입고 Lot의 검수 상태를 확인합니다.':'완제품 창고 Lot의 출고 계획을 확인합니다.':'창고 재고와 품질 보류 상태를 함께 확인합니다.'}</p></div>`;
    html+=`<div class="asset-exceptions"><span>확인 필요 <strong>${a.blockedCount} Lot</strong></span><span>미해결 알림 <strong>${a.alertCount}건</strong></span></div>`;
    html+=`<div class="asset-panel-actions"><button id="asset-focus">가까이 보기</button>${a.kind==='forklift'?`<button id="asset-follow" aria-pressed="${scene.following===a.id}">${scene.following===a.id?'추적 중 · 해제':'따라가기'}</button><button id="asset-play">${state.running?'운영 일시정지':'운영 시작'}</button>`:''}</div>`;
    html+=`<div class="asset-manifest"><h4>관련 Lot <span>${a.lots.length}</span></h4>${a.lots.length?a.lots.slice(0,8).map(l=>`<button data-asset-lot="${esc(l.id)}" aria-label="${esc(l.id)} Lot 상세 열기"><span><strong>${esc(l.id)}</strong><small>${esc(l.product)}</small></span><span>${fmt(l.quantity)} kg<br><small>${statusName(l)}</small></span></button>`).join(''):'<p>현재 연결된 Lot이 없습니다.</p>'}${a.lots.length>8?`<p>외 ${a.lots.length-8}개는 Lot 목록에서 확인하세요.</p>`:''}</div><p class="asset-note">물량·검사 값은 Lot 기록 집계입니다. 배터리·속도·배정 기준과 위치는 시연용입니다. 대상별 물량은 같은 Lot의 다른 보기이며 서로 합산하지 않습니다.</p>`;
    replaceContent($('asset-detail'),html);
  }
  function renderTop(){const report=E.report(state);$('clock').textContent=time(state.tick);$('day').textContent='DAY '+String(Math.floor((state.tick+480)/1440)+1).padStart(2,'0');$('stat-shipped').innerHTML=fmt(report.shippedKg)+' <small>kg</small>';$('stat-held').innerHTML=fmt(report.heldKg)+' <small>kg</small>';$('stat-alerts').innerHTML=report.unresolved+' <small>건</small>';$('alert-badge').textContent=report.unresolved;$('inspector-count').textContent=state.lots.length+'개 Lot';
    $('play').textContent=state.running?'Ⅱ':'▶';$('play').setAttribute('aria-label',state.running?'일시정지':'시뮬레이션 시작');$('play').setAttribute('aria-pressed',!state.running);
    document.querySelectorAll('[data-speed]').forEach(b=>{b.classList.toggle('active',Number(b.dataset.speed)===state.speed);b.setAttribute('aria-pressed',Number(b.dataset.speed)===state.speed);});
    document.querySelectorAll('.stage-tag').forEach(b=>{let s=b.dataset.stage,n=report.byStage[s];b.classList.toggle('selected',selectedStage===s);b.classList.toggle('has-hold',state.lots.some(l=>l.stage===s&&l.status==='hold'));b.setAttribute('aria-pressed',selectedStage===s);b.querySelector('small').textContent=n.lots+' LOT';});
    replaceContent($('process-strip'),E.STAGES.map((s,i)=>`<button class="process-step ${s===selectedStage?'active':''}" data-stage="${s}" aria-pressed="${s===selectedStage}"><span>${String(i+1).padStart(2,'0')} · ${stageName(s)}</span><strong>${fmt(report.byStage[s].kg)}<small>kg · ${report.byStage[s].lots} Lot</small></strong></button>`).join(''));
  }
  function renderInspector(force=false){let box=$('inspector-body'),scroll=box.scrollTop;let html='';
    if(desk==='lot'){
      const l=selectedId?state.lots.find(x=>x.id===selectedId):null;
      if(!l)html=`<div class="empty">${esc(stageName(selectedStage))} 구역에 현재 Lot이 없습니다.<br>다른 구역을 선택하거나 CSV를 가져오세요.</div>`;
      else {const connected=state.lots.filter(x=>x.rawId&&x.rawId===l.rawId),sum=E.summary(state,l.id);
        html=`<div class="lot-title"><strong>${esc(l.id)}</strong>${statusHtml(l)}</div><p class="product-name">${esc(l.product)}</p><dl class="mini-facts"><div><dt>현재 공정</dt><dd>${stageName(l.stage)}</dd></div><div><dt>수량</dt><dd>${fmt(l.quantity)} kg</dd></div><div><dt>수분 / 데모 기준</dt><dd>${l.qc.moisture===null?'미측정':fmt(l.qc.moisture)+'%'} / ${state.settings.moistureLimit}%</dd></div><div><dt>보관 온도 / 기준</dt><dd>${l.qc.temperature===null?'미측정':fmt(l.qc.temperature)+'°C'} / ${state.settings.storageLimit}°C</dd></div></dl>`;
        if(l.qc.stale)html+='<p class="form-error">센서 기록이 지연됐습니다. 재측정 확인이 필요합니다.</p>';
        if(l.shipment)html+=`<div class="trace-card"><h3>출하 기록</h3><p>${esc(l.shipment.destination)} · ${esc(l.shipment.time)}<br>${l.status==='warning'?'이미 출하된 물량입니다. 영향 범위를 확인하세요.':'출하 이력이 연결되어 있습니다.'}</p></div>`;
        html+=`<div class="trace-card"><h3>같은 원료의 흐름</h3><div class="trace-chain"><span>${esc(l.rawId||'원료 연결 누락')}</span><span>→</span>${connected.map(x=>`<button data-lot="${esc(x.id)}" aria-label="연결된 ${esc(x.id)} 선택">${esc(x.id)}</button>`).join('')}</div><p>${l.rawId?`입력 원료 ID로 연결된 ${connected.length}개 Lot · ${fmt(connected.reduce((n,x)=>n+x.quantity,0))} kg`:'원료 ID를 복원하면 연결된 Lot을 추적할 수 있습니다.'}</p></div>`;
        html+=`<div class="lot-actions">${l.stage!=='shipped'?`<button data-action="hold" data-id="${esc(l.id)}">보류하기</button><button class="release" data-action="release" data-id="${esc(l.id)}" ${l.status!=='hold'?'disabled':''}>보류 해제</button>`:`<button data-open-desk="alerts">출하 영향 확인</button>`}<button data-open-desk="log">이력 보기</button></div>`;
        html+=`<div class="summary-card"><h3>기록으로 읽는 운영 요약</h3><p>${esc(sum.text)}</p><details><summary style="font-size:10px;margin-top:8px;cursor:pointer">근거와 다음 조치</summary><ul>${sum.evidence.map(e=>'<li>'+esc(e)+'</li>').join('')}</ul><ul>${sum.nextSteps.map(e=>'<li>'+esc(e)+'</li>').join('')}</ul></details><small>현재 기록·설정 기준으로 생성 · 외부 AI 호출 없음</small></div>`;
      }
    }else if(desk==='alerts'){
      const alerts=[...state.alerts].sort((a,b)=>Number(a.resolved)-Number(b.resolved)||b.tick-a.tick);
      html=alerts.length?alerts.map(a=>`<article class="alert-item ${a.resolved?'resolved':''}"><header><strong>${esc(a.title)}</strong><span class="status ${a.resolved?'':'hold'}">${a.resolved?'처리 기록':'확인 필요'}</span></header><p>${esc(a.lotId)} · ${a.shippedImpact?'출하된 Lot 영향':'공정 이동 보류'}<br>${esc(a.details)}</p>${a.resolved?`<p>조치: ${esc(typeof a.resolution==='object'?a.resolution.reason:a.resolution||'처리 완료')}</p>`:''}<footer><button data-lot="${esc(a.lotId)}">Lot 보기</button>${!a.resolved?`<button data-action="resolve" data-id="${esc(a.id)}">재검사·조치 기록</button>`:''}</footer></article>`).join(''):'<div class="empty">현재 미해결 알림이 없습니다.<br>아래에서 상황을 발생시켜 보세요.</div>';
    }else html=state.events.length?[...state.events].reverse().slice(0,80).map(e=>`<div class="log-item"><time>${time(e.tick)}</time><p>${e.lotId?'<strong>'+esc(e.lotId)+'</strong><br>':''}${esc(e.message)}</p></div>`).join(''):'<div class="empty">아직 활동 기록이 없습니다.</div>';
    replaceContent(box,html);box.scrollTop=scroll;
  }
  function replaceContent(container,html){if(container.innerHTML===html)return;const active=document.activeElement,inside=container.contains(active),attrs=['id','data-lot','data-stage','data-action','data-id','aria-label'].filter(a=>active.hasAttribute?.(a)).map(a=>[a,active.getAttribute(a)]),tag=active.tagName,detailsOpen=!!container.querySelector('details[open]');container.innerHTML=html;if(detailsOpen&&container.querySelector('details'))container.querySelector('details').open=true;if(inside){const match=[...container.querySelectorAll(tag)].find(el=>attrs.every(([a,v])=>el.getAttribute(a)===v));if(match)match.focus({preventScroll:true});}}
  function filteredLots(){return state.lots.filter(l=>(!filter||[l.id,l.rawId,l.product].some(v=>String(v).toLowerCase().includes(filter.toLowerCase())))&&(stageFilter==='all'||l.stage===stageFilter)&&(statusFilter==='all'||l.status===statusFilter));}
  function renderRows(){let rows=$('lot-rows');if(!rows)return;const list=filteredLots();const html=list.length?list.map(l=>`<tr class="${l.id===selectedId?'selected':''}"><td><button data-lot="${esc(l.id)}">${esc(l.id)}</button></td><td class="mono">${esc(l.rawId||'연결 누락')}</td><td>${esc(l.product)}</td><td class="mono">${fmt(l.quantity)} kg</td><td>${stageName(l.stage)}</td><td>${statusHtml(l)}</td><td class="mono">${l.qc.moisture===null?'미측정':fmt(l.qc.moisture)+'%'}</td><td><button data-lot="${esc(l.id)}" aria-label="${esc(l.id)} 상세 보기">보기 ↗</button></td></tr>`).join(''):'<tr><td colspan="8" class="empty">조건에 맞는 Lot이 없습니다. 검색어나 필터를 변경하세요.</td></tr>';replaceContent(rows,html);$('table-count').textContent=`${list.length} / ${state.lots.length} LOT · 전체 ${fmt(E.report(state).totalKg)} kg`;}
  function renderBench(full=false){const body=$('bench-body');if(bench==='lots'){
      if(full||!$('lot-rows')){body.innerHTML=`<div class="table-toolbar"><div class="filters"><input id="lot-search" type="search" aria-label="Lot·원료·제품 검색" placeholder="Lot, 원료, 제품 검색" value="${esc(filter)}"><select id="stage-filter" aria-label="공정 필터"><option value="all">모든 공정</option>${E.STAGES.map(s=>`<option value="${s}" ${stageFilter===s?'selected':''}>${stageName(s)}</option>`).join('')}</select><select id="status-filter" aria-label="상태 필터"><option value="all">모든 상태</option><option value="ok" ${statusFilter==='ok'?'selected':''}>정상</option><option value="hold" ${statusFilter==='hold'?'selected':''}>보류</option><option value="warning" ${statusFilter==='warning'?'selected':''}>영향 확인</option></select></div><span class="table-summary" id="table-count"></span></div><div class="table-wrap"><table><thead><tr><th scope="col">Lot</th><th scope="col">원료 ID</th><th scope="col">제품</th><th scope="col">수량</th><th scope="col">공정</th><th scope="col">상태</th><th scope="col">수분</th><th scope="col">상세</th></tr></thead><tbody id="lot-rows"></tbody></table></div><div class="table-notice">Lot을 누르면 원료 연결과 조치 이력을 확인할 수 있습니다. ${stageFilter!=='all'?'<button class="quiet" id="clear-filters" style="padding:3px 8px;font-size:10px">모든 공정 보기</button>':''}</div>`;}renderRows();
    }else if(bench==='settings'&&full){body.innerHTML=`<div class="settings-panel">${[{key:'productionRate',title:'생산 속도',min:20,max:200,step:10,unit:'%',description:'100%는 데모 기본 속도입니다. 입고·배합·검사·포장에 걸리는 가상 시간이 바뀝니다.'},{key:'moistureLimit',title:'수분 확인 기준',min:8,max:20,step:.5,unit:'%',description:'검사 단계에서 이 값을 넘는 기록은 보류합니다. 실제 제품별 품질 기준이 아닌 시연용 값입니다.'},{key:'storageLimit',title:'보관 온도 기준',min:18,max:40,step:1,unit:'°C',description:'창고 단계에서 기준을 넘으면 확인 알림을 남깁니다. 기준 변경만으로 보류가 해제되지는 않습니다.'}].map(s=>`<div class="setting"><label for="setting-${s.key}">${s.title}</label><div class="value"><output id="value-${s.key}" for="setting-${s.key}">${state.settings[s.key]}</output><small>${s.unit}</small></div><input type="range" id="setting-${s.key}" data-setting="${s.key}" min="${s.min}" max="${s.max}" step="${s.step}" value="${state.settings[s.key]}"><p>${s.description}</p></div>`).join('')}<div class="settings-note">생산 조건은 현재 시뮬레이션에 바로 반영됩니다. 변경 내역은 활동에 기록하며, 이미 발생한 보류는 근거 확인과 별도 해제가 필요합니다.</div></div>`;
    }else if(bench==='data'&&full){body.innerHTML=`<div class="data-panel"><div class="data-copy"><h3>내 데이터로 흐름을 연결하세요.</h3><p>CSV의 원료 ID와 Lot ID를 기준으로 연결합니다.<br>누락·중복·잘못된 값은 가져오기 전에 검사합니다.<br>샘플을 내려받아 일부 값을 바꾸며 실험해 보세요.</p><div class="data-actions"><a href="sample-lots.csv" download>샘플 CSV ↓</a><button id="export-csv">현재 Lot 내보내기 ↓</button><button id="choose-file">CSV 파일 선택</button><input type="file" id="csv-file" accept=".csv,text/csv" hidden></div><p style="font-size:10px;margin-top:17px">기존 Lot에 추가 · 최대 1,000개 / 1 MB<br>공정: intake, mixing, quality, packing, warehouse, shipped<br>실제 사료 배합비와 내부 회사 데이터는 포함하지 않습니다.</p></div><div class="data-form"><label for="csv-text">CSV 붙여넣기</label><textarea id="csv-text" spellcheck="false" placeholder="id,rawId,product,quantity,moisture,temperature,stage&#10;LOT-101,RAW-101,데모 사료,1000,12,24,intake"></textarea><div class="form-error" id="csv-error" role="alert"></div><footer><span>전체 입력을 검증한 뒤 한 번에 추가합니다.</span><button id="import-csv" class="primary">데이터 추가</button></footer></div></div>`;
    }else if(bench==='report'){const r=E.report(state);const html=`<div class="report-panel"><div><span class="eyebrow">OPERATION SNAPSHOT · ${time(state.tick)}</span><h3 style="margin-top:8px">현재 흐름의 운영 보고서</h3><div class="report-kpis"><div><span>전체 기록 물량</span><strong>${fmt(r.totalKg)}<small style="font-size:10px"> kg</small></strong></div><div><span>보류 물량</span><strong>${fmt(r.heldKg)}<small style="font-size:10px"> kg</small></strong></div><div><span>미해결 알림</span><strong>${r.unresolved}<small style="font-size:10px"> 건</small></strong></div></div><p>출하 ${fmt(r.shippedKg)} kg + 미출하 ${fmt(r.activeKg+r.heldKg)} kg = 전체 ${fmt(r.totalKg)} kg.<br>보류 물량은 미출하 물량에 포함됩니다. 물량을 중복 합산하지 않습니다.</p><div class="data-actions"><button id="export-report" class="primary">보고서 JSON ↓</button><button id="export-events">조치 이력 CSV ↓</button></div><p style="font-size:10px;margin-top:13px">합성·직접 입력 데이터의 현재 집계입니다. 실제 공장 개선율이나 처리시간 절감률을 주장하지 않습니다.</p></div><div><h3>영향받은 Lot</h3>${r.affectedLots.length?`<ul class="report-list">${r.affectedLots.map(l=>`<li><button data-lot="${esc(l.id)}" style="border:0;background:none;padding:0">${esc(l.id)} ↗</button><span>${fmt(l.kg)} kg · ${l.shipped?'출하 영향':stageName(l.stage)}</span></li>`).join('')}</ul>`:'<p class="empty">미해결 알림에 연결된 Lot이 없습니다.<br>상황을 발생시키면 영향 범위가 표시됩니다.</p>'}<p style="font-size:10px;margin-top:13px">${state.events.length}건의 최근 활동 기록 · 기록 저장 한도 500건</p></div></div>`;replaceContent(body,html);}
  }
  function renderAll(force=false){scene.draw(state,0);renderTop();renderInspector(force);renderBench();renderAssets();}
  function download(name,data,mime){const a=document.createElement('a');const url=URL.createObjectURL(new Blob([data],{type:mime}));a.href=url;a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),3000);}
  const csvCell=v=>{let text=String(v??'');if(/^[\s]*[=+@-]/.test(text)||/^[\t\r]/.test(text))text="'"+text;return '"'+text.replace(/"/g,'""')+'"';};
  function showAction(kind,id){action={kind,id};const dialog=$('action-dialog');$('action-error').textContent='';$('action-reason').value='';const old=$('recheck-fields');if(old)old.remove();
    const a=kind==='resolve'?state.alerts.find(a=>a.id===id):null;const l=a?state.lots.find(l=>l.id===a.lotId):state.lots.find(l=>l.id===id);
    $('action-title').textContent=kind==='hold'?'Lot 보류하기':kind==='release'?'보류 해제하기':'재검사·조치 기록';
    $('action-info').textContent=kind==='resolve'?`${a?.title||''} · ${l?.id||''}. 재검사 값과 확인한 근거를 기록하세요. 알림 처리 후 미출하 Lot은 별도로 보류를 해제합니다.`:kind==='release'?`${l?.id||''}의 미해결 알림과 현재 검사 기록을 확인합니다. 검사값이 기준을 넘으면 해제되지 않습니다.`:`${l?.id||''}의 공정 이동을 멈춥니다. 사유를 남겨 다음 담당자가 확인할 수 있게 하세요.`;
    if(kind==='resolve'&&l){const el=document.createElement('div');el.id='recheck-fields';el.innerHTML=`<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:15px"><div><label for="recheck-moisture" style="margin:0 0 6px">재검사 수분 (%)</label><input id="recheck-moisture" type="number" min="0" max="100" step="0.1" required value="${l.qc.moisture??''}" style="width:100%;padding:8px;border:1px solid #cdd7d0;border-radius:5px"></div><div><label for="recheck-temp" style="margin:0 0 6px">재검사 온도 (°C)</label><input id="recheck-temp" type="number" min="-30" max="100" step="0.1" required value="${l.qc.temperature??''}" style="width:100%;padding:8px;border:1px solid #cdd7d0;border-radius:5px"></div></div>${!l.rawId?`<label for="recheck-raw">확인한 원료 ID</label><input id="recheck-raw" required maxlength="80" placeholder="${esc(l.previousRawId||'RAW-2401')}" style="width:100%;padding:9px;border:1px solid #cdd7d0;border-radius:5px">`:''}<p style="font-size:10px;margin-top:9px">시연용 기준: 수분 ${state.settings.moistureLimit}% 이하 · 온도 ${state.settings.storageLimit}°C 이하. 모의 재검사 값은 실제 품질 판정을 대신하지 않습니다.</p>`;$('action-info').after(el);}
    dialog.showModal();$('action-reason').focus();
  }
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&selectedAsset){closeAsset();$('asset-list-toggle').focus({preventScroll:true});return;}if(e.key==='Escape'&&!$('asset-browser').hidden){$('asset-browser').hidden=true;$('asset-list-toggle').setAttribute('aria-expanded','false');$('asset-list-toggle').focus({preventScroll:true});return;}if(e.key==='Escape'&&$('world').classList.contains('is-expanded')){$('world').classList.remove('is-expanded');$('fullscreen').setAttribute('aria-label','공장 전체 화면');$('fullscreen').setAttribute('aria-pressed','false');scene.resize();$('fullscreen').focus();}});
  document.addEventListener('click',async e=>{const b=e.target.closest('button,a');if(!b)return;
    if(b.dataset.asset){selectAsset(b.dataset.asset);}
    else if(b.dataset.assetLot){closeAsset();$('world').classList.remove('is-expanded');$('fullscreen').setAttribute('aria-label','공장 전체 화면');$('fullscreen').setAttribute('aria-pressed','false');selectLot(b.dataset.assetLot);$('inspector-body').scrollIntoView({block:'nearest'});$('desk-lot').focus({preventScroll:true});}
    else if(b.dataset.kind){assetKind=b.dataset.kind;document.querySelectorAll('[data-kind]').forEach(el=>el.setAttribute('aria-pressed',el===b));renderAssets();}
    else if(b.dataset.layer){$('world').dataset.layer=b.dataset.layer;document.querySelectorAll('[data-layer]').forEach(el=>{if(el.tagName==='BUTTON')el.setAttribute('aria-pressed',el===b);});if(b.dataset.layer==='process')closeAsset();scene.dirty=true;}
    else if(b.dataset.stage){selectStage(b.dataset.stage);renderBench(true);}
    else if(b.dataset.lot){selectLot(b.dataset.lot);}
    else if(b.dataset.desk)setDesk(b.dataset.desk);
    else if(b.dataset.bench)setBench(b.dataset.bench);
    else if(b.dataset.openDesk)setDesk(b.dataset.openDesk);
    else if(b.dataset.action)showAction(b.dataset.action,b.dataset.id);
    else if(b.dataset.light){scene.setLighting(b.dataset.light);document.querySelectorAll('[data-light]').forEach(el=>el.setAttribute('aria-pressed',el===b));}
    else if(b.dataset.speed){state.speed=Number(b.dataset.speed);dirty();}
    else if(b.dataset.inject){const result=E.inject(state,b.dataset.inject);if(!result.ok){toast(result.error,true);return;}state.running=false;const a=[...state.alerts].reverse().find(a=>!a.resolved);if(a){selectedId=a.lotId;selectedStage=state.lots.find(l=>l.id===a.lotId)?.stage||selectedStage;scene.selected=selectedStage;}setDesk('alerts');dirty();toast('상황을 발생시켰습니다. 관련 기록을 확인하세요.');}
    else if(b.hasAttribute('data-close'))b.closest('dialog').close();
    else switch(b.id){
      case 'asset-close':closeAsset();$('asset-list-toggle').focus({preventScroll:true});break;
      case 'asset-list-toggle':{const open=$('asset-browser').hidden;$('asset-browser').hidden=!open;b.setAttribute('aria-expanded',open);if(open)closeAsset();renderAssets();}break;
      case 'asset-focus':scene.focusAsset();break;
      case 'asset-follow':if(scene.following)scene.following=null;else scene.followAsset(selectedAsset);renderAssets();break;
      case 'asset-play':state.running=!state.running;dirty();break;
      case 'play':state.running=!state.running;dirty();break;
      case 'zoom-in':scene.setZoom(scene.zoom*1.15);break;
      case 'zoom-out':scene.setZoom(scene.zoom/1.15);break;
      case 'focus-stage':scene.focusStage();break;
      case 'roof-toggle':{const visible=b.getAttribute('aria-pressed')!=='true';scene.setRoof(visible);b.setAttribute('aria-pressed',visible);b.textContent=visible?'내부 보기':'지붕 보기';}break;
      case 'zoom-reset':scene.following=null;scene.resetView();break;
      case 'fullscreen':{const expanded=$('world').classList.toggle('is-expanded');b.setAttribute('aria-label',expanded?'공장 전체 화면 닫기':'공장 전체 화면');b.setAttribute('aria-pressed',expanded);scene.resize();}break;
      case 'help':$('help-dialog').showModal();break;
      case 'reset':$('reset-dialog').showModal();break;
      case 'confirm-reset':closeAsset();$('reset-dialog').close();{state=E.createState();state.running=false;selectedId='LOT-005';selectedStage='quality';stageFilter='all';statusFilter='all';filter='';scene.resetView();scene.selected='quality';setDesk('lot');setBench('lots');dirty();save();toast('초기 샘플로 되돌렸습니다.');}break;
      case 'clear-filters':stageFilter='all';statusFilter='all';filter='';renderBench(true);break;
      case 'choose-file':$('csv-file').click();break;
      case 'export-csv':download('grainworks-lots.csv',E.exportCsv(state),'text/csv;charset=utf-8');toast('현재 Lot CSV를 내려받았습니다.');break;
      case 'import-csv':{const result=E.importCsv(state,$('csv-text').value);if(!result.ok){$('csv-error').textContent=result.error;return;}$('csv-error').textContent='';$('csv-text').value='';state.running=false;stageFilter='all';dirty();toast(`${result.imported??result.count??result.added??'새'}개 Lot을 추가했습니다.`);break;}
      case 'export-report':download('grainworks-report.json',JSON.stringify({project:'Grainworks',synthetic:true,generatedAt:new Date().toISOString(),virtualMinutes:state.tick,settings:state.settings,...E.report(state)},null,2),'application/json');toast('현재 운영 보고서를 내려받았습니다.');break;
      case 'export-events':download('grainworks-events.csv','\ufeff'+[['id','virtualMinutes','kind','lotId','message'],...state.events.map(e=>[e.id,e.tick,e.kind,e.lotId,e.message])].map(r=>r.map(csvCell).join(',')).join('\r\n'),'text/csv;charset=utf-8');toast('조치 이력 CSV를 내려받았습니다.');break;
    }
  });
  document.addEventListener('input',e=>{if(e.target.id==='lot-search'){filter=e.target.value;renderRows();}if(e.target.dataset.setting)$('value-'+e.target.dataset.setting).textContent=e.target.value;});
  document.addEventListener('change',async e=>{
    if(e.target.id==='stage-filter'){stageFilter=e.target.value;renderRows();}if(e.target.id==='status-filter'){statusFilter=e.target.value;renderRows();}
    if(e.target.dataset.setting){const result=E.configure(state,e.target.dataset.setting,Number(e.target.value));if(result.ok){dirty();toast('운영 기준을 변경하고 이력을 남겼습니다.');}else toast(result.error,true);}
    if(e.target.id==='csv-file'){const f=e.target.files[0];if(!f)return;if(f.size>1048576){$('csv-error').textContent='1 MB 이하 CSV 파일을 선택하세요.';return;}try{$('csv-text').value=await f.text();$('csv-error').textContent='';}catch{$('csv-error').textContent='파일을 읽지 못했습니다. CSV 내용을 붙여넣어 주세요.';}e.target.value='';}
  });
  $('action-form').addEventListener('submit',e=>{e.preventDefault();if(!action)return;let result;if(action.kind==='resolve'){const recheck={moisture:Number($('recheck-moisture').value),temperature:Number($('recheck-temp').value)};if($('recheck-raw'))recheck.rawId=$('recheck-raw').value;if(recheck.moisture>state.settings.moistureLimit||recheck.temperature>state.settings.storageLimit){$('action-error').textContent='재검사 값이 현재 기준을 넘습니다. 알림을 유지합니다. 확인 후 기준에 맞는 값을 입력하세요.';return;}result=E.resolve(state,action.id,$('action-reason').value,recheck);}else result=E[action.kind](state,action.id,$('action-reason').value);
    if(!result.ok){$('action-error').textContent=result.error;return;}$('action-dialog').close();dirty();save();toast(action.kind==='resolve'?'재검사와 조치 사유를 기록했습니다. 보류 해제는 Lot에서 확인하세요.':action.kind==='hold'?'Lot을 보류했습니다.':'검사 기록을 확인하고 보류를 해제했습니다.');action=null;
  });
  document.addEventListener('keydown',e=>{const group=e.target.closest('[role="tablist"]');if(group&&['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){const tabs=[...group.querySelectorAll('[role="tab"]')],i=tabs.indexOf(e.target),next=e.key==='Home'?0:e.key==='End'?tabs.length-1:(i+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;e.preventDefault();tabs[next].click();tabs[next].focus();return;}if(e.code==='Space'&&!['INPUT','TEXTAREA','SELECT','BUTTON'].includes(e.target.tagName)&&!document.querySelector('dialog[open]')){e.preventDefault();state.running=!state.running;dirty();}});
  document.addEventListener('visibilitychange',()=>{lastFrame=0;if(document.hidden)save();});
  window.addEventListener('pagehide',()=>{saveLocal();if(serverOnline&&navigator.sendBeacon)navigator.sendBeacon('/api/state',new Blob([JSON.stringify(state)],{type:'application/json'}));});
  function frame(now){const dt=lastFrame?Math.min((now-lastFrame)/1000,.2):0;lastFrame=now;if(!document.hidden){if(state.running&&dt){const result=E.step(state,dt*state.speed*.25);if(!result.ok){state.running=false;toast(result.error,true);}else revision++;}scene.draw(state,dt);renderTimer+=dt;persistTimer+=dt;if(renderTimer>.5){renderAll();renderTimer=0;}if(persistTimer>3){save();persistTimer=0;}}requestAnimationFrame(frame);}
  window.Grainworks=Object.freeze({
    snapshot:()=>JSON.parse(JSON.stringify(state)),
    async save(){
      if(location.protocol==='file:')throw Error('AI 작업대는 로컬 서버 주소에서 실행하세요.');
      state.running=false;dirty();
      while(saving)await new Promise(resolve=>setTimeout(resolve,50));
      saving=true;
      try{
        saveLocal();
        const response=await fetch('/api/state',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(state)});
        if(!response.ok)throw Error('현재 상태를 서버에 저장하지 못했습니다. 연결을 확인하세요.');
        serverOnline=true;savedRevision=revision;
        try{localStorage.removeItem(SYNC);}catch{}
        $('save-state').textContent='로컬 서버 저장';
        return JSON.parse(JSON.stringify(state));
      }finally{saving=false;}
    },
    selectLot(id){selectLot(id);$('inspector-body').scrollIntoView({block:'nearest'});$('desk-lot').focus({preventScroll:true});},
    selectAsset(id){selectAsset(id);$('world').scrollIntoView({block:'nearest'});},
    openAction(proposal){
      const {lotId,action:kind,reason,alertId}=proposal||{};
      if(!['hold','release','resolve'].includes(kind))throw Error('지원하지 않는 조치 제안입니다. 원본 기록을 직접 확인하세요.');
      if(!state.lots.some(item=>item.id===lotId))throw Error('제안의 Lot이 현재 기록에 없습니다.');
      let id=lotId;
      if(kind==='resolve'){
        const alert=state.alerts.find(item=>item.id===alertId&&item.lotId===lotId&&!item.resolved&&item.blocking)||state.alerts.find(item=>item.lotId===lotId&&!item.resolved&&item.blocking);
        if(!alert)throw Error('현재 처리할 차단 경보가 없습니다. 새 작업 결과를 확인하세요.');
        id=alert.id;
      }
      selectLot(lotId);showAction(kind,id);$('action-reason').value=String(reason||'').slice(0,300);
    }
  });
  renderAll();loadServer();requestAnimationFrame(frame);
  window.dispatchEvent(new Event('grainworks:ready'));
})();
