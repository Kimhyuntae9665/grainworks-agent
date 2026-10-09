"""Safety/grounding checks; mocked transport tests workflow, not model accuracy."""
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from agent_backend import AgentService, execute_run, ollama_json, validate_summary, validate_simulation_request, shipment_tool_context, explicit_proposal_requests
from domain_tools import DomainTools, state_hash, TOOL_SCHEMAS
from server import Store

ROOT=Path(__file__).resolve().parent


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.state=json.loads((ROOT/'fixtures/demo-state.json').read_text(encoding='utf-8'))
        self.tools=DomainTools(self.state)

    def test_real_engine_trace_and_allocation(self):
        result=self.tools.trace_lot('LOT-007')
        self.assertEqual(result['lot']['quantity'],4500)
        self.assertIn('TRK-02',result['assetIds'])
        asset=self.tools.get_asset_manifest('TRK-02')['manifest']
        self.assertEqual(asset['heldKg'],4500)
        with self.assertRaises(ValueError): self.tools.trace_lot('LOT-999')

    def test_csv_units_mismatch_and_duplicates(self):
        inputs={s+'Csv':(ROOT/f'fixtures/reconcile-{s}.csv').read_text(encoding='utf-8') for s in ['left','right']}
        tools=DomainTools(self.state,inputs)
        left={'lotId':'lot_id','quantity':'weight_kg','fixedUnit':'kg','rawId':'raw_id','moisture':'moisture_pct','confidence':.99}
        right={'lotId':'로트','quantity':'중량','unit':'단위','rawId':'원료','moisture':'수분','confidence':.99}
        result=tools.compare_import_records(left,right)
        self.assertEqual([r['status'] for r in result['rows']],['match','mismatch','duplicate'])
        self.assertEqual(result['rows'][0]['right'][0]['kg'],4500)
        self.assertEqual(result['rows'][1]['right'][0]['kg'],4700)
        with self.assertRaises(ValueError): tools.compare_import_records({**left,'confidence':.6},right)
        with self.assertRaises(ValueError): tools.compare_import_records({**left,'fixedUnit':'lb'},right)

    def test_csv_rejects_duplicate_empty_headers_and_bad_row_width_before_mapping(self):
        invalid=[
            'lot_id,weight_kg,weight_kg\nLOT-007,4500,4700\n',
            'lot_id,weight_kg, weight_kg \nLOT-007,4500,4700\n',
            'lot_id,,weight_kg\nLOT-007,ignored,4500\n',
            'lot_id,   ,weight_kg\nLOT-007,ignored,4500\n',
            'lot_id,weight_kg\nLOT-007\n',
            'lot_id,weight_kg\nLOT-007,4500,4700\n',
            'lot_id,weight_kg\nLOT-007,"4500\n',
        ]
        for text in invalid:
            with self.subTest(csv=text):
                tools=DomainTools(self.state,{'leftCsv':text})
                with self.assertRaises(ValueError): tools.read_csv('left')
                with self.assertRaises(ValueError): tools.inspect_inputs()
                self.assertIsNone(tools.comparison)
                self.assertFalse(tools.evidence)
        valid=DomainTools(self.state,{'leftCsv':'\ufefflot_id,note,weight_kg\nLOT-007,"first, second",4500\n'})
        self.assertEqual(valid.read_csv('left')[0]['note'],'first, second')

    def test_csv_literal_unit_headers_are_rejected_with_actionable_correction(self):
        inputs={s+'Csv':(ROOT/f'fixtures/reconcile-{s}.csv').read_text(encoding='utf-8') for s in ['left','right']}
        tools=DomainTools(self.state,inputs)
        left={'confidence':.95,'lotId':'lot_id','quantity':'weight_kg','unit':'kg'}
        right={'confidence':.9,'lotId':'로트','quantity':'중량','unit':'t','fixedUnit':'t'}
        with self.assertRaisesRegex(ValueError,'EXACT header.*Headers:.*weight_kg.*omit unit and use fixedUnit'):
            tools.compare_import_records(left,right)
        self.assertIsNone(tools.comparison);self.assertFalse(tools.evidence)
        left.pop('unit');left['fixedUnit']='kg'
        with self.assertRaisesRegex(ValueError,'Choose exactly one'):
            tools.compare_import_records(left,right)
        right.pop('fixedUnit');right['unit']='단위'
        result=tools.compare_import_records(left,right)
        self.assertEqual([row['status'] for row in result['rows']],['match','mismatch','duplicate'])
        self.assertEqual(result['rows'][0]['right'][0]['kg'],4500)

    def test_native_csv_mode_source_is_required_explicit_and_not_inferred(self):
        inputs={s+'Csv':(ROOT/f'fixtures/reconcile-{s}.csv').read_text(encoding='utf-8') for s in ['left','right']}
        tools=DomainTools(self.state,inputs)
        left={'confidence':.95,'lotId':'lot_id','quantity':'weight_kg','unitMode':'fixed','unitSource':'kg'}
        right={'confidence':.95,'lotId':'로트','quantity':'중량','unitMode':'column','unitSource':'단위'}
        native_schema=next(t['function']['parameters']['properties']['left_mapping'] for t in TOOL_SCHEMAS if t['function']['name']=='compare_import_records')
        self.assertTrue({'unitMode','unitSource'}.issubset(native_schema['required']))
        self.assertNotIn('unit',native_schema['properties']);self.assertNotIn('fixedUnit',native_schema['properties'])
        missing={k:v for k,v in left.items() if k!='unitSource'}
        with self.assertRaisesRegex(ValueError,'BOTH unitMode and unitSource.*Actual headers:.*weight_kg'):
            tools.call('compare_import_records',{'left_mapping':missing,'right_mapping':right})
        with self.assertRaisesRegex(ValueError,'Mixed unit contracts'):
            tools.compare_import_records({**left,'fixedUnit':'kg'},right)
        with self.assertRaisesRegex(ValueError,'unsupported unit: 4500.*numeric quantity column'):
            tools.compare_import_records({**left,'unitMode':'column','unitSource':'weight_kg'},right)
        with self.assertRaisesRegex(ValueError,'Fixed unit is not declared'):
            tools.compare_import_records(left,{**right,'unitMode':'fixed','unitSource':'t'})
        with self.assertRaisesRegex(ValueError,'unitMode="fixed".*unitSource=real unit HEADER'):
            tools.compare_import_records({**left,'unitMode':'column','unitSource':'kg'},right)
        result=tools.call('compare_import_records',{'left_mapping':left,'right_mapping':right})
        self.assertEqual([row['status'] for row in result['rows']],['match','mismatch','duplicate'])
        self.assertEqual(result['rows'][0]['right'][0]['kg'],4500)
        self.assertEqual(result['mappings']['left'],left)

    def test_native_csv_correction_does_not_request_repeated_inspection(self):
        inputs={s+'Csv':(ROOT/f'fixtures/reconcile-{s}.csv').read_text(encoding='utf-8') for s in ['left','right']}
        left={'confidence':.95,'lotId':'lot_id','quantity':'weight_kg','unitMode':'fixed','unitSource':'kg'}
        right={'confidence':.95,'lotId':'로트','quantity':'중량','unitMode':'column','unitSource':'단위'}
        requests=[];trace=[]
        def chat(path,payload):
            requests.append(payload)
            if len(requests)==1: name,args='inspect_inputs',{}
            elif len(requests)==2:
                self.assertIn('inspection already done',payload['messages'][-1]['content'])
                self.assertEqual(payload['options']['num_predict'],320)
                name,args='compare_import_records',{'left_mapping':{k:v for k,v in left.items() if k not in ['unitMode','unitSource']},'right_mapping':right}
            elif len(requests)==3:
                self.assertIn('inspection already done',payload['messages'][-1]['content'])
                self.assertNotIn('inspect_inputs then',payload['messages'][-1]['content'])
                self.assertIn('BOTH unitMode and unitSource',payload['messages'][-2]['content'])
                name,args='compare_import_records',{'left_mapping':left,'right_mapping':right}
            else:
                self.assertNotIn('tools',payload)
                return {'message':{'role':'assistant','content':'물량 불일치와 중복 행은 근거 표에서 확인해야 합니다.'}}
            return {'message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':name,'arguments':args}}]}}
        result=execute_run('reconcile','두 CSV 비교',self.state,inputs,trace.append,chat=chat)
        self.assertEqual(len(requests),4)
        self.assertEqual(sum(r['tool']=='inspect_inputs' for r in trace),1)
        self.assertEqual([row['status'] for row in result['comparison']['rows']],['match','mismatch','duplicate'])
        recorded_requests=[r for r in trace if r['tool']=='ollama.request']
        self.assertEqual([r['args']['options']['num_predict'] for r in recorded_requests],[320,320,320,160])

    def test_document_only_exact_source_no_missing_fabrication(self):
        tools=DomainTools(self.state,{'documentText':'합성 검사 기록\nLot: LOT-007\n수분: 11.9%\n알 수 없는 온도'})
        result=tools.extract_document_fields([{'field':'lotId','value':'LOT-007','source':'Lot: LOT-007'}, {'field':'moisture','value':11.9,'source':'수분: 11.9%'}])
        self.assertIn('temperature',result['missingFields'])
        with self.assertRaises(ValueError): tools.extract_document_fields([{'field':'temperature','value':25,'source':'온도: 25C'}])
        with self.assertRaises(ValueError): tools.extract_document_fields([{'field':'temperature','value':11.9,'source':'수분: 11.9%'}])

    def test_document_actual_string_measurements_and_ledger_sources_are_rejected(self):
        text='SIMULATED DEMO\nLot ID: LOT-007\nMoisture: 12.0 %\nTemperature: 31 C\nInspection Date: 2026-10-07'
        tools=DomainTools(self.state,{'documentText':text})
        for field,value,source in [('moisture','12.0 %','Moisture: 12.0 %'),('temperature','31 C','Temperature: 31 C')]:
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError,'JSON NUMBER without quotes or units'):
                    tools.extract_document_fields([{'field':field,'value':value,'source':source}])
                self.assertIsNone(tools.document)
        with self.assertRaisesRegex(ValueError,'original inspect_inputs.documentText.*Do not use trace_lot'):
            tools.extract_document_fields([{'field':'moisture','value':11.9,'source':'LOT-007 수분 %'}])
        result=tools.extract_document_fields([
            {'field':'lotId','value':'LOT-007','source':'Lot ID: LOT-007'},
            {'field':'moisture','value':12.0,'source':'Moisture: 12.0 %'},
            {'field':'temperature','value':31,'source':'Temperature: 31 C'},
            {'field':'inspectionDate','value':'2026-10-07','source':'Inspection Date: 2026-10-07'},
        ])
        self.assertEqual(result['missingFields'],[])
        self.assertEqual(next(f['value'] for f in result['fields'] if f['field']=='moisture'),12.0)
        self.assertEqual(self.state['lots'][6]['qc']['moisture'],11.9)

    def test_document_after_inspection_requests_extract_with_numeric_json(self):
        requests=[];trace=[]
        text='Lot ID: LOT-007\nMoisture: 12.0 %\nTemperature: 31 C'
        def chat(path,payload):
            requests.append(payload)
            if len(requests)==1: name,args='inspect_inputs',{}
            elif len(requests)==2:
                self.assertIn('inspection already done',payload['messages'][-1]['content'])
                self.assertIn('ORIGINAL inspected documentText',payload['messages'][-1]['content'])
                self.assertNotIn('inspect_inputs then',payload['messages'][-1]['content'])
                self.assertEqual(payload['options']['num_predict'],320)
                self.assertIn('{"field":"moisture","value":12.0',payload['messages'][0]['content'])
                name,args='extract_document_fields',{'fields':[{'field':'lotId','value':'LOT-007','source':'Lot ID: LOT-007'},{'field':'moisture','value':12.0,'source':'Moisture: 12.0 %'},{'field':'temperature','value':31,'source':'Temperature: 31 C'}]}
            else:
                self.assertNotIn('tools',payload)
                return {'message':{'role':'assistant','content':'문서 원문을 추출했으며 확인이 필요합니다.'}}
            return {'message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':name,'arguments':args}}]}}
        result=execute_run('document','문서 추출',self.state,{'documentText':text},trace.append,chat=chat)
        self.assertEqual(len(requests),3)
        self.assertEqual(sum(r['tool']=='inspect_inputs' for r in trace),1)
        self.assertEqual(len(result['document']['fields']),3)

    def test_document_present_english_labels_cannot_be_classified_missing(self):
        text='SIMULATED DEMO\nLot ID: LOT-007\nMoisture: 12.0 %'
        tools=DomainTools(self.state,{'documentText':text})
        moisture={'field':'moisture','value':12,'source':'Moisture: 12.0 %'}
        with self.assertRaisesRegex(ValueError,'omitted explicitly labelled fields: lotId.*Values are not automatically filled'):
            tools.extract_document_fields([moisture])
        self.assertIsNone(tools.document);self.assertFalse(tools.evidence)
        with self.assertRaisesRegex(ValueError,'Omit missing fields; no empty sources'):
            tools.extract_document_fields([moisture,{'field':'temperature','value':0,'source':''}])
        self.assertIsNone(tools.document);self.assertFalse(tools.evidence)
        result=tools.extract_document_fields([{'field':'lotId','value':'LOT-007','source':'Lot ID: LOT-007'},moisture])
        self.assertEqual(result['missingFields'],['temperature','inspectionDate'])
        self.assertFalse(result['semanticLabelVerified'])
        self.assertIn('English line labels only',result['completenessScope'])
        self.assertEqual(len(result['fields']),2)
        # The guard is explicitly scoped, not a universal semantic parser.
        unrelated=DomainTools(self.state,{'documentText':'An essay mentions Lot ID without a labelled value.'})
        self.assertEqual(unrelated.extract_document_fields([])['fields'],[])

    def test_document_omission_retries_native_extraction_without_auto_fill(self):
        text='Lot ID: LOT-007\nMoisture: 12.0 %';requests=[];trace=[]
        moisture={'field':'moisture','value':12,'source':'Moisture: 12.0 %'}
        def chat(path,payload):
            requests.append(payload)
            if len(requests)==1: name,args='inspect_inputs',{}
            elif len(requests)==2: name,args='extract_document_fields',{'fields':[moisture]}
            elif len(requests)==3:
                self.assertIn('omitted explicitly labelled fields: lotId',payload['messages'][-2]['content'])
                name,args='extract_document_fields',{'fields':[{'field':'lotId','value':'LOT-007','source':'Lot ID: LOT-007'},moisture]}
            else:
                self.assertNotIn('tools',payload)
                return {'message':{'role':'assistant','content':'Lot과 수분은 추출했고 온도와 검사일은 문서에 없습니다.'}}
            return {'message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':name,'arguments':args}}]}}
        result=execute_run('document','있는 필드만 추출',self.state,{'documentText':text},trace.append,chat=chat)
        self.assertEqual(len(requests),4)
        self.assertEqual(sum(r['tool']=='extract_document_fields' for r in trace),2)
        self.assertEqual(result['document']['missingFields'],['temperature','inspectionDate'])
        self.assertEqual({f['field'] for f in result['document']['fields']},{'lotId','moisture'})

    def test_branch_mass_and_no_mutation(self):
        before=copy.deepcopy(self.state)
        result=self.tools.simulate_branch([{'lot_id':'LOT-007','action':'reinspect_release','moisture':12,'temperature':25}],60)
        self.assertEqual(result['baseline']['shippedKg'],40000)
        self.assertEqual(result['branch']['shippedKg'],44500)
        self.assertEqual(result['baseline']['totalKg'],result['branch']['totalKg'])
        self.assertEqual(self.state,before);self.assertEqual(self.tools.state,before)

    def test_simulation_semantic_labels_prevent_swaps_and_unlabelled_digits(self):
        args={'assumptions':[{'lot_id':'LOT-007','action':'reinspect_release','moisture':10,'temperature':12}],'virtual_minutes':60}
        question='LOT-007: 수분 10%, 온도 12°C, 가상 시간 60분'
        validate_simulation_request(question,args)
        validate_simulation_request('LOT-007: moisture:10, temperature=12, virtual minutes:60',args)
        swapped=copy.deepcopy(args);swapped['assumptions'][0].update(moisture=12,temperature=10)
        with self.assertRaises(ValueError): validate_simulation_request(question,swapped)
        with self.assertRaises(ValueError): validate_simulation_request('LOT-010 LOT-012 60분',args)
        with self.assertRaises(ValueError): validate_simulation_request('LOT-007 수분10%, 온도12°C',args)
        with self.assertRaises(ValueError): validate_simulation_request(question+' 수분11%',args)
        with self.assertRaises(ValueError): validate_simulation_request(question+' 가상 시간30분',args)
        with self.assertRaises(ValueError): validate_simulation_request(question,{'virtual_minutes':60})
        with self.assertRaises(ValueError): validate_simulation_request('데모 가정',{'assumptions':[],'virtual_minutes':60})
        with self.assertRaises(ValueError): validate_simulation_request('데모 가정',{'assumptions':[{}],'virtual_minutes':60})
        demo={'assumptions':[{'lot_id':'LOT-007','action':'reinspect_release','moisture':12,'temperature':25}],'virtual_minutes':60}
        validate_simulation_request('LOT-007 데모 가정',demo)
        with self.assertRaises(ValueError): validate_simulation_request('LOT-007 데모 가정',args)
        with self.assertRaises(ValueError): validate_simulation_request('LOT-007 데모 가정 수분10%',demo)

    def test_proposal_requires_grounded_target_and_release_rules(self):
        self.tools.trace_lot('LOT-007')
        with self.assertRaises(ValueError): self.tools.prepare_action_proposal('LOT-007','release','검토',['LOT-007-quantity'])
        with self.assertRaises(ValueError): self.tools.prepare_action_proposal('LOT-007','resolve','검토',['FAKE-EVIDENCE'])
        proposal=self.tools.prepare_action_proposal('LOT-007','resolve','사람 재검사 기록 확인',['LOT-007-temperature'])
        self.assertEqual(proposal['alertId'],'ALT-1')
        self.assertEqual(proposal['stateHash'],state_hash(self.state))

    def test_model_proposal_reason_never_prefills_unverified_completed_work(self):
        self.tools.trace_lot('LOT-007')
        hallucination='재검사 후 품질 확인되었음. TRK-02에 영향을 주지 않음. 해제 가능.'
        proposal=self.tools.prepare_action_proposal('LOT-007','resolve',hallucination,['LOT-007-temperature'])
        self.assertNotEqual(proposal['reason'],hallucination)
        self.assertIn('담당자가 확인해야',proposal['reason'])
        self.assertIn('ALT-1',proposal['reason'])
        self.assertEqual(proposal['reasonOrigin'],'verified_tool_template')
        self.assertEqual(proposal['modelSuggestedReason'],hallucination)
        self.assertFalse(proposal['modelSuggestedReasonVerified'])
        self.assertTrue(any('검증되지 않은 초안' in warning for warning in self.tools.result('검토')['warnings']))
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'state.db');store.write(self.state);service=AgentService(store)
            service.proposals[proposal['id']]={**proposal,'consumed':False}
            reviewed=service.review(proposal['id'],'review')
            self.assertEqual(reviewed['reason'],proposal['reason'])
            self.assertNotIn('품질 확인되었음',reviewed['reason'])
            self.assertEqual(store.read()['state'],self.state)

    def test_stale_and_single_use_review_never_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'state.db');store.write(self.state)
            service=AgentService(store)
            self.tools.trace_lot('LOT-007')
            proposal=self.tools.prepare_action_proposal('LOT-007','resolve','검토',['LOT-007-temperature'])
            service.proposals[proposal['id']]={**proposal,'consumed':False}
            service.review(proposal['id'],'review')
            self.assertEqual(store.read()['state'],self.state)
            with self.assertRaises(ValueError): service.review(proposal['id'],'review')
            fresh=self.tools.prepare_action_proposal('LOT-007','resolve','검토',['LOT-007-temperature'])
            service.proposals[fresh['id']]={**fresh,'consumed':False}
            changed=copy.deepcopy(self.state);changed['tick']+=1;store.write(changed)
            with self.assertRaises(ValueError): service.review(fresh['id'],'review')
            self.assertFalse(service.proposals[fresh['id']]['consumed'])

    def test_unobserved_ids_abstain(self):
        self.tools.trace_lot('LOT-007')
        summary,warning=validate_summary('LOT-999 불량입니다',self.tools)
        self.assertIn('보류',summary);self.assertIn('LOT-999',warning)
        summary,warning=validate_summary('LOT-007 4700kg 출하 가능합니다',self.tools)
        self.assertIn('보류',summary);self.assertIn('4700',warning)
        summary,warning=validate_summary('LOT-007의 수량과 RAW-2403의 온도를 확인하세요.',self.tools)
        self.assertIsNone(warning)
        self.assertIn('LOT-007의',summary)

    def test_graph_uses_native_tool_response_and_keeps_state(self):
        requests=[];trace=[]
        def chat(path,payload):
            requests.append(payload)
            if len(requests)==1:
                return {'message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':name,'arguments':args}} for name,args in [('list_blocking_alerts',{}),('trace_lot',{'lot_id':'LOT-007'}),('get_asset_manifest',{'asset_id':'TRK-02'})]]}}
            self.assertNotIn('tools',payload)
            self.assertIn('LOT-007-temperature',payload['messages'][-1]['content'])
            self.assertEqual(payload['options']['num_predict'],160)
            self.assertIn('BELOW its limit is NOT an exceedance',payload['messages'][0]['content'])
            alerts=json.loads(payload['messages'][-1]['content'])['blockingAlerts']
            self.assertEqual(alerts[0]['label'],'보관 온도 기준 초과')
            self.assertIn('31',alerts[0]['value'])
            return {'message':{'role':'assistant','content':'LOT-007의 기록 온도를 사람에게 검토 요청합니다.'}}
        before=copy.deepcopy(self.state)
        result=execute_run('shipment','LOT-007의 기록 확인',self.state,{},trace.append,chat=chat)
        self.assertEqual(self.state,before);self.assertEqual(len(requests),2)
        self.assertTrue(any(t['tool']=='trace_lot' for t in trace))
        self.assertFalse(result['summaryVerified'])
        self.assertTrue(result['evidence'])
        self.assertNotIn('incomplete',result)
        self.assertEqual(result['summary'],'LOT-007의 기록 온도를 사람에게 검토 요청합니다.')

    def test_requested_proposal_requires_actual_native_call_before_synthesis(self):
        requests=[];trace=[]
        def chat(path,payload):
            requests.append(payload)
            calls=[('list_blocking_alerts',{}),('trace_lot',{'lot_id':'LOT-007'}),('get_asset_manifest',{'asset_id':'TRK-02'})]
            if len(requests)==1: return {'message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':name,'arguments':args}} for name,args in calls]}}
            if len(requests)==2: return {'message':{'role':'assistant','content':'검토안을 작성했습니다.'}}
            if len(requests)==3:
                self.assertIn('prepare_action_proposal',payload['messages'][-1]['content'])
                return {'message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':'prepare_action_proposal','arguments':{'lot_id':'LOT-007','action':'resolve','reason':'재검사 완료되었음','evidence_ids':['LOT-007-temperature']}}}]}}
            self.assertNotIn('tools',payload)
            self.assertNotIn('modelSuggestedReason',payload['messages'][-1]['content'])
            return {'message':{'role':'assistant','content':'LOT-007 경보의 재검사 기록을 담당자가 검토해야 합니다.'}}
        result=execute_run('shipment','LOT-007의 검토안도 만들어주세요',self.state,{},trace.append,chat=chat)
        self.assertEqual(len(requests),4)
        self.assertEqual(len(result['proposals']),1)
        self.assertEqual(sum(r['tool']=='prepare_action_proposal' for r in trace),1)
        self.assertEqual(sum(r['tool']=='ollama.synthesis' for r in trace),1)
        self.assertNotIn('완료되었음',result['proposals'][0]['reason'])

    def test_multilot_context_projects_records_without_reducing_trace_or_evidence(self):
        state=json.loads((ROOT/'fixtures/factory-state.json').read_text(encoding='utf-8'))
        tools=DomainTools(state);full=tools.trace_lot('LOT-003');before=copy.deepcopy(full)
        context=shipment_tool_context('trace_lot',full)
        self.assertEqual(set(context),{'lot','assetIds','relatedLots','alerts','evidence'})
        self.assertEqual(set(context['lot']),{'id','rawId','stage','status','quantity','qc'})
        self.assertEqual(context['lot']['qc'],full['lot']['qc'])
        self.assertEqual(context['relatedLots'],full['relatedLots']);self.assertEqual(context['assetIds'],full['assetIds'])
        self.assertEqual(context['evidence'],[{k:e[k] for k in ['id','label','value']} for e in full['evidence']])
        self.assertEqual(context['alerts'],[{k:a[k] for k in ['id','lotId','title','details','resolved','blocking','shippedImpact']} for a in full['alerts']])
        self.assertEqual(full,before);self.assertIn('progress',full['lot']);self.assertIn('product',full['lot'])
        full=tools.list_blocking_alerts();before=copy.deepcopy(full);context=shipment_tool_context('list_blocking_alerts',full)
        self.assertEqual(set(context['report']),{'totalKg','shippedKg','heldKg','activeKg','unresolved','shippedImpactKg'})
        self.assertEqual(context['report']['heldKg'],8400);self.assertEqual(context['report']['shippedImpactKg'],4000)
        self.assertEqual(full,before);self.assertIn('byStage',full['report']);self.assertIn('affectedLots',full['report'])
        self.assertEqual(context['evidence'],[{k:e[k] for k in ['id','label','value']} for e in full['evidence']])
        error={'error':'unknown','requiresCorrectionOrAbstention':True}
        self.assertEqual(shipment_tool_context('trace_lot',error),error)

    def test_multilot_graph_requires_all_traces_and_proposals_with_full_api_records(self):
        state=json.loads((ROOT/'fixtures/factory-state.json').read_text(encoding='utf-8'));before=copy.deepcopy(state)
        requests=[];trace=[]
        def response(calls):return {'message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':name,'arguments':args}} for name,args in calls]}}
        def chat(path,payload):
            requests.append(copy.deepcopy(payload))
            self.assertEqual(payload['options']['num_predict'],768 if 'tools' in payload else 320)
            if len(requests)==1:return response([('list_blocking_alerts',{}),('trace_lot',{'lot_id':'LOT-003'})])
            if len(requests)==2:
                outgoing=[m for m in payload['messages'] if m['role']=='tool']
                for message in outgoing:
                    recorded=next(r['result'] for r in trace if r['tool']==message['tool_name'])
                    self.assertEqual(json.loads(message['content']),shipment_tool_context(message['tool_name'],recorded))
                self.assertIn('LOT-001',payload['messages'][-1]['content']);self.assertIn('LOT-009',payload['messages'][-1]['content'])
                self.assertEqual(payload['options']['num_ctx'],4096)
                return response([('trace_lot',{'lot_id':'LOT-001'}),('trace_lot',{'lot_id':'LOT-009'})])
            if len(requests)==3:
                self.assertIn('prepare_action_proposal',payload['messages'][-1]['content'])
                self.assertNotIn('release',{tool['function']['name'] for tool in payload['tools']})
                return response([('prepare_action_proposal',{'lot_id':lot,'action':'resolve','reason':'담당자 검토','evidence_ids':[lot+'-moisture']}) for lot in ['LOT-001','LOT-003','LOT-009']])
            self.assertNotIn('tools',payload)
            return {'message':{'role':'assistant','content':'RAW-2401에 연결된 LOT-001, LOT-003, LOT-009의 저장 경보를 담당자가 검토해야 합니다.'}}
        result=execute_run('shipment','LOT-003의 보류 이유와 RAW-2401 관련 LOT-001 LOT-009를 조회하고 검토안을 만들어주세요',state,{},trace.append,chat=chat)
        self.assertEqual(len(requests),4);self.assertEqual(state,before)
        self.assertEqual({r['args']['lot_id'] for r in trace if r['tool']=='trace_lot'},{'LOT-001','LOT-003','LOT-009'})
        self.assertEqual({p['lotId'] for p in result['proposals']},{'LOT-001','LOT-003','LOT-009'})
        self.assertEqual({e['id'] for e in result['evidence'] if e['id'].endswith('-moisture')},{'LOT-001-moisture','LOT-003-moisture','LOT-009-moisture'})
        for record in [r for r in trace if r['tool']=='trace_lot']:
            self.assertIn('product',record['result']['lot']);self.assertIn('progress',record['result']['lot'])
        self.assertIn('byStage',next(r['result']['report'] for r in trace if r['tool']=='list_blocking_alerts'))

    def test_singlelot_outgoing_tool_context_is_unchanged(self):
        requests=[];trace=[]
        def chat(path,payload):
            requests.append(payload)
            self.assertEqual(payload['options']['num_predict'],225 if 'tools' in payload else 160)
            if len(requests)==1:return {'message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':'list_blocking_alerts','arguments':{}}}]}}
            if len(requests)==2:
                message=next(m for m in payload['messages'] if m['role']=='tool')
                recorded=next(r['result'] for r in trace if r['tool']=='list_blocking_alerts')
                self.assertEqual(message['content'],json.dumps(recorded,ensure_ascii=False))
                self.assertIn('byStage',json.loads(message['content'])['report'])
                return {'message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':'trace_lot','arguments':{'lot_id':'LOT-007'}}},{'function':{'name':'get_asset_manifest','arguments':{'asset_id':'TRK-02'}}}]}}
            return {'message':{'role':'assistant','content':'LOT-007의 저장 경보를 검토해야 합니다.'}}
        execute_run('shipment','LOT-007 상태',self.state,{},trace.append,chat=chat)

    def test_ollama_http_error_retains_only_bounded_json_error_detail(self):
        body=json.dumps({'error':'failed to parse native tool JSON\n'+('x'*1000),'prompt':'must not be exposed','other':'unrelated'}).encode()
        error=HTTPError('http://127.0.0.1:11434/api/chat',500,'Internal Server Error',{},io.BytesIO(body))
        with patch('agent_backend.urlopen',side_effect=error):
            with self.assertRaises(HTTPError) as raised:ollama_json('/api/chat',{'messages':[]})
        detail=str(raised.exception)
        self.assertEqual(raised.exception.code,500);self.assertIn('failed to parse native tool JSON',detail)
        self.assertNotIn('must not be exposed',detail);self.assertNotIn('unrelated',detail);self.assertNotIn('\n',detail)
        self.assertLess(len(detail),450)
        error=HTTPError('http://127.0.0.1:11434/api/chat',500,'Internal Server Error',{},io.BytesIO(b'<html>private arbitrary server response</html>'))
        with patch('agent_backend.urlopen',side_effect=error):
            with self.assertRaises(HTTPError) as raised:ollama_json('/api/chat')
        self.assertNotIn('private arbitrary',str(raised.exception));self.assertEqual(raised.exception.code,500)

    def test_adjacent_explicit_proposal_request_preserves_generic_multi_lot_scope(self):
        question='LOT-003이 왜 보류되었나요? RAW-2401 관련 LOT-001 LOT-009도 조회하고 LOT-003의 재검사 검토안도 만들어 주세요.'
        self.assertEqual(explicit_proposal_requests(question),{'LOT-003':'resolve'})
        self.assertEqual(explicit_proposal_requests('LOT-007의 검토안도 만들어주세요'),{'LOT-007':None})
        self.assertEqual(explicit_proposal_requests('LOT-003의 보류 이유와 LOT-001 LOT-009를 조회하고 검토안을 만들어주세요'),{})

    def test_explicit_reinspection_review_requires_only_target_resolve_and_all_lot_traces(self):
        state=json.loads((ROOT/'fixtures/factory-state.json').read_text(encoding='utf-8'));before=copy.deepcopy(state)
        requests=[];trace=[]
        def response(calls):return {'message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':name,'arguments':args}} for name,args in calls]}}
        def proposal(lot,action,evidence):return ('prepare_action_proposal',{'lot_id':lot,'action':action,'reason':'재검사 기록 담당자 검토','evidence_ids':[evidence]})
        def chat(path,payload):
            requests.append(copy.deepcopy(payload))
            self.assertEqual(payload['options']['num_predict'],768 if 'tools' in payload else 320)
            if len(requests)==1:
                source=json.loads(payload['messages'][-1]['content'])
                self.assertEqual(source['explicitProposalRequests'],[{'lotId':'LOT-003','action':'resolve'}])
                self.assertIn('ONLY draft those targets',payload['messages'][0]['content'])
                return response([('list_blocking_alerts',{}),('trace_lot',{'lot_id':'LOT-003'})])
            if len(requests)==2:
                self.assertIn('trace_lot for LOT-001,LOT-009',payload['messages'][-1]['content'])
                return response([('trace_lot',{'lot_id':'LOT-001'}),('trace_lot',{'lot_id':'LOT-009'}),proposal('LOT-003','hold','LOT-003-moisture'),proposal('LOT-009','resolve','LOT-009-moisture'),proposal('LOT-003','resolve','TOTAL-shippedImpactKg')])
            if len(requests)==3:
                self.assertIn('lot_id=LOT-003 action=resolve',payload['messages'][-1]['content'])
                messages=[json.loads(m['content']) for m in payload['messages'] if m['role']=='tool' and m['tool_name']=='prepare_action_proposal']
                self.assertIn('requires action=resolve',messages[0]['error'])
                self.assertIn('outside explicit user target',messages[1]['error'])
                self.assertIn('Only observed evidence IDs allowed',messages[2]['error'])
                return response([proposal('LOT-003','resolve','LOT-003-moisture')])
            self.assertNotIn('tools',payload)
            return {'message':{'role':'assistant','content':'LOT-003의 재검사 검토안은 담당자 확인이 필요합니다. LOT-001과 LOT-009는 관련 기록으로 조회했습니다.'}}
        question='LOT-003이 왜 보류되었나요? RAW-2401 관련 LOT-001 LOT-009도 조회하고 LOT-003의 재검사 검토안도 만들어 주세요.'
        result=execute_run('shipment',question,state,{},trace.append,chat=chat)
        self.assertEqual(len(requests),4);self.assertEqual(state,before)
        self.assertEqual({r['args']['lot_id'] for r in trace if r['tool']=='trace_lot'},{'LOT-001','LOT-003','LOT-009'})
        self.assertEqual([(p['lotId'],p['action']) for p in result['proposals']],[('LOT-003','resolve')])
        self.assertEqual(result['proposals'][0]['evidenceIds'],['LOT-003-moisture'])
        self.assertTrue(all(r['options']['num_ctx']==4096 for r in requests))

    def test_multilot_summary_retains_shipped_stage_separate_mass_and_exact_sources(self):
        state=json.loads((ROOT/'fixtures/factory-state.json').read_text(encoding='utf-8'))
        state['lots'][0]['quantity']=5143;state['lots'][8]['quantity']=4017
        before=copy.deepcopy(state);requests=[];trace=[]
        def chat(path,payload):
            requests.append(copy.deepcopy(payload))
            if len(requests)==1:
                calls=[('list_blocking_alerts',{})]+[('trace_lot',{'lot_id':lot}) for lot in ['LOT-001','LOT-003','LOT-009']]+[('prepare_action_proposal',{'lot_id':'LOT-003','action':'resolve','reason':'담당자 재검사 검토','evidence_ids':['ALT-2','LOT-003-moisture']})]
                return {'message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':name,'arguments':args}} for name,args in calls]}}
            self.assertNotIn('tools',payload);self.assertEqual(payload['options']['num_predict'],320)
            context=json.loads(payload['messages'][-1]['content']);mass=context['shipmentMass']
            listed=next(r['result'] for r in trace if r['tool']=='list_blocking_alerts');source=listed['report']
            self.assertEqual(mass['unit'],'kg');self.assertEqual(mass['heldKg'],source['heldKg'])
            self.assertEqual(mass['alreadyShippedImpactKg'],sum(l['kg'] for l in source['affectedLots'] if l['shipped']))
            self.assertEqual(mass['affectedLots'],source['affectedLots'])
            self.assertEqual(mass['heldSource'],{'tool':'list_blocking_alerts','field':'report.heldKg','evidenceIds':['TOTAL-heldKg']})
            self.assertEqual(mass['alreadyShippedImpactSource']['evidenceIds'],['LOT-009-quantity','LOT-009-stage'])
            self.assertEqual(mass['alreadyShippedImpactSource']['field'],'sum report.affectedLots kg where shipped=true')
            expected=[]
            for r in [r for r in trace if r['tool']=='trace_lot']:
                lot=r['result']['lot'];expected.append({**{k:lot[k] for k in ['id','rawId','stage','status','quantity','qc']},'quantityUnit':'kg','sourceTool':'trace_lot','evidenceIds':[e['id'] for e in r['result']['evidence'] if e.get('lotId')==lot['id']]})
            self.assertEqual(context['requestedLots'],expected)
            shipped=next(l for l in context['requestedLots'] if l['id']=='LOT-009')
            self.assertEqual(shipped['stage'],'shipped');self.assertEqual(shipped['status'],'warning');self.assertEqual(shipped['quantity'],4017)
            self.assertEqual(context['blockingAlerts'],[{**{k:a[k] for k in ['id','lotId','type','title','details','blocking','shippedImpact']},'evidenceIds':[a['id']]} for a in listed['alerts']])
            self.assertEqual(context['criteria'],[{'id':'SETTING-moistureLimit','label':'데모 수분 기준 %','value':state['settings']['moistureLimit']}])
            self.assertNotIn('facts',context)
            self.assertEqual([(p['lotId'],p['action']) for p in context['proposals']],[('LOT-003','resolve')])
            self.assertNotIn('modelSuggestedReason',context['proposals'][0])
            self.assertIn('must NEVER be counted as held',payload['messages'][0]['content']);self.assertIn('Do not add the two totals',payload['messages'][0]['content'])
            self.assertIn('Sentence 1 explains the relevant requested-Lot cause using the supplied measurement, limit and alert ID',payload['messages'][0]['content'])
            self.assertIn('Sentence 2 MUST report both shipmentMass.heldKg kg as unshipped hold and shipmentMass.alreadyShippedImpactKg kg as separate already-shipped impact',payload['messages'][0]['content'])
            self.assertIn('identify the shipped Lot ID from the source when present',payload['messages'][0]['content'])
            self.assertIn('state pending human proposal review if proposals are supplied',payload['messages'][0]['content'])
            self.assertIn('Do not waste either sentence on metadata, no-speculation self-description',payload['messages'][0]['content'])
            self.assertIn('AGGREGATE held total across all unshipped held Lots',payload['messages'][0]['content'])
            self.assertIn('Do not attach a single held Lot ID or any per-Lot attribution to that aggregate',payload['messages'][0]['content'])
            self.assertIn('do not list held Lot IDs in sentence 2',payload['messages'][0]['content'])
            self.assertIn('END sentence 2 with a short clause explicitly stating that the proposal still requires human review',payload['messages'][0]['content'])
            self.assertIn('do not spend that final clause restating the shipped quantity source',payload['messages'][0]['content'])
            return {'message':{'role':'assistant','content':f"미출하 보류 {mass['heldKg']}kg과 이미 출하된 LOT-009의 영향 {mass['alreadyShippedImpactKg']}kg을 별도로 확인해야 합니다. LOT-003 재검사 검토안은 담당자 확인이 필요합니다."}}
        question='LOT-001 LOT-009도 조회하고 LOT-003의 재검사 검토안도 만들어 주세요.'
        result=execute_run('shipment',question,state,{},trace.append,chat=chat)
        self.assertEqual(len(requests),2);self.assertEqual(state,before);self.assertFalse(result['summaryVerified'])
        self.assertTrue(any(e['id']=='LOT-009-stage' and e['value']=='shipped' for e in result['evidence']))

    def test_no_block_and_unknown_id_do_not_require_unrelated_hidden_tools(self):
        clear=copy.deepcopy(self.state);clear['alerts']=[];clear['lots'][6]['status']='ok';clear['lots'][6]['qc']['temperature']=25
        for state,question,tool,args in [(clear,'현재 차단 상태','list_blocking_alerts',{}),(self.state,'LOT-999의 상태','trace_lot',{'lot_id':'LOT-999'})]:
            requests=[];trace=[]
            def chat(path,payload):
                requests.append(payload)
                if len(requests)==1: return {'message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':tool,'arguments':args}}]}}
                self.assertNotIn('tools',payload)
                return {'message':{'role':'assistant','content':'저장된 기록 범위에서 확인했습니다.'}}
            result=execute_run('shipment',question,state,{},trace.append,chat=chat)
            self.assertEqual(len(requests),2)
            self.assertEqual([r['tool'] for r in trace if not r['tool'].startswith('ollama.')],[tool])
            if tool=='trace_lot': self.assertTrue(result['incomplete']);self.assertFalse(result['evidence'])

    def test_handover_history_and_missing_simulation_inputs(self):
        requests=[]
        def history_chat(path,payload):
            requests.append(payload)
            if len(requests)==1: return {'message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':'get_action_history','arguments':{'lot_id':'LOT-007'}}}]}}
            self.assertNotIn('tools',payload)
            return {'message':{'role':'assistant','content':'LOT-007의 저장된 경보 기록을 인수인계합니다.'}}
        result=execute_run('handover','LOT-007 인수인계',self.state,{},lambda _:None,chat=history_chat)
        self.assertTrue(result['evidence']);self.assertEqual(len(requests),2)
        requests=[]
        def missing_chat(path,payload):
            requests.append(payload)
            if len(requests)==1:
                self.assertIn('EVEN WHEN reinspection assumptions are unknown',payload['messages'][0]['content'])
                self.assertIn('Do not answer directly before this actual tool call',payload['messages'][0]['content'])
                return {'message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':'trace_lot','arguments':{'lot_id':'LOT-007'}}}]}}
            return {'message':{'role':'assistant','content':'가정할 수분과 온도, 가상 시간을 알려주세요.'}}
        result=execute_run('simulate','LOT-007 비교',self.state,{},lambda _:None,chat=missing_chat)
        self.assertEqual(len(requests),2);self.assertNotIn('simulation',result)

    def test_simulation_synthesis_uses_totals_only_and_abstains_when_truncated(self):
        requests=[];trace=[]
        def chat(path,payload):
            requests.append(payload)
            if len(requests)==1:
                return {'message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':'trace_lot','arguments':{'lot_id':'LOT-007'}}},{'function':{'name':'simulate_branch','arguments':{'assumptions':[{'lot_id':'LOT-007','action':'reinspect_release','moisture':12,'temperature':25}],'virtual_minutes':60}}}]}}
            self.assertNotIn('tools',payload)
            self.assertIn('Do NOT compare QC thresholds',payload['messages'][0]['content'])
            source=json.loads(payload['messages'][-1]['content'])
            self.assertNotIn('facts',source)
            self.assertEqual(source['simulation']['baseline']['shippedKg'],40000)
            self.assertEqual(source['simulation']['branch']['shippedKg'],44500)
            return {'done_reason':'length','message':{'role':'assistant','content':'25°C가28°C초과로 해제는 불'}}
        before=copy.deepcopy(self.state)
        result=execute_run('simulate','LOT-007 데모 가정',self.state,{},trace.append,chat=chat)
        self.assertTrue(result['narrativeTruncated'])
        self.assertIn('길이 한도로',result['summary'])
        self.assertNotIn('25°C가28°C초과',result['summary'])
        self.assertEqual(result['simulation']['branch']['shippedKg'],44500)
        self.assertTrue(result['facts']);self.assertEqual(self.state,before)
        self.assertTrue(any('종료 사유가 length' in warning for warning in result['warnings']))

    def test_missing_outputs_and_tool_limits_fail_within_bounds(self):
        requests=[]
        def never_ready(path,payload):
            requests.append(payload)
            return {'message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':'invented_tool','arguments':{}}}]}}
        with self.assertRaisesRegex(ValueError,'bounded tool rounds'): execute_run('shipment','검토안',self.state,{},lambda _:None,chat=never_ready)
        self.assertEqual(len(requests),6)
        requests=[]
        def excessive(path,payload):
            requests.append(payload)
            return {'message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':'invented_tool','arguments':{}}} for _ in range(8)]}}
        with self.assertRaisesRegex(ValueError,'Tool call limit'): execute_run('shipment','검토안',self.state,{},lambda _:None,chat=excessive)
        self.assertEqual(len(requests),2)

    def test_model_no_tools_or_failure_never_fake_completion(self):
        with self.assertRaises(ValueError): execute_run('shipment','검사',self.state,{},lambda _:None,chat=lambda *a:{'message':{'role':'assistant','content':'문제 없음'}})
        with self.assertRaises(OSError): execute_run('shipment','검사',self.state,{},lambda _:None,chat=lambda *a:(_ for _ in ()).throw(OSError('model stopped')))
        with self.assertRaisesRegex(ValueError,'Model made no actual tool call'):
            execute_run('simulate','LOT-007 값은 아직 모릅니다',self.state,{},lambda _:None,chat=lambda *a:{'message':{'role':'assistant','content':'가정이 필요합니다.'}})

    def test_client_snapshot_is_never_trusted(self):
        with tempfile.TemporaryDirectory() as tmp:
            service=AgentService(Store(Path(tmp)/'state.db'))
            with self.assertRaises(ValueError): service.submit({'task':'shipment','question':'확인','state':self.state})

    def test_rag_service_accepts_bounded_inputs_and_checks_embedding_availability(self):
        class Saved:
            def read(self):return {'state':copy.deepcopy(self.state)}
        store=Saved();store.state=self.state
        with patch.object(AgentService,'worker',lambda _:None), patch('agent_backend.backend_status',return_value={'available':True}), patch('rag_backend.rag_status',return_value={'available':True}):
            service=AgentService(store)
            run=service.submit({'task':'rag','question':'수분 기준','inputs':{'ragEnabled':True,'corpus':'baseline'}})
            self.assertEqual(service.get(run)['status'],'queued')
            for inputs in [{'ragEnabled':'false'},{'corpus':'actual-company'},{'documentText':'unbounded'}]:
                with self.assertRaises(ValueError):service.submit({'task':'rag','question':'수분 기준','inputs':inputs})
        with patch.object(AgentService,'worker',lambda _:None), patch('agent_backend.backend_status',return_value={'available':True}), patch('rag_backend.rag_status',return_value={'available':False,'detail':'Embedding model missing; no fallback'}):
            service=AgentService(store)
            with self.assertRaisesRegex(RuntimeError,'Embedding model missing'):service.submit({'task':'rag','question':'수분 기준'})
            self.assertTrue(service.submit({'task':'rag','question':'수분 기준','inputs':{'ragEnabled':False}}))


if __name__=='__main__': unittest.main(verbosity=2)
