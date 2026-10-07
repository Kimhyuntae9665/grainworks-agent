"""HTTP and SQLite integration checks with disposable test storage."""
import copy
import json
import subprocess
import tempfile
import threading
import unittest
from functools import partial
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from server import Store, Handler, ThreadingHTTPServer, validate_state

ROOT = Path(__file__).resolve().parent

class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed = json.loads(subprocess.check_output(["node", "-e", "process.stdout.write(JSON.stringify(require('./engine.js').createState()))"], cwd=ROOT, encoding="utf-8"))
        cls.tmp = tempfile.TemporaryDirectory(prefix="grainworks-test-")
        cls.store = Store(Path(cls.tmp.name)/"state.sqlite3")
        cls.http = ThreadingHTTPServer(("127.0.0.1",0), partial(Handler,store=cls.store))
        cls.thread = threading.Thread(target=cls.http.serve_forever,daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.http.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown(); cls.http.server_close(); cls.thread.join(); cls.tmp.cleanup()

    def request(self,path,body=None,origin=None):
        headers={"Content-Type":"application/json"}
        if origin: headers["Origin"]=origin
        req=Request(self.url+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:
            with urlopen(req,timeout=3) as response:
                return response.status,response.read(),response.headers
        except HTTPError as error:
            return error.code,error.read(),error.headers

    def test_health_and_static(self):
        code,data,headers=self.request('/api/health')
        self.assertEqual(code,200);self.assertTrue(json.loads(data)['ok'])
        code,data,headers=self.request('/')
        self.assertEqual(code,200);self.assertIn(b'Grainworks',data)
        self.assertEqual(headers['X-Content-Type-Options'],'nosniff')
        self.assertEqual(self.request('/server.py')[0],404)

    def test_real_engine_snapshot_roundtrip(self):
        state=copy.deepcopy(self.seed);state['running']=False;state['settings']['productionRate']=110
        self.assertEqual(self.request('/api/state',state,self.url)[0],200)
        actual=json.loads(self.request('/api/state')[1])['state']
        self.assertEqual(actual,state)
        self.assertEqual(Store(self.store.path).read()['state'],state)

    def test_invalid_snapshot_cannot_replace_saved_data(self):
        self.store.write(self.seed)
        bad=copy.deepcopy(self.seed);bad['lots'][1]['id']=bad['lots'][0]['id']
        self.assertEqual(self.request('/api/state',bad)[0],400)
        self.assertEqual(self.store.read()['state'],self.seed)
        bad=copy.deepcopy(self.seed);bad['lots'][0]['qc']['moisture']=float('nan')
        self.assertEqual(self.request('/api/state',bad)[0],400)
        self.assertEqual(self.store.read()['state'],self.seed)

    def test_invalid_nested_settings_returns_error(self):
        bad=copy.deepcopy(self.seed);bad['settings']=[]
        self.assertEqual(self.request('/api/state',bad)[0],400)
        self.assertEqual(self.request('/api/health')[0],200)

    def test_foreign_origin_is_rejected(self):
        self.store.write(self.seed)
        self.assertEqual(self.request('/api/state',self.seed,'https://external.invalid')[0],403)
        self.assertEqual(self.store.read()['state'],self.seed)

    def test_factory_configuration_roundtrip_and_rejection(self):
        state=copy.deepcopy(self.seed)
        state['factory']={'stations':{'PACK-01':{'capacityKgPerMinute':800,'status':'down'}}}
        self.assertEqual(self.request('/api/state',state)[0],200)
        self.assertEqual(self.store.read()['state'],state)
        for config in [{'stations':[]},{'stations':{'FAKE-01':{}}},{'stations':{'PACK-01':{'capacityKgPerMinute':0}}},{'stations':{'PACK-01':{'status':'release'}}},{'stations':{'PACK-01':{'status':[]}}}]:
            bad=copy.deepcopy(self.seed);bad['factory']=config
            self.assertEqual(self.request('/api/state',bad)[0],400)
            self.assertEqual(self.store.read()['state'],state)

    def test_factory_scripts_are_served_but_backend_is_private(self):
        for path in ['/factory_model.js','/factory-ui.js','/factory-ui.css']:
            self.assertEqual(self.request(path)[0],200)
        self.assertEqual(self.request('/factory_tools.py')[0],404)

    def test_orders_roundtrip_and_invalid_allocation_never_replaces_saved_state(self):
        script="const E=require('./engine.js'),O=require('./order_model.js'),s=E.createState();O.ensure(s);process.stdout.write(JSON.stringify(s));"
        state=json.loads(subprocess.check_output(['node','-e',script],cwd=ROOT,encoding='utf-8'))
        self.assertEqual(self.request('/api/state',state)[0],200)
        self.assertEqual(self.store.read()['state'],state)
        invalid=[]
        for field,value in [('quantityKg',-1),('quantityKg',True),('quantityKg',1000000000),('lotId','LOT-999'),('assetId','TRK-999')]:
            bad=copy.deepcopy(state);bad['orders'][0]['lines'][0]['allocations'][0][field]=value;invalid.append(bad)
        bad=copy.deepcopy(state);bad['orders'][0]['lines'][0]['requestedKg']=1;invalid.append(bad)
        bad=copy.deepcopy(state);bad['orders'].append(copy.deepcopy(bad['orders'][0]));invalid.append(bad)
        bad=copy.deepcopy(state);bad['orders'][0]['lines'][0]['product']='Wrong product';invalid.append(bad)
        # Each line is valid alone, but duplicating the Lot across a second order
        # exceeds the shared physical quantity and must be rejected globally.
        bad=copy.deepcopy(state);other=copy.deepcopy(bad['orders'][0]);other['id']='SO-999';other['lines'][0]['id']='SO-999-L1';bad['orders'].append(other);invalid.append(bad)
        for bad in invalid:
            with self.subTest(orders=bad['orders']):
                self.assertEqual(self.request('/api/state',bad)[0],400)
                self.assertEqual(self.store.read()['state'],state)
        legacy=copy.deepcopy(state);legacy.pop('orders')
        self.assertEqual(self.request('/api/state',legacy)[0],200)
        self.assertEqual(self.store.read()['state'],legacy)

    def test_order_model_is_served_and_order_backend_remains_private(self):
        for path in ['/order_model.js','/order-ui.js','/order-ui.css']:
            self.assertEqual(self.request(path)[0],200)
        self.assertEqual(self.request('/order_tools.py')[0],404)

    def test_hold_and_reinspection_snapshot_restores(self):
        script="const E=require('./engine.js'),s=E.createState();E.inject(s,'moisture');E.resolve(s,s.alerts[0].id,'synthetic reinspection',{moisture:12,temperature:24});E.release(s,'LOT-001','verified demo records');process.stdout.write(JSON.stringify(s));"
        state=json.loads(subprocess.check_output(['node','-e',script],cwd=ROOT,encoding='utf-8'))
        self.assertEqual(self.request('/api/state',state)[0],200)
        actual=self.store.read()['state'];self.assertEqual(actual,state)
        self.assertEqual(actual['lots'][0]['status'],'ok')
        self.assertEqual(actual['lots'][2]['status'],'hold')
        self.assertEqual(actual['alerts'][0]['reinspection']['before']['moisture'],15.8)

if __name__=='__main__': unittest.main(verbosity=2)
