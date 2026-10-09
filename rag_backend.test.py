"""Mocked model/vector transport tests; these are NOT real model evaluations."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from agent_backend import execute_run
from domain_tools import DomainTools, state_hash
from rag_backend import (cosine, load_corpus, rag_status, read_records,
                         required_fields, retrieve_documents, validate_claims, answer_schema,
                         validate_inputs)

ROOT=Path(__file__).resolve().parent


class FakeEmbeddingTransport:
    """Explicit vector API stub with no generation; not used by real evaluator."""
    def __init__(self): self.calls=[]; self.digest='fake-test-digest';self.available=True
    def __call__(self,path,payload=None,**kwargs):
        self.calls.append((path,copy.deepcopy(payload)))
        if path=='/api/tags':
            return {'models':[{'name':'embeddinggemma:latest','digest':self.digest},{'name':'qwen3:4b-instruct','digest':'fake-qwen'}] if self.available else []}
        if path=='/api/embed':
            return {'model':'embeddinggemma','embeddings':[[1,int(hashlib.sha256(t.encode()).hexdigest()[:4],16)/65535] for t in payload['input']]}
        raise AssertionError('Fake embedding transport cannot generate')


def retrieval_fixture(corpus='baseline'):
    data=load_corpus(corpus)
    return {'enabled':True,'embeddingModel':'embeddinggemma','indexFingerprint':'test-only','cacheHit':False,
            'elapsedMs':0,'chunks':[{**c,'score':.8} for c in data['chunks']],
            'documents':data['documents'],'manifestSha256':data['manifestSha256']}


def claim(field,value,cite=None,evidence=None,cid='C-1'):
    return {'claimId':cid,'field':field,'value':value,'citationChunkIds':cite or [],'evidenceIds':evidence or []}


def answer(claims,abstain=False):
    return {'answer':'테스트 모델의 자유 서술은 검증하지 않습니다.','abstain':abstain,'reasons':[],'claims':claims}


class GenerationSchemaTests(unittest.TestCase):
    def setUp(self):
        self.required=['policy.moistureLimit','lot.LOT-003.moisture','order.SO-001.heldKg']
        self.records={'expected':{'lot.LOT-003.moisture':{'value':15.8,'evidenceIds':['LOT-003-moisture']},
                                 'order.SO-001.heldKg':{'value':8400,'evidenceIds':['SO-001-heldKg']}}}

    def test_document_and_record_schema_branches_keep_id_channels_disjoint(self):
        schema=answer_schema(self.required,retrieval_fixture('injection'),self.records)
        branches=schema['properties']['claims']['items']['oneOf']
        policy=next(b['properties'] for b in branches if b['properties']['field']['enum']==['policy.moistureLimit'])
        record=next(b['properties'] for b in branches if 'lot.LOT-003.moisture' in b['properties']['field']['enum'])
        self.assertEqual(policy['evidenceIds']['maxItems'],0)
        self.assertEqual(record['citationChunkIds']['maxItems'],0)
        self.assertEqual(set(record['evidenceIds']['items']['enum']),{'LOT-003-moisture','SO-001-heldKg'})
        self.assertEqual(record['evidenceIds']['maxItems'],1)
        self.assertEqual(policy['citationChunkIds']['maxItems'],1)
        doc_ids=set(policy['citationChunkIds']['items']['enum'])
        self.assertIn('DEMO-QC@2:3',doc_ids)
        self.assertFalse(doc_ids & set(record['evidenceIds']['items']['enum']))
        self.assertNotIn('DEMO-QC@1:3',doc_ids);self.assertNotIn('DEMO-ATTACK@1:3',doc_ids)
        self.assertEqual(schema['properties']['reasons']['maxItems'],2)

    def test_schema_does_not_encode_expected_values_or_right_field_source_binding(self):
        one=answer_schema(self.required,retrieval_fixture(),self.records)
        altered=copy.deepcopy(self.records)
        altered['expected']['lot.LOT-003.moisture']['value']=99
        altered['expected']['order.SO-001.heldKg']['value']=0
        self.assertEqual(one,answer_schema(self.required,retrieval_fixture(),altered))
        for branch in one['properties']['claims']['items']['oneOf']:
            self.assertEqual(branch['properties']['value'],{'type':'number'})
        # Related field-ID and value checks remain the validator's responsibility;
        # the model still has to extract values and select the correct source.
        record=one['properties']['claims']['items']['oneOf'][1]['properties']
        self.assertIn('SO-001-heldKg',record['evidenceIds']['items']['enum'])

    def test_disabled_or_missing_sources_allow_empty_claims_without_invented_ids(self):
        disabled={'enabled':False,'chunks':[]}
        schema=answer_schema(self.required,disabled,self.records)
        branch=schema['properties']['claims']['items']['oneOf'][0]['properties']
        self.assertEqual(branch['field']['enum'],['lot.LOT-003.moisture','order.SO-001.heldKg'])
        self.assertEqual(branch['citationChunkIds']['maxItems'],0)
        self.assertNotIn('minItems',schema['properties']['claims'])
        unsupported=answer_schema(['unsupported.cause'],disabled,{'expected':{}})
        self.assertEqual(unsupported['properties']['claims']['maxItems'],0)
        self.assertEqual(unsupported['properties']['claims']['const'],[])
        self.assertEqual(unsupported['properties']['claims']['items'],{'type':'string'})
        self.assertNotIn('"enum": []',json.dumps(unsupported))

    def test_missing_document_field_uses_explicit_empty_array_even_with_irrelevant_retrieved_sources(self):
        schema=answer_schema(['policy.preservativeLimit'],retrieval_fixture(),{'expected':{}})
        self.assertEqual(schema['properties']['claims'],{'type':'array','const':[],'maxItems':0,'items':{'type':'string'}})
        self.assertFalse(schema['properties']['abstain'].get('const',False))

    def test_named_measurement_rule_and_status_types_are_disjoint_without_value_constants(self):
        required=self.required+['policy.reinspectionRequired','policy.separateReleaseRequired','lot.LOT-003.status']
        records=copy.deepcopy(self.records)
        records['expected']['lot.LOT-003.status']={'value':'hold','evidenceIds':['LOT-003-status']}
        schema=answer_schema(required,retrieval_fixture(),records)
        types={field:branch['properties']['value'] for branch in schema['properties']['claims']['items']['oneOf'] for field in branch['properties']['field']['enum']}
        self.assertEqual(types['policy.moistureLimit'],{'type':'number'})
        self.assertEqual(types['lot.LOT-003.moisture'],{'type':'number'})
        self.assertEqual(types['order.SO-001.heldKg'],{'type':'number'})
        self.assertEqual(types['policy.reinspectionRequired'],{'type':'boolean'})
        self.assertEqual(types['policy.separateReleaseRequired'],{'type':'boolean'})
        self.assertEqual(types['lot.LOT-003.status'],{'type':'string'})
        records['expected']['lot.LOT-003.status']['value']='anything-else'
        self.assertEqual(schema,answer_schema(required,retrieval_fixture(),records))
        disabled=answer_schema(required,{'chunks':[]},records)
        fields=[field for branch in disabled['properties']['claims']['items']['oneOf'] for field in branch['properties']['field']['enum']]
        self.assertNotIn('policy.moistureLimit',fields)
        self.assertNotIn('policy.reinspectionRequired',fields)
        self.assertNotIn('minItems',disabled['properties']['claims'])


class RetrievalTests(unittest.TestCase):
    def test_actual_embed_contract_cosine_cache_and_model_digest_invalidation(self):
        fake=FakeEmbeddingTransport()
        with tempfile.TemporaryDirectory() as tmp:
            first=retrieve_documents('수분 기준',chat=fake,cache_dir=tmp)
            second=retrieve_documents('다른 질문',chat=fake,cache_dir=tmp)
            self.assertFalse(first['cacheHit']);self.assertTrue(second['cacheHit'])
            self.assertEqual(len([c for c in fake.calls if c[0]=='/api/embed']),3)
            self.assertEqual(first['indexFingerprint'],second['indexFingerprint'])
            fake.digest='new-test-digest';third=retrieve_documents('수분 기준',chat=fake,cache_dir=tmp)
            self.assertFalse(third['cacheHit']);self.assertNotEqual(first['indexFingerprint'],third['indexFingerprint'])
            embeds=[p for path,p in fake.calls if path=='/api/embed']
            self.assertTrue(all(p['model']=='embeddinggemma' and p['truncate'] is False for p in embeds))
            self.assertTrue(all(-1<=c['score']<=1 for c in first['chunks']))
            self.assertTrue(all(c['status']!='retired' for c in first['chunks']))
            self.assertEqual(first['excludedDocuments'][0]['revision'],'1')
            self.assertEqual(cosine([1,0],[0,1]),0)

    def test_document_bytes_and_manifest_change_invalidate_index(self):
        fake=FakeEmbeddingTransport()
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as cache:
            src=Path(src)
            for path in (ROOT/'fixtures/rag').glob('*'):
                if path.suffix in ['.json','.md']: (src/path.name).write_bytes(path.read_bytes())
            one=retrieve_documents('수분',chat=fake,cache_dir=cache,source_dir=src)
            path=src/'quality-r2.md';path.write_text(path.read_text(encoding='utf-8')+'\n\n합성 부록.\n',encoding='utf-8')
            two=retrieve_documents('수분',chat=fake,cache_dir=cache,source_dir=src)
            self.assertNotEqual(one['indexFingerprint'],two['indexFingerprint'])
            source=json.loads((src/'manifest.json').read_text(encoding='utf-8'));source['description']='new metadata'
            (src/'manifest.json').write_text(json.dumps(source),encoding='utf-8')
            three=retrieve_documents('수분',chat=fake,cache_dir=cache,source_dir=src)
            self.assertNotEqual(two['indexFingerprint'],three['indexFingerprint'])

    def test_bad_vectors_corrupt_cache_and_offline_never_keyword_fallback(self):
        fake=FakeEmbeddingTransport()
        with tempfile.TemporaryDirectory() as tmp:
            result=retrieve_documents('수분',chat=fake,cache_dir=tmp)
            path=Path(tmp)/(result['indexFingerprint']+'.json');path.write_text('{broken',encoding='utf-8')
            self.assertFalse(retrieve_documents('수분',chat=fake,cache_dir=tmp)['cacheHit'])
            fake.available=False
            with self.assertRaisesRegex(RuntimeError,'Embedding model not installed'): retrieve_documents('수분',chat=fake,cache_dir=tmp)
            def bad(path,payload=None,**kwargs):
                if path=='/api/tags':return FakeEmbeddingTransport()(path)
                return {'model':'embeddinggemma','embeddings':[[float('nan'),1] for _ in payload['input']]}
            with self.assertRaisesRegex(ValueError,'nonfinite'): retrieve_documents('수분',chat=bad,cache_dir=Path(tmp)/'new')

    def test_status_and_bounded_inputs(self):
        self.assertTrue(rag_status(FakeEmbeddingTransport())['available'])
        fake=FakeEmbeddingTransport();fake.available=False;self.assertFalse(rag_status(fake)['available'])
        for data in [{'ragEnabled':'true'},{'corpus':'production'},{'file':'secret.md'}]:
            with self.assertRaises(ValueError):validate_inputs(data)
        validate_inputs({'ragEnabled':False,'corpus':'conflict'})
        self.assertIn('unsupported.intent',required_fields('LOT-003의 가격은?')[0])


class ClaimTests(unittest.TestCase):
    def setUp(self):
        self.corpus=load_corpus();self.retrieval=retrieval_fixture()
        self.records={'requiredFields':['policy.moistureLimit'],'expected':{},'recordErrors':[],'stateMoistureLimit':14}
        self.valid=claim('policy.moistureLimit',14,['DEMO-QC@2:3'])

    def check(self,claims,**kwargs):
        return validate_claims(answer(claims),kwargs.get('retrieval',self.retrieval),kwargs.get('corpus',self.corpus),kwargs.get('records',self.records))[1]

    def test_source_value_quote_revision_and_scope(self):
        self.assertTrue(self.check([self.valid])['structuredClaimsVerified'])
        for wrong in [claim('policy.moistureLimit',99,['DEMO-QC@2:3']),
                      claim('policy.moistureLimit',16,['DEMO-QC@1:3']),
                      claim('policy.moistureLimit',14,['DEMO-ORDERS@1:3']),
                      claim('policy.moistureLimit',14,[]),
                      claim('policy.moistureLimit','14',['DEMO-QC@2:3']),
                      claim('policy.moistureLimit',14,['unknown']),
                      claim('policy.moistureLimit',14,['DEMO-QC@2:3'],['SETTING-moistureLimit'])]:
            with self.subTest(claim=wrong):self.assertFalse(self.check([wrong])['structuredClaimsVerified'])
        without={**self.retrieval,'chunks':[]}
        self.assertFalse(self.check([self.valid],retrieval=without)['structuredClaimsVerified'])

    def test_duplicate_omitted_empty_wrong_type_unknown_field_rejected(self):
        for claims in [[],[self.valid,self.valid],[self.valid,{**self.valid,'claimId':'C-2'}],
                       [{**self.valid,'field':['policy.moistureLimit']}],
                       [{**self.valid,'value':True}],
                       [{**self.valid,'citationChunkIds':['DEMO-QC@2:3','DEMO-QC@2:3']}],
                       [{**self.valid,'field':'policy.fake'}],
                       [{**self.valid,'claimId':'invalid'}]]:
            self.assertFalse(self.check(claims)['structuredClaimsVerified'])
        validation=self.check([self.valid],records={**self.records,'requiredFields':['policy.moistureLimit','policy.reinspectionRequired']})
        self.assertEqual(validation['missingFields'],['policy.reinspectionRequired'])

    def test_conflict_detected_even_if_competing_chunk_not_retrieved(self):
        conflict=load_corpus('conflict')
        verdict=self.check([self.valid],corpus=conflict)
        self.assertFalse(verdict['structuredClaimsVerified']);self.assertEqual(verdict['conflicts'][0]['field'],'policy.moistureLimit')
        self.assertEqual({s['value'] for s in verdict['conflicts'][0]['sources']},{13,14})

    def test_document_rule_must_match_snapshot_and_release_intent_not_ignored(self):
        verdict=self.check([self.valid],records={**self.records,'stateMoistureLimit':12})
        self.assertFalse(verdict['structuredClaimsVerified']);self.assertFalse(verdict['checks']['documentRuleMatchesState'])
        self.assertEqual(verdict['statePolicyMismatches'][0]['stateValue'],12)
        self.assertIn('policy.separateReleaseRequired',required_fields('재검사와 보류 해제 절차')[0])
        self.assertIn('unsupported.execution',required_fields('LOT-003 보류를 해제해줘')[0])
        self.assertIn('policy.unsupportedCriterion',required_fields('수분 기준과 단백질 허용 기준')[0])

    def test_current_fact_requires_exact_tool_field_not_document(self):
        records={'requiredFields':['order.SO-001.heldKg'],'expected':{'order.SO-001.heldKg':{'value':8400,'evidenceIds':['SO-001-heldKg']}},'recordErrors':[]}
        good=claim('order.SO-001.heldKg',8400,evidence=['SO-001-heldKg'])
        self.assertTrue(self.check([good],records=records)['structuredClaimsVerified'])
        for wrong in [{**good,'value':0},{**good,'evidenceIds':['SO-001-shippedKg']},{**good,'citationChunkIds':['DEMO-ORDERS@1:3']},{**good,'evidenceIds':[]}]:
            self.assertFalse(self.check([wrong],records=records)['structuredClaimsVerified'])

    def test_injection_does_not_create_a_valid_rule_fact(self):
        verdict=self.check([claim('policy.moistureLimit',99,['DEMO-ATTACK@1:3'])],corpus=load_corpus('injection'),retrieval=retrieval_fixture('injection'))
        self.assertFalse(verdict['structuredClaimsVerified'])
        self.assertEqual(self.corpus['facts'][0]['value'],14)


class GraphTests(unittest.TestCase):
    def setUp(self):self.state=json.loads((ROOT/'fixtures/rag/evaluation-state.json').read_text(encoding='utf-8'))

    def test_actual_domain_reads_and_graph_validate_model_claims_without_mutation(self):
        before=state_hash(self.state);trace=[];requests=[]
        payload=answer([claim('policy.moistureLimit',14,['DEMO-QC@2:3']),
                        claim('lot.LOT-003.moisture',15.8,evidence=['LOT-003-moisture'],cid='C-2'),
                        claim('order.SO-001.heldKg',8400,evidence=['SO-001-heldKg'],cid='C-3'),
                        claim('order.SO-001.shippedImpactKg',4000,evidence=['SO-001-shippedImpactKg'],cid='C-4')])
        def fake(path,data):
            self.assertEqual(path,'/api/chat');requests.append(data)
            return {'model':'qwen3:4b-instruct','message':{'role':'assistant','content':json.dumps(payload)},'done_reason':'stop'}
        with patch('rag_backend.retrieve_documents',return_value=retrieval_fixture()):
            result=execute_run('rag','수분 기준과 LOT-003 수분 및 SO-001 주문 보류량과 기출하 영향',self.state,{},trace.append,chat=fake)
        self.assertEqual(state_hash(self.state),before);self.assertTrue(result['rag']['originalUnchanged'])
        self.assertFalse(result['summaryVerified']);self.assertFalse(result['rag']['modelAnswer']['verified'])
        self.assertTrue(result['rag']['validation']['structuredClaimsVerified'])
        self.assertIn('8400',result['rag']['answer']['text'])
        self.assertEqual({t['tool'] for t in trace if t['tool'] in ['trace_lot','get_order_status']},{'trace_lot','get_order_status'})
        self.assertIn('format',requests[0]);context=json.loads(requests[0]['messages'][1]['content'])
        self.assertNotIn('expected',context);self.assertNotIn('facts',context['retrieved'][0])

    def test_unknown_record_disabled_rag_and_no_fake_completion(self):
        traces=[]
        def fake(path,data):return {'message':{'role':'assistant','content':json.dumps(answer([],True))},'done_reason':'stop'}
        result=execute_run('rag','LOT-999 상태',self.state,{'ragEnabled':False},traces.append,chat=fake)
        self.assertEqual(result['rag']['validation']['status'],'abstained')
        self.assertTrue(result['incomplete']);self.assertTrue(result['rag']['validation']['recordErrors'])
        self.assertFalse(result['rag']['retrieval']['enabled']);self.assertFalse(result['rag']['validation']['structuredClaimsVerified'])

    def test_zero_supported_preflight_skips_model_without_a_fake_model_abstention(self):
        requests=[];trace=[];before=state_hash(self.state)
        def fake(path,payload):
            requests.append(payload)
            raise AssertionError('No model call is allowed when zero requested fields have evidence')
        with patch('rag_backend.retrieve_documents',return_value=retrieval_fixture()):
            result=execute_run('rag','보존제 B-77 허용 농도 기준',self.state,{},trace.append,chat=fake)
        self.assertEqual(len(requests),0);self.assertEqual(state_hash(self.state),before)
        rag=result['rag'];self.assertEqual(rag['runtime']['supportedFieldCount'],0)
        self.assertTrue(rag['runtime']['modelInvocationSkipped'])
        self.assertEqual(rag['runtime']['modelInvocationSkipReasonCode'],'no-supported-fields')
        self.assertEqual(rag['preflight']['reasonCode'],'no-supported-fields')
        self.assertEqual(rag['runtime']['generationAttempts'],[]);self.assertIsNone(rag['runtime']['generation'])
        self.assertEqual(rag['modelAnswer'],{'text':'','verified':False,'abstain':None,'invoked':False})
        self.assertIsNone(rag['validation']['modelAbstained']);self.assertFalse(rag['validation']['modelInvoked'])
        self.assertEqual(rag['validation']['status'],'abstained')
        self.assertIn('policy.preservativeLimit',rag['validation']['missingFields'])
        self.assertFalse(any(t['tool'].startswith('ollama.') for t in trace))
        self.assertTrue(next(t for t in trace if t['tool']=='rag.preflight')['result']['skipped'])
        self.assertFalse(result['rag']['validation']['structuredClaimsVerified'])

    def test_conflicting_current_revisions_skip_model_even_with_supported_fields_and_rag_off(self):
        for enabled in [False,True]:
            with self.subTest(ragEnabled=enabled):
                trace=[];calls=[];before=state_hash(self.state)
                def fake(*args):
                    calls.append(args)
                    raise AssertionError('A model cannot choose between conflicting current SOP revisions')
                with patch('rag_backend.retrieve_documents',return_value=retrieval_fixture('conflict')):
                    result=execute_run('rag','수분 기준과 LOT-003 수분',self.state,
                                       {'corpus':'conflict','ragEnabled':enabled},trace.append,chat=fake)
                rag=result['rag'];self.assertEqual(calls,[]);self.assertEqual(state_hash(self.state),before)
                self.assertGreater(rag['runtime']['supportedFieldCount'],0)
                self.assertEqual(rag['runtime']['modelInvocationSkipReasonCode'],'revision-conflict')
                self.assertEqual(rag['preflight']['reasonCode'],'revision-conflict')
                self.assertEqual(rag['preflight']['conflicts'],rag['validation']['conflicts'])
                self.assertTrue(rag['validation']['conflicts'])
                self.assertFalse(rag['validation']['checks']['noCurrentRevisionConflict'])
                self.assertFalse(rag['validation']['structuredClaimsVerified'])
                self.assertEqual(rag['validation']['status'],'abstained')
                self.assertEqual(rag['modelAnswer'],{'text':'','verified':False,'abstain':None,'invoked':False})
                self.assertIsNone(rag['validation']['modelAbstained'])
                self.assertEqual(rag['runtime']['generationAttempts'],[]);self.assertIsNone(rag['runtime']['generation'])
                self.assertFalse(rag['runtime']['repairAttempted'])
                self.assertFalse(any(t['tool'].startswith('ollama.') for t in trace))
                preflight=next(t for t in trace if t['tool']=='rag.preflight')
                self.assertEqual(preflight['args']['reasonCode'],'revision-conflict')
                self.assertTrue(result['incomplete']);self.assertFalse(result['summaryVerified'])

    def test_retrieved_policy_state_mismatch_skips_model_and_retains_both_rule_values(self):
        state=copy.deepcopy(self.state);state['settings']['moistureLimit']=12
        before=state_hash(state);trace=[];calls=[]
        def fake(*args):
            calls.append(args)
            raise AssertionError('A model cannot reconcile differing saved and retrieved rules')
        with patch('rag_backend.retrieve_documents',return_value=retrieval_fixture()):
            result=execute_run('rag','수분 기준과 LOT-003 수분',state,{},trace.append,chat=fake)
        rag=result['rag'];self.assertEqual(calls,[]);self.assertEqual(state_hash(state),before)
        self.assertGreater(rag['runtime']['supportedFieldCount'],0)
        self.assertEqual(rag['runtime']['modelInvocationSkipReasonCode'],'state-rule-mismatch')
        self.assertEqual(rag['preflight']['reasonCode'],'state-rule-mismatch')
        self.assertEqual(rag['preflight']['statePolicyMismatches'],rag['validation']['statePolicyMismatches'])
        mismatch=rag['preflight']['statePolicyMismatches'][0]
        self.assertEqual((mismatch['documentValue'],mismatch['stateValue'],mismatch['chunkId']),(14,12,'DEMO-QC@2:3'))
        self.assertFalse(rag['validation']['checks']['documentRuleMatchesState'])
        self.assertFalse(rag['validation']['structuredClaimsVerified'])
        self.assertEqual(rag['validation']['status'],'abstained');self.assertEqual(rag['claims'],[])
        self.assertFalse(rag['modelAnswer']['invoked']);self.assertIsNone(rag['modelAnswer']['abstain'])
        self.assertEqual(rag['runtime']['generationAttempts'],[]);self.assertIsNone(rag['runtime']['generation'])
        self.assertFalse(any(t['tool'].startswith('ollama.') for t in trace))
        self.assertEqual(next(t for t in trace if t['tool']=='rag.preflight')['args']['reasonCode'],'state-rule-mismatch')

    def test_partial_supported_cause_or_unknown_record_still_invokes_model(self):
        cases=[('LOT-003 수분과 수분 기준 및 부패 원인을 확정해줘',
                answer([claim('policy.moistureLimit',14,['DEMO-QC@2:3']),
                        claim('lot.LOT-003.moisture',15.8,evidence=['LOT-003-moisture'],cid='C-2')],True),
                'unsupported.cause'),
               ('LOT-999 상태와 재검사 절차',
                answer([claim('policy.reinspectionRequired',True,['DEMO-QC@2:4'])],True),
                'lot.LOT-999.status')]
        for question,payload,missing in cases:
            with self.subTest(question=question):
                calls=[];trace=[];before=state_hash(self.state)
                def fake(path,data):
                    calls.append(data)
                    return {'message':{'role':'assistant','content':json.dumps(payload)},'done_reason':'stop'}
                with patch('rag_backend.retrieve_documents',return_value=retrieval_fixture()):
                    result=execute_run('rag',question,self.state,{},trace.append,chat=fake)
                rag=result['rag'];self.assertEqual(len(calls),1);self.assertEqual(state_hash(self.state),before)
                self.assertFalse(rag['runtime']['modelInvocationSkipped'])
                self.assertIsNone(rag['runtime']['modelInvocationSkipReasonCode'])
                self.assertTrue(rag['modelAnswer']['invoked']);self.assertTrue(rag['validation']['modelAbstained'])
                self.assertEqual(rag['validation']['status'],'abstained')
                self.assertIn(missing,rag['validation']['missingFields'])
                self.assertGreater(rag['validation']['acceptedCount'],0)
                self.assertFalse(rag['runtime']['repairAttempted']);self.assertEqual(len(rag['runtime']['generationAttempts']),1)
                self.assertFalse(any(t['tool']=='rag.preflight' for t in trace))

    def test_missing_current_measurement_is_not_replaced_by_another_record_or_model(self):
        state=copy.deepcopy(self.state);next(lot for lot in state['lots'] if lot['id']=='LOT-003')['qc']['moisture']=None
        before=state_hash(state);trace=[]
        def fake(*args):raise AssertionError('Missing measurement must skip model')
        result=execute_run('rag','LOT-003 현재 수분',state,{'ragEnabled':False},trace.append,chat=fake)
        self.assertEqual(state_hash(state),before);self.assertTrue(result['rag']['runtime']['modelInvocationSkipped'])
        self.assertIn('lot.LOT-003.moisture',result['rag']['validation']['missingFields'])
        self.assertFalse(result['rag']['modelAnswer']['invoked'])

    def test_model_invalid_or_unavailable_fails_transparently(self):
        for response in [{'message':{'role':'assistant','content':'not JSON'}},
                         {'message':{'role':'assistant','content':'{}'},'done_reason':'length'}]:
            with self.assertRaisesRegex(ValueError,'invalid structured JSON|truncated'):
                execute_run('rag','LOT-003 현재 상태',self.state,{'ragEnabled':False},lambda _:None,chat=lambda *args:response)
        with self.assertRaises(OSError):
            execute_run('rag','LOT-003 현재 상태',self.state,{'ragEnabled':False},lambda _:None,chat=lambda *args:(_ for _ in ()).throw(OSError('model offline')))

    def test_composite_requests_cannot_pass_after_omitting_requested_fields(self):
        cases=[
            ('LOT-003 수분과 상태',[claim('lot.LOT-003.moisture',15.8,evidence=['LOT-003-moisture'])],'lot.LOT-003.status'),
            ('SO-001 요청 물량과 현재 보류량',[claim('order.SO-001.heldKg',8400,evidence=['SO-001-heldKg'])],'order.SO-001.requestedKg'),
            ('LOT-003 수분과 가격',[claim('lot.LOT-003.moisture',15.8,evidence=['LOT-003-moisture'])],'unsupported.price'),
        ]
        for question,claims,missing in cases:
            with self.subTest(question=question):
                fake=lambda *args:{'message':{'role':'assistant','content':json.dumps(answer(claims))},'done_reason':'stop'}
                result=execute_run('rag',question,self.state,{'ragEnabled':False},lambda _:None,chat=fake)
                self.assertFalse(result['rag']['validation']['structuredClaimsVerified'])
                self.assertIn(missing,result['rag']['validation']['missingFields'])
                self.assertTrue(result['rag']['answer']['abstain'])
        self.assertEqual(required_fields('SO-001 요청 물량과 현재 보류량')[0],['order.SO-001.requestedKg','order.SO-001.heldKg'])

    def test_lowercase_unknown_lot_is_read_and_abstained_not_silently_omitted(self):
        trace=[]
        fake=lambda *args:{'message':{'role':'assistant','content':json.dumps(answer([],True))},'done_reason':'stop'}
        result=execute_run('rag','lot-999 상태',self.state,{'ragEnabled':False},trace.append,chat=fake)
        self.assertTrue(any(t['tool']=='trace_lot' and t['args']['lot_id']=='LOT-999' for t in trace))
        self.assertIn('lot.LOT-999.status',result['rag']['validation']['missingFields'])
        self.assertTrue(result['rag']['validation']['recordErrors']);self.assertTrue(result['rag']['answer']['abstain'])

    def test_composite_correct_values_and_units_stay_bound_to_fields(self):
        claims=[claim('order.SO-001.requestedKg',13000,evidence=['SO-001-requestedKg']),
                claim('order.SO-001.heldKg',8400,evidence=['SO-001-heldKg'],cid='C-2')]
        fake=lambda *args:{'message':{'role':'assistant','content':json.dumps(answer(claims))},'done_reason':'stop'}
        result=execute_run('rag','SO-001 요청 물량과 현재 보류량',self.state,{'ragEnabled':False},lambda _:None,chat=fake)
        self.assertTrue(result['rag']['validation']['structuredClaimsVerified'])
        self.assertEqual([(c['field'],c['value'],c['unit']) for c in result['rag']['claims']],
                         [('order.SO-001.requestedKg',13000,'kg'),('order.SO-001.heldKg',8400,'kg')])

    def test_injection_evaluation_reports_unexercised_when_attack_not_retrieved(self):
        from evaluate_rag import checks
        corpus=retrieval_fixture('injection');corpus['corpus']='injection'
        corpus['runtime']={'queryEmbedding':{'model':'embeddinggemma'}}
        result={'summaryVerified':False,'stateHash':state_hash(self.state),'rag':{'retrieval':corpus,'claims':[],
                'originalUnchanged':True,'modelAnswer':{'verified':False},'validation':{'status':'abstained'}}}
        trace=[{'tool':'ollama.rag','result':{'message':{'role':'assistant'}}}]
        present=checks(result,trace,True,'abstain',state_hash(self.state),self.state)
        self.assertTrue(present['injection_attack_retrieved'])
        corpus['chunks']=[c for c in corpus['chunks'] if c['chunkId']!='DEMO-ATTACK@1:3']
        missing=checks(result,trace,True,'abstain',state_hash(self.state),self.state)
        self.assertFalse(missing['injection_attack_retrieved'])

    def test_wrong_tool_reference_channel_is_rejected_without_automatic_remapping(self):
        original=claim('lot.LOT-003.moisture',15.8,cite=['LOT-003-moisture'])
        fake=lambda *args:{'message':{'role':'assistant','content':json.dumps(answer([original]))},'done_reason':'stop'}
        result=execute_run('rag','LOT-003 현재 수분',self.state,{'ragEnabled':False},lambda _:None,chat=fake)
        self.assertEqual(result['rag']['validation']['status'],'rejected')
        returned=result['rag']['claims'][0]
        self.assertEqual(returned['citationChunkIds'],['LOT-003-moisture'])
        self.assertEqual(returned['evidenceIds'],[])
        self.assertEqual(returned['value'],15.8)
        self.assertTrue(result['rag']['answer']['abstain'])


class RepairTests(unittest.TestCase):
    def setUp(self):
        from evaluate_rag import NORMAL
        self.question=NORMAL;self.state=json.loads((ROOT/'fixtures/rag/evaluation-state.json').read_text(encoding='utf-8'))
        self.good=answer([
            claim('policy.moistureLimit',14,['DEMO-QC@2:3']),
            claim('policy.reinspectionRequired',True,['DEMO-QC@2:4'],cid='C-2'),
            claim('policy.separateReleaseRequired',True,['DEMO-QC@2:4'],cid='C-3'),
            claim('lot.LOT-003.moisture',15.8,evidence=['LOT-003-moisture'],cid='C-4'),
            claim('order.SO-001.heldKg',8400,evidence=['SO-001-heldKg'],cid='C-5'),
            claim('order.SO-001.shippedImpactKg',4000,evidence=['SO-001-shippedImpactKg'],cid='C-6')])
        self.bad=copy.deepcopy(self.good)
        self.bad['claims']=[c for c in self.bad['claims'] if c['field']!='policy.separateReleaseRequired']
        for c in self.bad['claims']:
            if c['field'].startswith(('lot.','order.')):c['evidenceIds']=['LOT-003-moisture','SO-001-heldKg','SO-001-shippedImpactKg']

    def test_repair_is_a_second_actual_transport_call_with_feedback_and_same_originals(self):
        requests=[];trace=[];before=state_hash(self.state)
        def fake(path,payload):
            self.assertEqual(path,'/api/chat');requests.append(payload)
            response=self.bad if len(requests)==1 else self.good
            return {'message':{'role':'assistant','content':json.dumps(response)},'done_reason':'stop','eval_count':10+len(requests)}
        with patch('rag_backend.retrieve_documents',return_value=retrieval_fixture()):
            result=execute_run('rag',self.question,self.state,{},trace.append,chat=fake)
        self.assertEqual(len(requests),2);self.assertEqual(state_hash(self.state),before)
        rag=result['rag'];self.assertTrue(rag['validation']['structuredClaimsVerified']);self.assertTrue(rag['runtime']['repairAttempted'])
        initial=json.loads(requests[0]['messages'][1]['content']);repair=json.loads(requests[1]['messages'][1]['content'])
        self.assertEqual(initial['retrieved'],repair['retrieved']);self.assertEqual(initial['currentToolEvidence'],repair['currentToolEvidence'])
        self.assertEqual(set(repair['validationFeedback']),{'previousModelJson','missingFields','validationErrors','instruction'})
        self.assertEqual(json.loads(repair['validationFeedback']['previousModelJson']),self.bad)
        self.assertIn('policy.separateReleaseRequired',repair['validationFeedback']['missingFields'])
        self.assertNotIn('expected',repair);self.assertNotIn('correctValues',repair['validationFeedback'])
        attempts=rag['runtime']['generationAttempts']
        self.assertEqual([(a['attempt'],a['phase'],a['validationStatus']) for a in attempts],[(1,'initial','rejected'),(2,'repair','passed')])
        self.assertEqual(json.loads(attempts[0]['rawResponse']['message']['content']),self.bad)
        self.assertEqual(json.loads(attempts[1]['rawResponse']['message']['content']),self.good)
        self.assertEqual([(t['args']['attempt'],t['args']['phase']) for t in trace if t['tool']=='ollama.rag'],[(1,'initial'),(2,'repair')])
        self.assertTrue(all(a['elapsedMs']>=0 for a in attempts));self.assertFalse(result['summaryVerified'])

    def test_repeat_failure_stops_after_one_repair_and_stays_fail_closed(self):
        calls=[]
        def fake(path,payload):
            calls.append(payload)
            return {'message':{'role':'assistant','content':json.dumps(self.bad)},'done_reason':'stop'}
        with patch('rag_backend.retrieve_documents',return_value=retrieval_fixture()):
            result=execute_run('rag',self.question,self.state,{},lambda _:None,chat=fake)
        self.assertEqual(len(calls),2);self.assertFalse(result['rag']['validation']['structuredClaimsVerified'])
        self.assertTrue(result['rag']['answer']['abstain']);self.assertTrue(result['incomplete'])
        self.assertEqual(result['rag']['runtime']['maxGenerationAttempts'],2)
        self.assertEqual(result['rag']['runtime']['generationAttempts'][1]['validationStatus'],'rejected')

    def test_no_repair_when_disabled_missing_unknown_conflicting_or_unsupported(self):
        changed=copy.deepcopy(self.state);changed['settings']['moistureLimit']=12
        cases=[
            (self.question,{'ragEnabled':False},self.state,'baseline'),
            ('문서의 보존제 B-77 허용 농도 기준',{},self.state,'baseline'),
            ('LOT-999 상태',{},self.state,'baseline'),
            ('수분 기준과 LOT-003 수분',{'corpus':'conflict'},self.state,'conflict'),
            ('LOT-003 부패 원인을 확정해줘',{},self.state,'baseline'),
            ('LOT-003 수분과 가격',{},self.state,'baseline'),
            ('수분 기준',{},changed,'baseline'),
        ]
        for question,inputs,state,corpus in cases:
            with self.subTest(question=question,inputs=inputs):
                calls=[]
                def fake(path,payload):
                    calls.append(payload)
                    return {'message':{'role':'assistant','content':json.dumps(answer([],True))},'done_reason':'stop'}
                with patch('rag_backend.retrieve_documents',return_value=retrieval_fixture(corpus)):
                    result=execute_run('rag',question,state,inputs,lambda _:None,chat=fake)
                expected_count=0 if result['rag']['runtime']['modelInvocationSkipped'] else 1
                self.assertEqual(len(calls),expected_count);self.assertFalse(result['rag']['runtime']['repairAttempted'])
                self.assertFalse(result['rag']['validation']['structuredClaimsVerified'])

    def test_invalid_repair_json_preserves_audit_and_abstains(self):
        calls=[];before=state_hash(self.state)
        def fake(path,payload):
            calls.append(payload)
            return {'message':{'role':'assistant','content':json.dumps(self.bad) if len(calls)==1 else 'invalid JSON'},'done_reason':'stop'}
        with patch('rag_backend.retrieve_documents',return_value=retrieval_fixture()):
            result=execute_run('rag',self.question,self.state,{},lambda _:None,chat=fake)
        self.assertEqual(len(calls),2);self.assertEqual(state_hash(self.state),before)
        rag=result['rag'];self.assertEqual(rag['runtime']['generationAttempts'][1]['validationStatus'],'error')
        self.assertEqual(rag['runtime']['generationAttempts'][1]['rawResponse']['message']['content'],'invalid JSON')
        self.assertIn('invalid structured JSON',rag['validation']['repairError'])
        self.assertTrue(rag['answer']['abstain']);self.assertFalse(rag['validation']['structuredClaimsVerified'])

    def test_repair_transport_exception_is_traced_and_result_stays_fail_closed(self):
        calls=[];trace=[];before=state_hash(self.state)
        def fake(path,payload):
            calls.append(payload)
            if len(calls)==2:raise OSError('model stopped during repair')
            return {'message':{'role':'assistant','content':json.dumps(self.bad)},'done_reason':'stop'}
        with patch('rag_backend.retrieve_documents',return_value=retrieval_fixture()):
            result=execute_run('rag',self.question,self.state,{},trace.append,chat=fake)
        self.assertEqual(len(calls),2);self.assertEqual(state_hash(self.state),before)
        responses=[t for t in trace if t['tool']=='ollama.rag']
        self.assertEqual(responses[1]['args']['attempt'],2)
        self.assertEqual(responses[1]['result']['error'],'model stopped during repair')
        rag=result['rag'];self.assertEqual(rag['runtime']['generationAttempts'][1]['validationStatus'],'error')
        self.assertEqual(json.loads(rag['runtime']['generationAttempts'][0]['rawResponse']['message']['content']),self.bad)
        self.assertTrue(rag['answer']['abstain']);self.assertFalse(rag['validation']['structuredClaimsVerified'])

    def test_complete_claims_with_model_abstention_alone_do_not_trigger_repair(self):
        payload=copy.deepcopy(self.good);payload['abstain']=True;calls=[]
        def fake(path,data):
            calls.append(data)
            return {'message':{'role':'assistant','content':json.dumps(payload)},'done_reason':'stop'}
        with patch('rag_backend.retrieve_documents',return_value=retrieval_fixture()):
            result=execute_run('rag',self.question,self.state,{},lambda _:None,chat=fake)
        self.assertEqual(len(calls),1);self.assertFalse(result['rag']['runtime']['repairAttempted'])
        self.assertEqual(result['rag']['validation']['status'],'abstained')


if __name__=='__main__':unittest.main(verbosity=2)
