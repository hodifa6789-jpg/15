import json
import asyncio
import time
import httpx
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from web_app import create_app
from grounding import collected_facts
from web_store import WebStore

BENIGN='صار خلاف بيني وبين جاري حول موقف السيارة. انتهى الخلاف. ما هددني ولا اعتدى علي. ما صار ضرر أو خسارة. أنا مش خايف وما في خطر حاليًا. بدي نصائح وما بدي بلاغ.'
THREAT='هددني بنشر صوري إذا ما دفعت 500 دينار. ما دفعت أي مبلغ. لم يتم نشر الصور.'

def mock_extract(session,*args):
    session.analysis_mode='mock'
    return collected_facts(session)

class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'web.sqlite3'
        self.app=create_app(self.path,password='',public_url='',extractor=mock_extract)
        self.client=TestClient(self.app)
        self.state=self.client.get('/api/state').json()
        self.counter=0

    def tearDown(self): self.client.close(); self.temp.cleanup()

    def send(self,text='',action='message',request_id=None,revision=None):
        self.counter+=1
        response=self.client.post('/api/message',headers={'X-CSRF-Token':self.state['csrf']},json={'text':text,'action':action,
            'request_id':request_id or f'request-{self.counter:016d}','revision':self.state['revision'] if revision is None else revision})
        if response.status_code==200: self.state=response.json()
        return response

    def test_web_page_and_security_headers(self):
        r=self.client.get('/')
        self.assertEqual(r.status_code,200)
        self.assertIn('text/html',r.headers['content-type'])
        self.assertIn('script-src',r.headers['content-security-policy'])
        self.assertEqual(self.client.get('/health').json()['status'],'ok')
        cookie=self.client.get('/api/state')
        self.assertNotIn('key',cookie.json())

    def test_benign_full_flow_report_and_download(self):
        self.assertEqual(self.send(BENIGN).status_code,200)
        self.assertEqual(self.state['answered'],0)
        self.assertEqual(self.state['report']['risk']['recommended_route'],'assisted_resolution')
        for kind in ('json','html','md'):
            self.assertEqual(self.client.get('/api/report/'+kind).status_code,200)
        self.assertEqual(self.client.get('/api/report/draft').status_code,403)

    def test_restore_cookie_and_pending_question_across_app_restart(self):
        self.send(THREAT)
        question=self.state['question']
        second=TestClient(create_app(self.path,password='',public_url='',extractor=mock_extract))
        second.cookies.update(self.client.cookies)
        recovered=second.get('/api/state').json()
        self.assertEqual(recovered['question'],question)
        self.assertEqual(recovered['messages'],self.state['messages'])
        second.close()

    def test_explain_summary_do_not_consume_answer(self):
        self.send(THREAT)
        original=self.state['question']
        self.send(action='explain')
        self.send(action='summary')
        self.assertEqual(self.state['answered'],0)
        self.assertEqual(self.state['question'],original)

    def test_idempotency_and_stale_tabs(self):
        first_revision=self.state['revision']
        self.send(THREAT,request_id='one-fixed-request-12345')
        history=self.state['messages']
        self.send(THREAT,request_id='one-fixed-request-12345',revision=first_revision)
        self.assertEqual(history,self.state['messages'])
        self.assertEqual(self.send('لا',revision=first_revision).status_code,409)

    def test_owner_isolation(self):
        self.send(BENIGN)
        with TestClient(self.app) as outsider:
            other=outsider.get('/api/state').json()
            self.assertEqual(other['messages'],[])
            self.assertEqual(outsider.get('/api/report/html').status_code,404)

    def test_explicit_consent_required_then_download(self):
        self.send(THREAT)
        self.send(action='finish')
        self.assertEqual(self.state['stage'],'draft_consent')
        self.assertEqual(self.client.get('/api/report/draft').status_code,403)
        self.send(action='consent_yes')
        self.assertEqual(self.client.get('/api/report/draft').status_code,200)
        self.assertEqual(self.send(action='consent_yes').status_code,409)

    def test_reset_deletes_case_and_changes_cookie(self):
        self.send(BENIGN)
        old=self.client.cookies.get('accp_session')
        result=self.client.post('/api/reset',json={},headers={'X-CSRF-Token':self.state['csrf']})
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()['messages'],[])
        self.assertNotEqual(old,self.client.cookies.get('accp_session'))
        self.assertIsNone(self.app.state.store.load(old))

    def test_csrf_origin_and_host(self):
        self.assertEqual(self.client.post('/api/reset',json={}).status_code,403)
        self.assertEqual(self.client.post('/api/reset',json={},headers={'Origin':'https://evil.invalid','X-CSRF-Token':self.state['csrf']}).status_code,403)
        self.assertEqual(self.client.get('/api/state',headers={'host':'evil.invalid'}).status_code,400)

    def test_invalid_inputs_do_not_echo_sensitive_values(self):
        secret='secret-example-not-real'
        r=self.client.post('/api/model',json={'key':secret,'model':22,'base_url':'wrong'},headers={'X-CSRF-Token':self.state['csrf']})
        self.assertEqual(r.status_code,422)
        self.assertNotIn(secret,r.text)
        self.assertEqual(self.send('x'*3001).status_code,422)

    def test_public_deployment_requires_password(self):
        with self.assertRaises(RuntimeError): create_app(self.path,password='',public_url='https://demo.example')
        app=create_app(self.path,password='test-passphrase-123',public_url='https://demo.example')
        with TestClient(app,base_url='https://demo.example') as client:
            self.assertEqual(client.get('/api/state').status_code,401)
            self.assertEqual(client.post('/api/login',json={'password':'bad'}).status_code,401)
            r=client.post('/api/login',json={'password':'test-passphrase-123'})
            self.assertEqual(r.status_code,200)
            self.assertIn('Secure',r.headers['set-cookie'])

    def test_model_key_memory_only_and_unsafe_host_rejected(self):
        headers={'X-CSRF-Token':self.state['csrf']}
        bad={'key':'secret-example-123456','model':'test/model','base_url':'https://127.0.0.1/v1'}
        self.assertEqual(self.client.post('/api/model',json=bad,headers=headers).status_code,422)
        with patch('web_app.hosted_completion',return_value='{"ready":true}'):
            r=self.client.post('/api/model',json={**bad,'base_url':'https://openrouter.ai/api/v1'},headers=headers)
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['mode'],'cloud')
        self.assertNotIn(bad['key'],r.text)
        self.assertNotIn(bad['key'].encode(),self.path.read_bytes())
        self.assertEqual(self.client.request('DELETE','/api/model',json={},headers=headers).status_code,200)

    def test_model_failure_clear_and_fallback(self):
        app=create_app(self.path,password='',public_url='',credentials={'key':'mock-only-123','model':'mock','base_url':'https://openrouter.ai/api/v1'})
        with TestClient(app) as client:
            state=client.get('/api/state').json()
            with patch('cloud_llm.hosted_completion',side_effect=RuntimeError('private-secret')):
                r=client.post('/api/message',headers={'X-CSRF-Token':state['csrf']},json={'text':BENIGN,'request_id':'failure-request-123','revision':0})
            self.assertEqual(r.status_code,200)
            self.assertTrue(r.json()['last_error'])
            self.assertIsNone(r.json()['report']['risk']['risk_index'])
            self.assertNotIn('private-secret',r.text)

    def test_greeting_and_new_fact_after_report(self):
        self.send('مرحبا')
        self.assertIsNone(self.state['case_id'])
        self.send(BENIGN)
        case=self.state['case_id']
        self.send('تطور جديد: هددني بالقتل. أنا في خطر مباشر الآن.')
        self.assertEqual(self.state['case_id'],case)
        self.assertEqual(self.state['report']['risk']['review_priority'],'immediate')

    def test_retention(self):
        store=WebStore(self.path,retention_hours=1)
        token,_=store.create()
        with store.db() as db: db.execute('UPDATE web_sessions SET updated=0 WHERE id=?',(store.identifier(token),))
        self.assertIsNone(store.load(token))
        store.purge()

    def test_chunked_body_is_bounded(self):
        r=self.client.post('/api/login',content=iter([b'{"password":"',b'x'*20000,b'"}']),headers={'Content-Type':'application/json'})
        self.assertEqual(r.status_code,413)

    def test_help_does_not_delay_urgent_safety(self):
        self.send(THREAT)
        self.send('ليش سألتني؟ أنا في خطر مباشر الآن.')
        self.assertEqual(self.state['report']['risk']['review_priority'],'immediate')

    def test_parallel_answers_cannot_bind_to_unseen_question(self):
        async def exercise():
            def slow(session,*args): time.sleep(.08); return mock_extract(session)
            app=create_app(Path(self.temp.name)/'parallel.sqlite3',password='',public_url='',extractor=slow)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://testserver') as client:
                state=(await client.get('/api/state')).json()
                headers={'X-CSRF-Token':state['csrf']}
                payload={'action':'message','text':THREAT,'revision':0,'request_id':'parallel-first-0001'}
                responses=await asyncio.gather(client.post('/api/message',json=payload,headers=headers),
                    client.post('/api/message',json={**payload,'text':'لا','request_id':'parallel-second-0002'},headers=headers))
                self.assertEqual(sorted(r.status_code for r in responses),[200,409])
                state=(await client.get('/api/state')).json()
                self.assertEqual(state['answered'],0)
        asyncio.run(exercise())

    def test_model_credentials_do_not_use_process_globals(self):
        from web_dialogue import WebDialogue
        from domain import Session
        response=httpx.Response(200,json={'choices':[{'message':{'content':'{"facts":[]}'},'finish_reason':'stop'}]},request=httpx.Request('POST','https://provider.invalid/v1'))
        with patch('httpx.post',return_value=response) as call:
            for key in ('fake-first-user-key','fake-second-user-key'):
                dialogue=WebDialogue({'key':key,'model':'mock/model','base_url':'https://provider.invalid/v1'})
                dialogue.extract(Session(narrative=BENIGN),'mock/model',False)
                self.assertEqual(call.call_args.kwargs['headers']['Authorization'],'Bearer '+key)

if __name__=='__main__': unittest.main()
