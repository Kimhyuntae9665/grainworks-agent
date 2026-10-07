"""Read-only evidence tools. All inputs/results are synthetic or user supplied."""
from __future__ import annotations
import copy
import csv
import hashlib
import io
import json
import math
import os
import re
import subprocess
import uuid
from pathlib import Path
from factory_tools import FactoryTools, factory_schemas
from order_tools import OrderTools, order_schemas

ROOT = Path(__file__).resolve().parent
DOCUMENT_LABEL_SCOPE='Nonempty English line labels only: Lot ID:, Moisture:, Temperature:, Inspection Date:. This is not general document-semantic verification.'
DOCUMENT_LABEL_PATTERNS={
    'lotId':r'^[ \t]*Lot[ \t]+ID[ \t]*:[ \t]*[^\r\n \t]',
    'moisture':r'^[ \t]*Moisture[ \t]*:[ \t]*[^\r\n \t]',
    'temperature':r'^[ \t]*Temperature[ \t]*:[ \t]*[^\r\n \t]',
    'inspectionDate':r'^[ \t]*Inspection[ \t]+Date[ \t]*:[ \t]*[^\r\n \t]',
}


def state_hash(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def schema(name, description, properties=None, required=None):
    return {'type': 'function', 'function': {'name': name, 'description': description,
        'parameters': {'type': 'object', 'properties': properties or {}, 'required': required or [], 'additionalProperties': False}}}


STRING = {'type': 'string'}
MAPPING = {'type': 'object', 'description': 'Explicit semantic mapping from inspected CSV. unitMode and unitSource are BOTH REQUIRED; no guessing or automatic defaults.',
    'properties': {
        'lotId':{'type':'string','description':'Exact inspected header containing Lot IDs.'},
        'quantity':{'type':'string','description':'Exact inspected header containing numeric quantity.'},
        'unitMode':{'type':'string','enum':['column','fixed'],'description':'column: each row has units in a separate unit column. fixed: quantity HEADER explicitly declares kg/t.'},
        'unitSource':{'type':'string','description':'If unitMode=column: EXACT header whose cells contain kg/t, e.g unit or 단위 (NEVER quantity header). If unitMode=fixed: literal kg or t only. Both keys REQUIRED.'},
        **{k:{'type':'string','description':'Exact inspected header for '+k+'. Omit if absent.'} for k in ['rawId','moisture','temperature']},
        'confidence':{'type':'number','description':'Your semantic mapping confidence 0..1; below0.85 requires human clarification.'}},
    'required': ['lotId','quantity','confidence','unitMode','unitSource'],
    'additionalProperties': False}
DOC_FIELD={'oneOf':[
    {'type':'object','properties':{'field':{'type':'string','enum':['moisture','temperature']},'value':{'type':'number','description':'JSON NUMBER only, e.g12.0 or31. NEVER quoted string or units like "12.0 %"/"31 C".'},'source':{'type':'string','description':'Exact original documentText substring INCLUDING label and units, e.g "Moisture: 12.0 %". Never a trace_lot evidence label.'}},'required':['field','value','source'],'additionalProperties':False},
    {'type':'object','properties':{'field':{'type':'string','enum':['lotId','inspectionDate']},'value':{'type':'string','description':'Exact Lot ID or date string present in source.'},'source':{'type':'string','description':'Exact original documentText substring INCLUDING label. Omit absent fields instead of empty strings.'}},'required':['field','value','source'],'additionalProperties':False},
]}
TOOL_SCHEMAS = [
    schema('list_blocking_alerts', 'Read current blocking alerts and totals, then trace affected Lots.'),
    schema('trace_lot', 'Read exact Lot record, related raw-material Lots and assigned assets.', {'lot_id': STRING}, ['lot_id']),
    schema('get_asset_manifest', 'Read synthetic logistics allocation and blocked kg.', {'asset_id': STRING}, ['asset_id']),
    schema('get_action_history', 'Read recorded alerts, reinspection and manual actions.', {'lot_id': STRING}, ['lot_id']),
    schema('search_demo_procedure', 'Retrieve authored synthetic procedure clauses; never real factory SOP.', {'query': STRING}, ['query']),
    schema('inspect_inputs', 'Inspect two CSV headers/samples or original document text before semantic mapping.'),
    schema('compare_import_records', 'Apply your explicit semantic header mapping, deterministic kg/t normalization, duplicate and mismatch checks.', {'left_mapping': MAPPING,'right_mapping': MAPPING}, ['left_mapping','right_mapping']),
    schema('extract_document_fields', 'Extract ALL present original documentText fields, not ledger QC. Nonempty English labels Lot ID/Moisture/Temperature/Inspection Date must not be omitted. Measurement values JSON NUMBERS without units; Lot/date strings. Omit actually absent fields, never add zero/empty-source placeholders. No OCR here.', {'fields': {'type':'array','maxItems':4,'items': DOC_FIELD}}, ['fields']),
    schema('simulate_branch', 'Compare copied engine branches. Use explicit assumed reinspection only; never actual state mutation.', {'assumptions': {'type':'array','maxItems':3,'items':{'type':'object','properties':{'lot_id':STRING,'action':{'type':'string','enum':['reinspect_release']},'moisture':{'type':'number'},'temperature':{'type':'number'}},'required':['lot_id','action','moisture','temperature'],'additionalProperties':False}},'virtual_minutes': {'type':'integer','minimum':1,'maximum':120}}, ['assumptions','virtual_minutes']),
    schema('prepare_action_proposal', 'Draft a human review proposal for a traced Lot using evidence IDs. No execution. release is refused if engine would reject it.', {'lot_id':STRING,'action':{'type':'string','enum':['hold','release','resolve']},'reason':STRING,'evidence_ids':{'type':'array','items':STRING}}, ['lot_id','action','reason','evidence_ids']),
]
TOOL_SCHEMAS.extend(factory_schemas(schema))
TOOL_SCHEMAS.extend(order_schemas(schema))


class DomainTools(FactoryTools, OrderTools):
    def __init__(self, state, inputs=None):
        self.state = copy.deepcopy(state)
        self.hash = state_hash(state)
        self.inputs = inputs or {}
        self.evidence = {}
        self.proposals = []
        self.traced = set()
        self.comparison = self.simulation = self.document = None
        self.factory = self.factorySimulation = None
        self.orders = None

    def ev(self, label, value, lot_id=None, asset_id=None, key=None):
        eid = key or f'E-{len(self.evidence)+1}'
        record = {'id':eid,'label':label,'value':value}
        if lot_id: record['lotId'] = lot_id
        if asset_id: record['assetId'] = asset_id
        self.evidence[eid] = record
        return record

    def lot(self, lot_id):
        lot = next((l for l in self.state['lots'] if l['id'] == lot_id), None)
        if not lot: raise ValueError('Unknown Lot ID; no record can be inferred')
        return lot

    def bridge(self, operation, **args):
        command = [os.environ.get('GRAINWORKS_NODE','node'), str(ROOT/'tool_bridge.js')]
        result = subprocess.run(command, input=json.dumps({'operation':operation,'state':self.state,'args':args},ensure_ascii=False), text=True, encoding='utf-8', capture_output=True, timeout=20, cwd=ROOT)
        if result.returncode: raise ValueError(result.stderr[:500])
        return json.loads(result.stdout)

    def call(self, name, args):
        if name not in {t['function']['name'] for t in TOOL_SCHEMAS}: raise ValueError('Unknown tool')
        if not isinstance(args,dict): raise ValueError('Arguments must be an object')
        if name=='compare_import_records':
            for side in ['left','right']:
                mapping=args.get(side+'_mapping')
                if not isinstance(mapping,dict) or not {'unitMode','unitSource'}.issubset(mapping):
                    headers=', '.join(self.read_csv(side)[0])
                    raise ValueError(side+'_mapping requires BOTH unitMode and unitSource. For per-row units: unitMode="column", unitSource=EXACT unit HEADER. For header-declared fixed kg/t: unitMode="fixed", unitSource="kg" or "t". Actual headers: '+headers+'. Never infer or omit units.')
        return getattr(self,name)(**args)

    def list_blocking_alerts(self):
        alerts = [a for a in self.state['alerts'] if not a['resolved'] and a.get('blocking')]
        report = self.bridge('report')
        evidence = [self.ev('현재 '+k,report[k],key='TOTAL-'+k) for k in ['totalKg','shippedKg','heldKg','unresolved']]
        for a in alerts[:30]: evidence.append(self.ev(a['title'], a['details'], a['lotId'], key=a['id']))
        return {'alerts':alerts[:30],'totalAlerts':len(alerts),'report':report,'evidence':evidence,'synthetic':True}

    def trace_lot(self, lot_id):
        lot = self.lot(lot_id); self.traced.add(lot_id)
        assets=[]
        for aid in ['TRK-01','TRK-02','CNT-01','CNT-02','WH-01','FL-01']:
            manifest=self.bridge('manifest',asset_id=aid)
            if any(l['id']==lot_id for l in manifest['lots']): assets.append(aid)
        evidence=[self.ev(lot_id+' '+label,value,lot_id,key=lot_id+'-'+field) for field,label,value in [
            ('quantity','물량 kg',lot['quantity']),('temperature','온도 °C',lot['qc']['temperature']),('moisture','수분 %',lot['qc']['moisture']),('status','상태',lot['status']),('rawId','원료',lot['rawId']),('stage','단계',lot['stage'])]]
        evidence.extend([self.ev('데모 보관 온도 기준 °C',self.state['settings']['storageLimit'],key='SETTING-storageLimit'),self.ev('데모 수분 기준 %',self.state['settings']['moistureLimit'],key='SETTING-moistureLimit')])
        return {'lot':lot,'relatedLots':[{'id':l['id'],'quantity':l['quantity'],'stage':l['stage']} for l in self.state['lots'] if l['rawId']==lot['rawId']][:30], 'assetIds':assets,'alerts':[a for a in self.state['alerts'] if a['lotId']==lot_id],'evidence':evidence}

    def get_asset_manifest(self, asset_id):
        m = self.bridge('manifest',asset_id=asset_id)
        if not m: raise ValueError('Unknown asset ID')
        m['lots']=[{'id':l['id'],'quantity':l['quantity'],'status':l['status']} for l in m['lots']]
        return {'manifest':m,'evidence':[self.ev(asset_id+' '+label,m[field],asset_id=asset_id,key=asset_id+'-'+field) for field,label in [('quantity','모의 배정 kg'),('heldKg','차단 kg'),('status','상태')]]}

    def get_action_history(self, lot_id):
        self.lot(lot_id)
        records=[e for e in self.state['events'] if e.get('lotId')==lot_id][-20:]
        evidence=[self.ev('기록 '+e['id'],e['message'],lot_id,key=e['id']) for e in records]
        return {'events':records,'alerts':[a for a in self.state['alerts'] if a['lotId']==lot_id],'evidence':evidence}

    def search_demo_procedure(self, query):
        if not isinstance(query,str) or not 1<=len(query)<=200: raise ValueError('Query length 1..200')
        clauses=json.loads((ROOT/'fixtures/procedures.json').read_text(encoding='utf-8'))
        tokens=re.findall(r'[\w]+',query.lower())
        clauses.sort(key=lambda c:sum(t in c['text'].lower() or t in c['label'].lower() for t in tokens),reverse=True)
        return {'synthetic':True,'clauses':[self.ev(c['label'],c['text'],key=c['id']) for c in clauses[:2]]}

    def read_csv(self, side):
        text=self.inputs.get(side+'Csv','')
        if not isinstance(text,str) or not text.strip(): raise ValueError('Both CSV inputs are required')
        # DictReader would silently overwrite duplicate headers and pad short rows.
        # Check the physical table before assigning semantic column names.
        try: table=[row for row in csv.reader(io.StringIO(text.lstrip('\ufeff')),strict=True) if row]
        except csv.Error as exc: raise ValueError('Malformed CSV quoting: '+str(exc)) from exc
        if len(table)<2 or len(table)>201: raise ValueError('CSV must contain 1..200 records')
        headers=table[0]
        normalized=[header.strip() for header in headers]
        if not headers or len(headers)>20 or any(not header for header in normalized): raise ValueError('CSV headers must be nonempty; at most 20 columns')
        if len(set(normalized))!=len(normalized): raise ValueError('Duplicate CSV headers are not allowed')
        if any(len(row)!=len(headers) for row in table[1:]): raise ValueError('Malformed CSV row width; every row must match headers')
        return [dict(zip(headers,row)) for row in table[1:]]

    def inspect_inputs(self):
        result={}
        if self.inputs.get('leftCsv') or self.inputs.get('rightCsv'):
            for side in ['left','right']:
                rows=self.read_csv(side);result[side]={'headers':list(rows[0]),'sample':rows[:4],'rowCount':len(rows)}
        if self.inputs.get('documentText'): result['documentText']=self.inputs['documentText'][:6000]
        if not result: raise ValueError('No input data')
        return result

    def normalized(self, rows, mapping):
        allowed={'lotId','quantity','unit','fixedUnit','unitMode','unitSource','rawId','moisture','temperature','confidence'}
        if not isinstance(mapping,dict) or set(mapping)-allowed: raise ValueError('Invalid mapping')
        native_contract='unitMode' in mapping or 'unitSource' in mapping
        if native_contract:
            if 'unit' in mapping or 'fixedUnit' in mapping: raise ValueError('Mixed unit contracts forbidden: provide unitMode/unitSource only, not unit/fixedUnit')
            mode,source=mapping.get('unitMode'),mapping.get('unitSource')
            if mode not in ['column','fixed'] or not isinstance(source,str) or not source:
                raise ValueError('Both explicit unitMode (column/fixed) and nonempty unitSource are required')
            if mode=='fixed' and source not in ['kg','t']: raise ValueError('Fixed unitSource must be the literal kg or t')
            # Strict adapter of MODEL-PROVIDED mode/source, without semantic inference.
            mapping={k:v for k,v in mapping.items() if k not in ['unitMode','unitSource']}
            mapping['unit' if mode=='column' else 'fixedUnit']=source
        confidence=mapping.get('confidence')
        if isinstance(confidence,bool) or not isinstance(confidence,(int,float)) or not math.isfinite(confidence) or not .85<=confidence<=1: raise ValueError('Semantic mapping confidence below 0.85; manual mapping required')
        if not all(mapping.get(k) in rows[0] for k in ['lotId','quantity']): raise ValueError('Missing exact mapped header')
        if mapping.get('unit') and mapping.get('fixedUnit'): raise ValueError('Choose exactly one: unit is an exact CSV HEADER; fixedUnit is a kg/t LITERAL. For mixed kg/t rows use the real unit column header and omit fixedUnit.')
        if not mapping.get('unit') and not mapping.get('fixedUnit'): raise ValueError('Explicit unit required. Provide unitMode="column" and unitSource=EXACT per-row unit HEADER, or unitMode="fixed" and unitSource="kg"/"t" only when quantity header declares that unit. Actual headers: '+', '.join(rows[0])+'. No automatic unit inference.')
        if not mapping.get('unit'):
            header=mapping['quantity'].lower()
            fixed=mapping['fixedUnit'].lower()
            declared=bool(re.search(r'kg|킬로그램',header)) if fixed=='kg' else bool(re.search(r'(?:^|[_\s(])t(?:$|[_\s)])|ton|톤',header)) if fixed in ['t','ton','톤'] else False
            if not declared: raise ValueError('Fixed unit is not declared in quantity header; manual unit evidence required. For a separate per-row unit column, choose unitMode="column" with unitSource=EXACT unit HEADER. Actual headers: '+', '.join(rows[0]))
        if mapping.get('unit') and mapping['unit'] not in rows[0]:
            correction='If quantity HEADER declares kg/t, choose unitMode="fixed", unitSource="kg"/"t"; otherwise unitMode="column", unitSource=real unit HEADER.' if native_contract else 'If quantity header declares kg/t, omit unit and use fixedUnit; otherwise map the real unit column HEADER.'
            raise ValueError('Unknown unit header '+repr(mapping['unit'])+'. unit must be an EXACT header, not a kg/t literal. Headers: '+', '.join(rows[0])+'. '+correction+' Do not guess or silently normalize.')
        for field in ['rawId','moisture','temperature']:
            if mapping.get(field) and mapping[field] not in rows[0]: raise ValueError('Unknown mapped header')
        result={};issues=[]
        for i,row in enumerate(rows,2):
            lid=row[mapping['lotId']].strip()
            unit=(row[mapping['unit']] if mapping.get('unit') else mapping['fixedUnit']).strip().lower()
            if unit not in ['kg','t','ton','톤']: raise ValueError('Ambiguous or unsupported unit: '+unit+'. A column-mode unitSource must name a column whose CELLS contain kg/t, not the numeric quantity column. If quantity header declares kg/t, choose unitMode="fixed" and unitSource="kg"/"t". Correct mapping explicitly; no inference.')
            try: qty=float(row[mapping['quantity']].replace(',',''))
            except ValueError: raise ValueError('Invalid numeric quantity')
            if not math.isfinite(qty) or qty<=0: raise ValueError('Quantity must be positive and finite')
            normalized={'lotId':lid,'kg':qty*(1000 if unit!='kg' else 1),'sourceRow':i,'originalQuantity':row[mapping['quantity']],'originalUnit':unit}
            for field in ['rawId','moisture','temperature']:
                if mapping.get(field):
                    value=row[mapping[field]].strip()
                    if field!='rawId' and value:
                        try: value=float(value.rstrip('%°Cc '))
                        except ValueError: raise ValueError('Invalid mapped measurement')
                        if not math.isfinite(value): raise ValueError('Nonfinite mapped measurement')
                    normalized[field]=value
            result.setdefault(lid,[]).append(normalized)
            if not lid: issues.append({'type':'missingLotId','row':i})
        return result,issues

    def compare_import_records(self, left_mapping, right_mapping):
        left,issues=self.normalized(self.read_csv('left'),left_mapping);right,more=self.normalized(self.read_csv('right'),right_mapping);issues+=more
        rows=[]
        for lid in sorted(set(left)|set(right)):
            a,b=left.get(lid,[]),right.get(lid,[])
            if len(a)>1 or len(b)>1: status='duplicate';differences=['duplicate rows: no aggregation permitted']
            elif not a or not b: status='missing';differences=['missing on one side']
            else:
                differences=[field for field in ['kg','rawId','moisture','temperature'] if field in a[0] and field in b[0] and (not math.isclose(a[0][field],b[0][field],abs_tol=.001) if field=='kg' else a[0][field]!=b[0][field])]
                status='mismatch' if differences else 'match'
            row={'lotId':lid,'status':status,'left':a,'right':b,'differences':differences}
            rows.append(row);self.ev('대사 '+lid,row,lot_id=lid,key='CSV-'+lid)
        self.comparison={'rows':rows,'issues':issues,'mappings':{'left':left_mapping,'right':right_mapping},'semanticMappingVerified':False,'note':'컬럼 의미/신뢰도는 모델 판단. 계산은 결정적 검증. 수정 실행 없음.'}
        return {**self.comparison,'rows':rows[:12],'rowCount':len(rows),'previewTruncated':len(rows)>12,'counts':{status:sum(r['status']==status for r in rows) for status in ['match','mismatch','duplicate','missing']}}

    def extract_document_fields(self, fields):
        text=self.inputs.get('documentText','')
        if not isinstance(text,str) or not text: raise ValueError('Document text required; OCR is not implemented')
        accepted=[];seen=set()
        for f in fields:
            field,value,source=f.get('field'),f.get('value'),f.get('source')
            if field not in ['lotId','moisture','temperature','inspectionDate'] or field in seen: raise ValueError('Invalid/duplicate document field')
            if not isinstance(source,str) or not source or source not in text: raise ValueError('Source for '+str(field)+' must match an EXACT original inspect_inputs.documentText substring INCLUDING its label. Do not use trace_lot evidence labels/measurements. Omit missing fields; no empty sources.')
            if field in ['moisture','temperature']:
                if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value): raise ValueError('Invalid '+field+' measurement type: use a JSON NUMBER without quotes or units, e.g12.0 not "12.0 %";31 not "31 C". Keep exact original label/units in source, never substitute ledger QC.')
                nums=[float(n) for n in re.findall(r'-?\d+(?:\.\d+)?',source)]
                if value not in nums: raise ValueError('Measurement absent from source')
                if not (0<=value<=100 if field=='moisture' else -30<=value<=100): raise ValueError('Measurement outside range')
                if not re.search('수분|moisture',source,re.I) if field=='moisture' else not re.search('온도|temperature',source,re.I): raise ValueError('Source lacks field label')
            elif not isinstance(value,str) or value not in source: raise ValueError('Field absent from source')
            seen.add(field)
            accepted.append({**f,'start':text.index(source),'end':text.index(source)+len(source),'evidenceId':'DOC-'+field})
        present={field for field,pattern in DOCUMENT_LABEL_PATTERNS.items() if re.search(pattern,text,re.I|re.M)}
        omitted=present-seen
        if omitted: raise ValueError('Document extraction omitted explicitly labelled fields: '+', '.join(sorted(omitted))+'. Retry extract_document_fields with ALL present original English line labels (Lot ID, Moisture, Temperature, Inspection Date). Omit only truly absent fields; no zero/empty-source placeholders. Values are not automatically filled.')
        for field in accepted: self.ev('문서 '+field['field'],field['value'],key=field['evidenceId'])
        self.document={'fields':accepted,'missingFields':[k for k in ['lotId','moisture','temperature','inspectionDate'] if k not in seen],'sourceText':text,'ocrPerformed':False,'semanticLabelVerified':False,'completenessScope':DOCUMENT_LABEL_SCOPE,'note':'원문 부분문자열·수치 및 명시된 영어 라벨 누락 검사. 문서 진위/의미는 사람 확인 필요. 입력 실행 없음.'}
        return {k:v for k,v in self.document.items() if k!='sourceText'}

    def simulate_branch(self, assumptions, virtual_minutes):
        if not isinstance(virtual_minutes,int) or isinstance(virtual_minutes,bool) or not 1<=virtual_minutes<=120: raise ValueError('Virtual minutes 1..120')
        if not isinstance(assumptions,list) or len(assumptions)>3: raise ValueError('At most 3 explicit assumptions')
        for a in assumptions:
            self.lot(a['lot_id'])
            if a.get('action')!='reinspect_release': raise ValueError('Unsupported assumption')
            for k,lo,hi in [('moisture',0,100),('temperature',-30,100)]:
                if not isinstance(a.get(k),(int,float)) or not math.isfinite(a[k]) or not lo<=a[k]<=hi: raise ValueError('Invalid assumption measurement')
        self.simulation=self.bridge('simulate',assumptions=assumptions,virtual_minutes=virtual_minutes)
        self.ev('가상 복제 분기 계산',self.simulation,key='SIMULATION')
        return self.simulation

    def prepare_action_proposal(self, lot_id, action, reason, evidence_ids):
        lot=self.lot(lot_id)
        if lot_id not in self.traced: raise ValueError('Trace Lot first')
        if action not in ['hold','release','resolve'] or not isinstance(reason,str) or not 1<=len(reason.strip())<=500: raise ValueError('Invalid proposal action/reason')
        if not isinstance(evidence_ids,list) or not evidence_ids or any(e not in self.evidence for e in evidence_ids): raise ValueError('Only observed evidence IDs allowed')
        if not any(self.evidence[e].get('lotId')==lot_id for e in evidence_ids): raise ValueError('Proposal requires target Lot evidence')
        alerts=[a for a in self.state['alerts'] if a['lotId']==lot_id and not a['resolved'] and a.get('blocking')]
        if action=='resolve' and not alerts: raise ValueError('No open target alert')
        if action in ['hold','release'] and lot['stage']=='shipped': raise ValueError('Already shipped')
        if action=='release' and (alerts or lot['status']!='hold' or not lot['rawId'] or lot['qc']['stale'] or lot['qc']['moisture'] is None or lot['qc']['temperature'] is None or lot['qc']['moisture']>self.state['settings']['moistureLimit'] or lot['qc']['temperature']>self.state['settings']['storageLimit']): raise ValueError('Release prerequisites absent; propose manual alert review/reinspection instead')
        safe_reason={
            'resolve':f'{lot_id} 경보 {alerts[0]["id"] if alerts else ""}의 저장 기록 확인. 재검사값과 조치 사유를 담당자가 확인해야 합니다.',
            'hold':f'{lot_id} 이동 보류 검토. 저장 기록과 보류 사유를 담당자가 확인해야 합니다.',
            'release':f'{lot_id} 보류 해제 검토. 저장된 검사값과 원료 연결·신선도·미해결 경보 조건을 담당자가 다시 확인해야 합니다.',
        }[action]
        proposal={'id':'P-'+uuid.uuid4().hex,'lotId':lot_id,'action':action,'reason':safe_reason,'reasonOrigin':'verified_tool_template','modelSuggestedReason':reason.strip(),'modelSuggestedReasonVerified':False,'evidenceIds':evidence_ids,'stateHash':self.hash}
        if action=='resolve': proposal['alertId']=alerts[0]['id']
        self.proposals.append(proposal)
        return proposal

    def result(self, summary):
        evidence=list(self.evidence.values())
        result={'summary':summary[:4000],'facts':[{'label':e['label'],'value':e['value'],'evidenceIds':[e['id']]} for e in evidence if not isinstance(e['value'],(dict,list))], 'evidence':evidence,'proposals':self.proposals,'warnings':['합성 데모/사용자 입력 자료입니다. 실제 공장 적합 판정이 아닙니다.','근거 카드와 계산은 도구 결과입니다. 모델 자유 서술과 의미 매핑은 완전 검증되지 않았습니다.'],'stateHash':self.hash,'summaryVerified':False}
        if self.proposals: result['warnings'].append('수동 조치 사유에는 도구의 안전한 검토 문구만 채웁니다. 모델 제안 사유는 검증되지 않은 초안이며 검사·조치 완료 기록이 아닙니다.')
        for key in ['comparison','simulation','document','factory','factorySimulation','orders']:
            if getattr(self,key) is not None: result[key]=getattr(self,key)
        return result
