'use strict';
// Invoked only by the Python read-only tool service. stdin is a copied snapshot.
const E=require('./engine.js'), A=require('./assets.js');
let input='';process.stdin.setEncoding('utf8');process.stdin.on('data',s=>input+=s);
process.stdin.on('end',()=>{try{
 const {operation,state,args={}}=JSON.parse(input);
 let result;
 if(operation==='manifest') result=A.snapshot(state,args.asset_id);
 else if(operation==='report') result=E.report(state);
 else if(operation==='simulate') {
  const baseline=JSON.parse(JSON.stringify(state)), branch=JSON.parse(JSON.stringify(state));
  const before=E.report(state), assumptions=args.assumptions||[], actions=[];
  for(const a of assumptions){
   if(a.action!=='reinspect_release') throw Error('Only explicit synthetic reinspection/release assumption supported');
   const lot=branch.lots.find(l=>l.id===a.lot_id);if(!lot)throw Error('Unknown Lot');
   const alerts=branch.alerts.filter(x=>x.lotId===lot.id&&!x.resolved);
   for(const alert of alerts){const r=E.resolve(branch,alert.id,'가상 분기: 실제 검사 아님',{moisture:a.moisture,temperature:a.temperature});if(!r.ok)throw Error(r.error);}
   if(!alerts.length){lot.qc.moisture=a.moisture;lot.qc.temperature=a.temperature;lot.qc.stale=false;}
   const r=E.release(branch,lot.id,'가상 분기: 재검사 및 별도 해제 완료 가정');if(!r.ok)throw Error(r.error);
   actions.push({lotId:lot.id,moisture:a.moisture,temperature:a.temperature,action:a.action});
  }
  baseline.running=true;branch.running=true;
  for(let t=0;t<args.virtual_minutes;t++){E.step(baseline,1);E.step(branch,1);}
  result={synthetic:true,virtualMinutes:args.virtual_minutes,assumptions:actions,before,baseline:E.report(baseline),branch:E.report(branch),limitations:'주문 납기·설비 일정 없음. 실제 검사/해제 아님. 원본 변경 없음.'};
  if(result.baseline.totalKg!==before.totalKg||result.branch.totalKg!==before.totalKg)throw Error('Mass conservation failed');
 }else throw Error('Unknown bridge operation');
 process.stdout.write(JSON.stringify(result));
}catch(e){process.stderr.write(e.message);process.exitCode=1;}});
