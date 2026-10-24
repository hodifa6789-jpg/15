import asyncio
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import AsyncMock,patch
import httpx
from telegram import Update
from cloud_llm import hosted_completion,LLMUnavailable
from config import Settings
from persistence import SessionStore
from service import InterviewService
from grounding import collected_facts
from telegram_bot import text_message,start,id_command,report_command,callback,chunks,is_permitted,build_application

ENV={'LLM_API_KEY':'test-placeholder','LLM_JSON_MODE':'auto','LLM_MAX_ATTEMPTS':'3','LLM_TIMEOUT_SECONDS':'5',
     'LLM_BASE_URL':'https://provider.invalid/v1'}
def response(status=200,body=None):
    return httpx.Response(status,json=body if body is not None else {'choices':[{'message':{'content':'{"facts": []}'},'finish_reason':'stop'}]},
        request=httpx.Request('POST','https://provider.invalid/v1/chat/completions'))

class GatewayTests(unittest.TestCase):
    def test_retry_transient_bounded(self):
        with patch.dict(os.environ,ENV),patch('httpx.post',side_effect=[response(503),response()]) as call,patch('cloud_llm.time.sleep'):
            self.assertEqual(json.loads(hosted_completion('sys','text','test',{})),{'facts':[]})
        self.assertEqual(call.call_count,2)

    def test_timeout_bounded(self):
        with patch.dict(os.environ,ENV),patch('httpx.post',side_effect=httpx.ReadTimeout('private body')) as call,patch('cloud_llm.time.sleep'):
            with self.assertRaises(LLMUnavailable) as error: hosted_completion('sys','secret','test',{})
        self.assertEqual(call.call_count,3)
        self.assertNotIn('private',str(error.exception))
        self.assertNotIn('secret',str(error.exception))

    def test_unauthorized_not_retried(self):
        with patch.dict(os.environ,ENV),patch('httpx.post',return_value=response(401)) as call:
            with self.assertRaises(LLMUnavailable): hosted_completion('sys','text','test',{})
        self.assertEqual(call.call_count,1)

    def test_schema_fallback_only_when_unsupported(self):
        calls=[response(400,{'error':'response_format json_schema not supported'}),response()]
        with patch.dict(os.environ,ENV),patch('httpx.post',side_effect=calls) as call,patch('cloud_llm.time.sleep'):
            hosted_completion('sys','text','test',{})
        self.assertEqual(call.call_args_list[0].kwargs['json']['response_format']['type'],'json_schema')
        self.assertEqual(call.call_args_list[1].kwargs['json']['response_format']['type'],'json_object')

    def test_unsupported_model_400_is_not_hidden(self):
        with patch.dict(os.environ,ENV),patch('httpx.post',return_value=response(400,{'error':'unknown model'})) as call:
            with self.assertRaises(LLMUnavailable): hosted_completion('sys','text','test',{})
        self.assertEqual(call.call_count,1)

    def test_truncated_json_is_failure(self):
        value=response(body={'choices':[{'message':{'content':'{"facts": []}'},'finish_reason':'length'}]})
        with patch.dict(os.environ,ENV),patch('httpx.post',return_value=value):
            with self.assertRaises(LLMUnavailable): hosted_completion('sys','text','test',{})

    def test_malformed_content_is_failure(self):
        for content in ('not JSON','',{'facts':[]}):
            with self.subTest(content=content),patch.dict(os.environ,ENV),patch('httpx.post',return_value=response(body={'choices':[{'message':{'content':content}}]})):
                with self.assertRaises(LLMUnavailable): hosted_completion('sys','text','test',{})

    def test_unsafe_provider_url_rejected(self):
        for value in ('http://host/v1','https://user:secret@host/v1','https://host/v1?key=secret'):
            with self.subTest(value=value),patch.dict(os.environ,{**ENV,'LLM_BASE_URL':value}):
                with self.assertRaises(RuntimeError): hosted_completion('sys','text','test',{})

    def test_empty_allowlist_is_locked_bootstrap(self):
        with patch.dict(os.environ,{'TELEGRAM_BOT_TOKEN':'100:demo','ALLOWED_TELEGRAM_IDS':'','LLM_API_KEY':'','LLM_MODEL':''},clear=True):
            settings=Settings.from_env()
        self.assertEqual(settings.allowed,frozenset())

    def test_invalid_allowlist_rejected(self):
        for value in ('abc','0','-10'):
            with self.subTest(value=value),patch.dict(os.environ,{'TELEGRAM_BOT_TOKEN':'100:demo','ALLOWED_TELEGRAM_IDS':value},clear=True):
                with self.assertRaises(RuntimeError): Settings.from_env()

    def test_missing_railway_volume_fails_closed(self):
        with patch.dict(os.environ,{'TELEGRAM_BOT_TOKEN':'100:demo','RAILWAY_ENVIRONMENT_ID':'example'},clear=True):
            with self.assertRaisesRegex(RuntimeError,'volume'): Settings.from_env()

class AdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=SessionStore(Path(self.tmp.name)/'sessions.sqlite3')
        self.bot=AsyncMock()
        self.bot.id=999
        self.bot.defaults=None
        self.service=InterviewService(self.store,'mock',lambda session,*a:collected_facts(session))
        self.context=types.SimpleNamespace(application=types.SimpleNamespace(bot_data={'allowed':{123},'service':self.service}),bot=self.bot)
    def tearDown(self): self.tmp.cleanup()

    def update(self,text,uid=123,mid=1,chat_type='private'):
        return Update.de_json({'update_id':mid,'message':{'message_id':mid,'date':1700000000,
            'chat':{'id':uid,'type':chat_type},'from':{'id':uid,'is_bot':False,'first_name':'Synthetic'},'text':text}},self.bot)

    async def test_real_update_adapter_to_document(self):
        await start(self.update('/new',mid=1),self.context)
        await text_message(self.update('خلاف على الموقف وانتهى الخلاف. بدي نصائح وما بدي بلاغ.',mid=2),self.context)
        self.assertEqual(self.bot.send_document.await_count,3)
        names=[c.kwargs['filename'] for c in self.bot.send_document.await_args_list]
        self.assertTrue(any(n.endswith('.json') for n in names))
        self.assertEqual(self.service.snapshot(123)[0].stage,'complete')

    async def test_update_through_real_extractor_and_mock_hosted_api(self):
        from nlp import extract
        self.service.extractor=extract
        narrative='خلاف على الموقف وانتهى الخلاف. بدي نصائح.'
        payload={'choices':[{'message':{'content':json.dumps({'facts':[{
            'kind':'ordinary_dispute','polarity':'reported','quote':'خلاف على الموقف',
            'source':'initial_statement','temporal_context':'unspecified'}]},ensure_ascii=False)},'finish_reason':'stop'}]}
        await start(self.update('/new',mid=1),self.context)
        with patch.dict(os.environ,{**ENV,'LLM_PROVIDER':'cloud'}),patch('httpx.post',return_value=response(body=payload)) as call:
            await text_message(self.update(narrative,mid=2),self.context)
        self.assertEqual(call.call_count,1)
        self.assertEqual(self.bot.send_document.await_count,3)
        self.assertEqual(self.service.snapshot(123)[0].session.analysis_mode,'hosted_llm')

    async def test_malicious_model_proposal_is_quarantined(self):
        from nlp import extract
        self.service.extractor=extract
        narrative='ما هددني. خلاف على الموقف وانتهى الخلاف. بدي نصائح.'
        payload={'choices':[{'message':{'content':json.dumps({'facts':[{
            'kind':'death_threat','polarity':'reported','quote':'ما هددني','source':'initial_statement'}]})}}]}
        await start(self.update('/new',mid=1),self.context)
        with patch.dict(os.environ,{**ENV,'LLM_PROVIDER':'cloud'}),patch('httpx.post',return_value=response(body=payload)):
            await text_message(self.update(narrative,mid=2),self.context)
        session=self.service.snapshot(123)[0].session
        self.assertNotIn('تهديد خطير بالقتل',session.report['classification'])
        self.assertGreater(len(session.rejected_proposals),0)

    async def test_model_invalid_schema_degrades_to_review(self):
        from nlp import extract
        self.service.extractor=extract
        await start(self.update('/new',mid=1),self.context)
        bad={'choices':[{'message':{'content':'{"facts":[{"kind":"illegal","polarity":"verified","quote":3}]}'}}]}
        with patch.dict(os.environ,{**ENV,'LLM_PROVIDER':'cloud'}),patch('httpx.post',return_value=response(body=bad)):
            await text_message(self.update('خلاف وانتهى الخلاف. بدي نصائح.',mid=2),self.context)
        self.assertIsNone(self.service.snapshot(123)[0].session.report['risk']['risk_index'])

    async def test_unallowed_can_only_get_id(self):
        await id_command(self.update('/id',uid=444),self.context)
        self.assertIn('444',self.bot.send_message.await_args.kwargs['text'])
        await start(self.update('/new',uid=444,mid=2),self.context)
        self.assertIsNone(self.store.load(444)[0])
        self.assertEqual(self.bot.send_document.await_count,0)

    async def test_group_is_not_permitted(self):
        value=self.update('/new',chat_type='group')
        self.assertFalse(is_permitted(value,{123}))
        await start(value,self.context)
        self.bot.send_message.assert_not_awaited()

    async def test_duplicate_update_does_not_resend_report(self):
        await start(self.update('/new',mid=1),self.context)
        u=self.update('خلاف وانتهى الخلاف. بدي نصائح.',mid=2)
        await text_message(u,self.context)
        count=self.bot.send_document.await_count
        await text_message(u,self.context)
        self.assertEqual(count,self.bot.send_document.await_count)

    async def test_failure_during_send_leaves_outbox(self):
        await start(self.update('/new',mid=1),self.context)
        self.bot.send_document.side_effect=RuntimeError('offline')
        with self.assertRaises(RuntimeError):
            await text_message(self.update('خلاف وانتهى الخلاف. بدي نصائح.',mid=2),self.context)
        self.assertFalse(self.store.seen(123,2)['delivered'])
        self.assertEqual(self.service.snapshot(123)[0].stage,'complete')

    async def test_oversize_message_does_not_reach_extractor(self):
        await start(self.update('/new',mid=1),self.context)
        self.service.extractor=lambda *a: self.fail('Oversize inference')
        await text_message(self.update('س'*3001,mid=2),self.context)
        self.assertIn('3000',self.bot.send_message.await_args.kwargs['text'])

    def test_emoji_chunks_obey_utf16_budget(self):
        value='😀'*4096+'سلام'
        parts=list(chunks(value))
        self.assertEqual(''.join(parts),value)
        self.assertTrue(all(len(p.encode('utf-16-le'))//2<=3600 for p in parts))

    async def test_application_registers_all_commands(self):
        settings=Settings('100:demo',frozenset({123}),'mock',self.store.path,24,2)
        app=build_application(settings,self.store)
        commands=set().union(*(getattr(handler,'commands',set()) for handler in app.handlers[0]))
        self.assertTrue({'start','new','help','status','report','cancel','reset','id'}<=commands)

if __name__=='__main__': unittest.main()
