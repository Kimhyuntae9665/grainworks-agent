"""Factory read-only domain and native graph contract tests, no model accuracy claim."""
import copy
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from domain_tools import DomainTools, TOOL_SCHEMAS
from factory_tools import request_values, validate_request
from agent_backend import AgentService, execute_run, validate_summary
from server import Store

ROOT=Path(__file__).resolve().parent


class FactoryTests(unittest.TestCase):
    def setUp(self):
        self.state=json.loads((ROOT/'fixtures/demo-state.json').read_text(encoding='utf-8'))
        self.inputs={'stationId':'MIX-01','downtimeMinutes':10,'horizonMinutes':15}
        self.args={'station_id':'MIX-01','downtime_minutes':10,'horizon_minutes':15}

    def test_tools_read_shared_js_model_and_keep_original(self):
        before=copy.deepcopy(self.state);tools=DomainTools(self.state)
        result=tools.call('get_factory_status',{'station_id':'MIX-01'})
        self.assertEqual(result['stations'][0]['id'],'MIX-01')
        result=tools.call('compare_factory_downtime',self.args)
        self.assertTrue(result['massConserved']);self.assertTrue(result['originalUnchanged'])
        self.assertLessEqual(result['branch']['throughputKg'],result['baseline']['throughputKg'])
        self.assertIn('FACTORY-SIMULATION',tools.evidence)
        self.assertEqual(self.state,before);self.assertEqual(tools.state,before)
        self.assertIn('factorySimulation',tools.result(''))
        self.assertNotIn('release',{s['function']['name'] for s in TOOL_SCHEMAS})

    def test_explicit_inputs_are_bound_without_defaults_or_units_guess(self):
        validate_request('MIX-01 설비 비교',self.inputs,self.args)
        self.assertEqual(request_values('MIX-01 중단 10분 비교 시간 15분',{}),self.args)
        for question,inputs,args in [
            ('중단 비교',{},self.args),('MIX-01 중단 12분 비교 15분',self.inputs,self.args),
            ('MIX-01 중단 10분 비교 15분',{},dict(self.args,horizon_minutes=10)),
            ('MIX-01 PACK-01 중단 10분 비교 15분',{},self.args),
            ('MIX-01',dict(self.inputs,downtimeMinutes=True),self.args),
            ('MIX-01',self.inputs,dict(self.args,station_id='PACK-01')),
            ('MIX-01',self.inputs,dict(self.args,horizon_minutes=math.inf)),
            ('MIX-01',self.inputs,dict(self.args,release=True)),
        ]:
            with self.assertRaises(ValueError): validate_request(question,inputs,args)

    def test_graph_uses_actual_native_factory_tools_then_synthesis(self):
        requests=[];trace=[];before=copy.deepcopy(self.state)
        def chat(path,payload):
            requests.append(payload)
            if len(requests)==1:
                self.assertEqual({s['function']['name'] for s in payload['tools']},{'get_factory_status','compare_factory_downtime'})
                self.assertEqual(json.loads(payload['messages'][-1]['content'])['factoryInputs'],self.inputs)
                return {'message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':'get_factory_status','arguments':{}}},{'function':{'name':'compare_factory_downtime','arguments':self.args}}]}}
            self.assertNotIn('tools',payload)
            source=json.loads(payload['messages'][-1]['content'])['factorySimulation']
            self.assertTrue(source['originalUnchanged']);self.assertTrue(source['massConserved'])
            self.assertEqual(payload['options']['num_predict'],256)
            self.assertNotIn('lots',source['baseline']);self.assertNotIn('backlogByStage',source['baseline'])
            return {'message':{'role':'assistant','content':'MIX-01 합성 용량 가정으로 포장 완료량을 비교했습니다. 원본 상태는 변경하지 않았습니다.'}}
        result=execute_run('downtime','MIX-01 설비 중단 비교',self.state,self.inputs,trace.append,chat=chat)
        self.assertEqual(len(requests),2);self.assertEqual(self.state,before)
        self.assertIn('factorySimulation',result);self.assertFalse(result['proposals'])
        self.assertEqual([r['tool'] for r in trace if not r['tool'].startswith('ollama.')],['get_factory_status','compare_factory_downtime'])

    def test_missing_values_read_status_then_abstain_without_comparison(self):
        requests=[]
        def chat(path,payload):
            requests.append(payload)
            if len(requests)==1:return {'message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':'get_factory_status','arguments':{}}}]}}
            return {'message':{'role':'assistant','content':'중단 시간과 비교 시간을 알려주세요.'}}
        result=execute_run('downtime','MIX-01 중단 비교',self.state,{},lambda _:None,chat=chat)
        self.assertNotIn('factorySimulation',result);self.assertIn('factory',result)
        self.assertTrue(result['incomplete'])

    def test_service_rejects_non_numeric_and_unbounded_factory_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'factory.db');store.write(self.state);service=AgentService(store)
            for invalid in [dict(self.inputs,downtimeMinutes=True),dict(self.inputs,horizonMinutes='15'),dict(self.inputs,horizonMinutes=241),dict(self.inputs,stationId='MIX-99'),dict(self.inputs,release=True)]:
                with self.assertRaises(ValueError):service.submit({'task':'downtime','question':'설비 비교','inputs':invalid})
            before=copy.deepcopy(store.read()['state'])
            with patch('agent_backend.backend_status',return_value={'available':False,'detail':'unavailable'}):
                with self.assertRaises(RuntimeError):service.submit({'task':'downtime','question':'MIX-01 비교','inputs':self.inputs})
            self.assertEqual(store.read()['state'],before)

    def test_graph_rejects_guessed_comparison_arguments_before_readonly_calculation(self):
        requests=[];trace=[]
        def chat(path,payload):
            requests.append(payload)
            if len(requests)==1:
                return {'message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':'get_factory_status','arguments':{}}},{'function':{'name':'compare_factory_downtime','arguments':dict(self.args,downtime_minutes=12)}}]}}
            if len(requests)==2:
                self.assertTrue(any('must match explicit' in json.dumps(m) for m in payload['messages']))
                return {'message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':'compare_factory_downtime','arguments':self.args}}]}}
            return {'message':{'role':'assistant','content':'MIX-01 합성 용량 비교 결과이며 원본은 변경하지 않았습니다.'}}
        result=execute_run('downtime','MIX-01 설비 중단 비교',self.state,self.inputs,trace.append,chat=chat)
        attempts=[r for r in trace if r['tool']=='compare_factory_downtime']
        self.assertIn('error',attempts[0]['result']);self.assertNotIn('error',attempts[1]['result'])
        self.assertEqual(result['factorySimulation']['downtimeMinutes'],10)

    def test_factory_summary_rejects_unobserved_station(self):
        tools=DomainTools(self.state);tools.get_factory_status('MIX-01')
        self.assertIsNone(validate_summary('MIX-01 설비 가정',tools)[1])
        self.assertIn('QC-99',validate_summary('QC-99 고장입니다',tools)[1])


if __name__=='__main__':unittest.main(verbosity=2)
