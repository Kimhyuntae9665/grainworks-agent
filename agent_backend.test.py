"""Safety/grounding checks; mocked transport tests workflow, not model accuracy."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from agent_backend import AgentService, execute_run, validate_summary, validate_simulation_request
from domain_tools import DomainTools, state_hash
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

    def test_document_only_exact_source_no_missing_fabrication(self):
        tools=DomainTools(self.state,{'documentText':'합성 검사 기록\nLot: LOT-007\n수분: 11.9%\n알 수 없는 온도'})
        result=tools.extract_document_fields([{'field':'lotId','value':'LOT-007','source':'Lot: LOT-007'}, {'field':'moisture','value':11.9,'source':'수분: 11.9%'}])
        self.assertIn('temperature',result['missingFields'])
        with self.assertRaises(ValueError): tools.extract_document_fields([{'field':'temperature','value':25,'source':'온도: 25C'}])
        with self.assertRaises(ValueError): tools.extract_document_fields([{'field':'temperature','value':11.9,'source':'수분: 11.9%'}])

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
            if len(requests)==1: return {'message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':'trace_lot','arguments':{'lot_id':'LOT-007'}}}]}}
            return {'message':{'role':'assistant','content':'가정할 수분과 온도, 가상 시간을 알려주세요.'}}
        result=execute_run('simulate','LOT-007 비교',self.state,{},lambda _:None,chat=missing_chat)
        self.assertEqual(len(requests),2);self.assertNotIn('simulation',result)

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

    def test_client_snapshot_is_never_trusted(self):
        with tempfile.TemporaryDirectory() as tmp:
            service=AgentService(Store(Path(tmp)/'state.db'))
            with self.assertRaises(ValueError): service.submit({'task':'shipment','question':'확인','state':self.state})


if __name__=='__main__': unittest.main(verbosity=2)
