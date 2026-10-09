"""HTTP contract/security checks. Transport is mocked; actual model eval is separate."""
import base64
import json
import tempfile
import threading
import time
import unittest
from functools import partial
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from unittest.mock import patch
from agent_backend import AgentService
from domain_tools import DomainTools
from server import Handler,Store,ThreadingHTTPServer

ROOT=Path(__file__).resolve().parent

class QuietHandler(Handler):
    def log_message(self,*args): pass


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.store=Store(Path(cls.tmp.name)/'demo.db')
        cls.seed=json.loads((ROOT/'fixtures/demo-state.json').read_text(encoding='utf-8'));cls.store.write(cls.seed)
        cls.agent=AgentService(cls.store)
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),partial(QuietHandler,store=cls.store,agent=cls.agent))
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url='http://127.0.0.1:'+str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.tmp.cleanup()

    def request(self,path,payload=None,headers=None):
        req=Request(self.url+path,data=json.dumps(payload).encode() if payload is not None else None,headers=headers or {})
        try:
            with urlopen(req,timeout=3) as response: return response.status,json.loads(response.read())
        except HTTPError as response: return response.code,json.loads(response.read())

    def test_backend_files_and_foreign_host_denied(self):
        for path in ['/agent_backend.py','/rag_backend.py','/domain_tools.py','/tool_bridge.js','/fixtures/demo-state.json','/fixtures/rag/manifest.json','/.runtime/state.sqlite3','/requirements.txt','/vendor/../server.py']:
            self.assertEqual(self.request(path)[0],404)
        self.assertEqual(self.request('/api/health',headers={'Host':'attacker.invalid:'+str(self.server.server_port)})[0],403)
        self.assertEqual(self.request('/api/state',self.seed,{'Sec-Fetch-Site':'cross-site'})[0],403)

    def test_actual_api_queue_review_is_one_use_and_state_unchanged(self):
        self.store.write(self.seed)
        def workflow(task,question,state,inputs,record):
            t=DomainTools(state);t.trace_lot('LOT-007')
            record({'tool':'trace_lot','args':{'lot_id':'LOT-007'},'result':{'observed':True},'elapsedMs':1})
            t.prepare_action_proposal('LOT-007','resolve','사람이 재검사 입력 확인',['LOT-007-temperature'])
            return t.result('Lot 기록 검토')
        with patch('agent_backend.backend_status',return_value={'available':True}),patch('agent_backend.execute_run',side_effect=workflow):
            code,start=self.request('/api/agent/runs',{'task':'shipment','question':'현재 차단 검토','inputs':{}})
            self.assertEqual(code,202)
            for _ in range(30):
                code,run=self.request('/api/agent/runs/'+start['runId'])
                if run['status'] in ['completed','failed']: break
                time.sleep(.1)
            self.assertEqual(run['status'],'completed')
            proposal=run['result']['proposals'][0]
            endpoint='/api/agent/proposals/'+proposal['id']+'/review'
            code,result=self.request(endpoint,{'decision':'review'})
            self.assertEqual(code,200);self.assertFalse(result['mutated']);self.assertEqual(result['alertId'],'ALT-1')
            self.assertEqual(self.request(endpoint,{'decision':'review'})[0],400)
            self.assertEqual(self.store.read()['state'],self.seed)

    def test_unavailable_invalid_and_missing_run(self):
        with patch('agent_backend.backend_status',return_value={'available':False,'detail':'model unavailable'}):
            self.assertEqual(self.request('/api/agent/runs',{'task':'shipment','question':'검토'})[0],503)
        self.assertEqual(self.request('/api/agent/runs',{'task':'shipment','question':'검토','state':self.seed})[0],400)
        self.assertEqual(self.request('/api/agent/proposals/fake/review',[])[0],400)
        self.assertEqual(self.request('/api/agent/runs/fake')[0],404)

    def test_document_endpoint_rejects_invalid_payload(self):
        self.assertEqual(self.request('/api/documents/extract',{'data_base64':'@@','mime_type':'application/pdf','filename':'x.pdf'})[0],400)

    def test_rag_status_and_local_origin_boundary(self):
        with patch('rag_backend.rag_status',return_value={'available':False,'detail':'embedding unavailable; no fallback'}):
            code,body=self.request('/api/rag/status')
            self.assertEqual(code,200);self.assertFalse(body['available'])
            self.assertEqual(self.request('/api/rag/status',headers={'Origin':'https://external.invalid'})[0],403)

    def test_rag_input_contract_rejects_guessed_corpus_and_string_flag(self):
        for inputs in [{'corpus':'../../private'},{'ragEnabled':'false'},{'ragEnabled':1},{'corpus':'baseline','secret':'x'}]:
            self.assertEqual(self.request('/api/agent/runs',{'task':'rag','question':'LOT-003 수분 기준','inputs':inputs})[0],400)

    def test_rag_run_keeps_read_only_result_and_citation_contract(self):
        self.store.write(self.seed)
        def workflow(task,question,state,inputs,record):
            self.assertEqual(task,'rag');self.assertEqual(inputs,{'ragEnabled':False,'corpus':'baseline'})
            result=DomainTools(state).result('mock transport contract, not live AI evidence')
            result['rag']={'validation':{'status':'abstained'},'retrieval':{'enabled':False,'chunks':[]},'originalUnchanged':True}
            return result
        with patch('agent_backend.backend_status',return_value={'available':True}),patch('agent_backend.execute_run',side_effect=workflow):
            code,start=self.request('/api/agent/runs',{'task':'rag','question':'LOT-007 수분 기준','inputs':{'ragEnabled':False,'corpus':'baseline'}})
            self.assertEqual(code,202)
            for _ in range(30):
                _,run=self.request('/api/agent/runs/'+start['runId'])
                if run['status'] in ['completed','failed']: break
                time.sleep(.1)
            self.assertEqual(run['status'],'completed')
            self.assertFalse(run['result']['summaryVerified']);self.assertEqual(run['result']['proposals'],[])
            self.assertEqual(run['result']['rag']['validation']['status'],'abstained')
            self.assertEqual(self.store.read()['state'],self.seed)


if __name__=='__main__': unittest.main(verbosity=2)
