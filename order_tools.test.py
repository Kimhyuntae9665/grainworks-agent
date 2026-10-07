"""Shared order arithmetic and actual native-tool graph contract, mocked transport."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from agent_backend import AgentService, execute_run, validate_summary
from domain_tools import DomainTools, state_hash, TOOL_SCHEMAS
from order_tools import compact_orders, requested_orders, validate_order_request
from server import Store

ROOT=Path(__file__).resolve().parent


def order_state():
    state=json.loads((ROOT/'fixtures/demo-state.json').read_text(encoding='utf-8'))
    state['alerts']=[]
    state['lots']=state['lots'][:4]
    for lot,stage,status in zip(state['lots'],['warehouse','warehouse','packing','shipped'],['ok','hold','ok','warning']):
        lot.update(quantity=200,product='합성 제품',stage=stage,status=status)
        lot['qc'].update(moisture=12,temperature=24,stale=False)
    state['orders']=[{'id':'SO-001','customer':'합성 거래처','dueTick':60,'lines':[{'id':'SO-001-L1','product':'합성 제품','requestedKg':1000,
        'allocations':[{'lotId':lot['id'],'quantityKg':200,'assetId':'TRK-02'} for lot in state['lots']]}]}]
    state['alerts']=[{'id':'ALT-001','lotId':'LOT-002','resolved':False,'blocking':True,'title':'합성 차단 기록','details':'담당자 확인 대기'}]
    return state


def native(name,args):
    return {'message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':name,'arguments':args}}]}}


class OrderTests(unittest.TestCase):
    def setUp(self): self.state=order_state()

    def test_tools_expose_disjoint_quantities_and_preserve_original(self):
        before=copy.deepcopy(self.state);tools=DomainTools(self.state)
        result=tools.call('get_order_status',{'order_id':'SO-001'})
        order=result['orders'][0];line=order['lines'][0]
        self.assertEqual(order['requestedKg'],1000);self.assertEqual(order['allocatedKg'],800)
        for key in ['shippedKg','readyKg','heldKg','workInProgressKg','unallocatedKg','shippedImpactKg']:
            self.assertEqual(order[key],200)
        self.assertEqual(order['remainingKg'],800);self.assertEqual(order['notReadyKg'],600)
        self.assertEqual(sum(order[k] for k in ['shippedKg','readyKg','heldKg','workInProgressKg','unallocatedKg']),1000)
        self.assertEqual(line['linkedAssetIds'],['TRK-02'])
        self.assertEqual(result['blockingAlerts'][0]['id'],'ALT-001')
        self.assertTrue(any(e.get('lotId')=='LOT-002' for e in result['evidence']))
        self.assertIn('orders',tools.result(''))
        self.assertEqual(self.state,before);self.assertEqual(tools.state,before)
        self.assertEqual(tools.hash,state_hash(before));self.assertFalse(tools.proposals)

    def test_all_orders_and_legacy_read_dont_infer_or_seed(self):
        self.assertEqual(len(DomainTools(self.state).get_order_status()['orders']),1)
        legacy=copy.deepcopy(self.state);legacy.pop('orders')
        tools=DomainTools(legacy)
        self.assertEqual(tools.get_order_status()['orders'],[])
        self.assertNotIn('orders',tools.state)
        with self.assertRaises(ValueError):tools.get_order_status('SO-001')
        with self.assertRaises(ValueError):tools.get_order_status('LOT-001')

    def test_explicit_order_binding_has_no_all_order_or_other_order_substitute(self):
        self.assertEqual(requested_orders('SO-001와 SO-099 주문'),{'SO-001','SO-099'})
        self.assertEqual(requested_orders('SO-001 주문 SO-001-L1 행'),{'SO-001'})
        validate_order_request('SO-001 주문',{'order_id':'SO-001'})
        for args in [{},{'order_id':'SO-002'},{'order_id':'LOT-001'},{'order_id':'SO-001','release':True}]:
            with self.assertRaises(ValueError):validate_order_request('SO-001 주문',args)
        validate_order_request('전체 주문',{})

    def test_native_order_tool_required_before_summary_and_compact_context(self):
        requests=[];trace=[];before=copy.deepcopy(self.state)
        def chat(path,payload):
            requests.append(payload)
            if len(requests)==1:
                self.assertEqual({t['function']['name'] for t in payload['tools']},{'get_order_status','trace_lot','list_blocking_alerts','get_asset_manifest'})
                return native('trace_lot',{'lot_id':'LOT-001'})
            if len(requests)==2:return {'message':{'role':'assistant','content':'주문 확인 완료.'}}
            if len(requests)==3:
                self.assertIn('get_order_status for exact SO-001',payload['messages'][-1]['content'])
                return native('get_order_status',{'order_id':'SO-001'})
            self.assertNotIn('tools',payload)
            context=json.loads(payload['messages'][-1]['content'])
            row=context['orderReads'][0]['orders'][0]
            self.assertEqual(row['id'],'SO-001');self.assertEqual(row['lines'][0]['id'],'SO-001-L1')
            self.assertEqual(row['lines'][0]['allocations'][0]['quantityKg'],200)
            self.assertNotIn('qc',row['lines'][0]['allocations'][0]);self.assertNotIn('evidence',context['orderReads'][0])
            self.assertEqual(row['heldKg'],200);self.assertEqual(row['shippedImpactKg'],200)
            return {'message':{'role':'assistant','content':'SO-001의 SO-001-L1 출하 기록은 200 kg이고 창고 준비량은 200 kg입니다.'}}
        result=execute_run('orders','SO-001 주문 상태',self.state,{},trace.append,chat=chat)
        self.assertEqual(len(requests),4);self.assertIn('orders',result)
        self.assertFalse(result['proposals']);self.assertEqual(self.state,before)
        self.assertTrue(any(r['tool']=='get_order_status' and 'error' not in r['result'] for r in trace))
        self.assertFalse(any('Grounding rejected' in w for w in result['warnings']))

    def test_unknown_explicit_order_requires_exact_read_then_abstains(self):
        requests=[];trace=[]
        def chat(path,payload):
            requests.append(payload)
            if len(requests)==1:return native('get_order_status',{})
            if len(requests)==2:return native('get_order_status',{'order_id':'SO-999'})
            return {'message':{'role':'assistant','content':'해당 주문 자료가 없습니다.'}}
        result=execute_run('orders','SO-999 주문 확인',self.state,{},trace.append,chat=chat)
        attempts=[r for r in trace if r['tool']=='get_order_status']
        self.assertIn('explicitly requested',attempts[0]['result']['error'])
        self.assertIn('error',attempts[1]['result'])
        self.assertTrue(result['incomplete']);self.assertIn('SO-999',result['summary'])
        self.assertFalse(result['evidence']);self.assertNotIn('orders',result)

    def test_each_explicit_order_requires_its_native_read_and_aggregate_has_no_duplicates(self):
        self.state['orders'].append({'id':'SO-002','customer':'합성 거래처 B','dueTick':60,
            'lines':[{'id':'SO-002-L1','product':'합성 제품','requestedKg':100,'allocations':[]}]})
        requests=[]
        def chat(path,payload):
            requests.append(payload)
            if len(requests)==1:return native('get_order_status',{'order_id':'SO-001'})
            if len(requests)==2:
                self.assertIn('get_order_status for exact SO-002',payload['messages'][-1]['content'])
                return native('get_order_status',{'order_id':'SO-002'})
            return {'message':{'role':'assistant','content':'SO-001과 SO-002 주문의 실제 도구 조회 결과입니다.'}}
        result=execute_run('orders','SO-001과 SO-002 주문',self.state,{},lambda _:None,chat=chat)
        self.assertEqual(len(requests),3)
        self.assertEqual([o['id'] for o in result['orders']['orders']],['SO-001','SO-002'])
        self.assertEqual(result['orders']['totals']['requestedKg'],1100)
        self.assertEqual(result['orders']['totals']['unallocatedKg'],300)

    def test_order_task_rejects_mutation_proposal_and_no_native_call(self):
        calls=[]
        def chat(path,payload):
            calls.append(payload)
            if len(calls)==1:return native('prepare_action_proposal',{'lot_id':'LOT-002','action':'resolve','reason':'x','evidence_ids':[]})
            if len(calls)==2:return native('get_order_status',{'order_id':'SO-001'})
            return {'message':{'role':'assistant','content':'SO-001 주문 조회 결과입니다.'}}
        trace=[];result=execute_run('orders','SO-001 검토안',self.state,{},trace.append,chat=chat)
        self.assertIn('not allowed',trace[2]['result']['error']);self.assertFalse(result['proposals'])
        with self.assertRaisesRegex(ValueError,'no actual tool call'):
            execute_run('orders','SO-001 조회',self.state,{},lambda _:None,chat=lambda *_:{'message':{'role':'assistant','content':'조회 완료'}})

    def test_order_line_ids_and_quantities_are_grounded(self):
        tools=DomainTools(self.state);tools.get_order_status('SO-001')
        self.assertIsNone(validate_summary('SO-001 / SO-001-L1 준비 200 kg',tools)[1])
        for prose in ['SO-999 주문','SO-00 주문','SO-001-L9 주문행','SO-001 준비 12345 kg']:
            self.assertIn('Grounding rejected',validate_summary(prose,tools)[1])

    def test_compact_context_separates_current_hold_from_shipped_impact_ids(self):
        tools=DomainTools(self.state);full=tools.get_order_status('SO-001');before=copy.deepcopy(full)
        context=compact_orders(full);order=context['orders'][0]
        self.assertEqual(order['heldLotIds'],['LOT-002'])
        self.assertEqual(order['shippedLotIds'],['LOT-004'])
        self.assertEqual(order['shippedImpactLotIds'],['LOT-004'])
        self.assertEqual(order['readyLotIds'],['LOT-001'])
        self.assertEqual(order['workInProgressLotIds'],['LOT-003'])
        self.assertFalse(set(order['heldLotIds']) & set(order['shippedImpactLotIds']))
        self.assertEqual(context['blockingAlerts'][0]['allocationStatus'],'held')
        allocation=order['lines'][0]['allocations'][3]
        self.assertEqual(allocation['stage'],'shipped');self.assertEqual(allocation['status'],'shipped')
        self.assertEqual(allocation['quantityKg'],200);self.assertTrue(allocation['shippedImpact'])
        self.assertNotIn('evidence',context);self.assertEqual(full,before)
        self.assertLess(len(json.dumps(context,ensure_ascii=False)),5000)

    def test_allocation_change_hash_invalidates_prior_proposal(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'orders.db');store.write(self.state)
            tools=DomainTools(self.state);tools.trace_lot('LOT-002')
            proposal=tools.prepare_action_proposal('LOT-002','resolve','검토',['LOT-002-status'])
            service=AgentService(store);service.proposals[proposal['id']]={**proposal,'consumed':False}
            changed=copy.deepcopy(self.state);changed['orders'][0]['lines'][0]['allocations'][0]['quantityKg']=100
            store.write(changed)
            with self.assertRaisesRegex(ValueError,'Stale proposal'):service.review(proposal['id'],'review')
            self.assertEqual(store.read()['state'],changed)

    def test_service_order_input_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'orders.db');store.write(self.state);service=AgentService(store)
            with self.assertRaises(ValueError):service.submit({'task':'orders','question':'조회','inputs':{'orderId':'SO-001'}})
            with patch('agent_backend.backend_status',return_value={'available':False,'detail':'unavailable'}):
                with self.assertRaises(RuntimeError):service.submit({'task':'orders','question':'SO-001 조회'})


if __name__=='__main__':unittest.main(verbosity=2)
