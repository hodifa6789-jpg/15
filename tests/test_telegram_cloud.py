"""Cloud and Telegram state-machine regression tests. No network or real tokens."""
from __future__ import annotations
import json
import os
import unittest
from unittest.mock import patch
from bot_flow import InterviewFlow
from cloud_llm import hosted_completion
from engine import Session
from nlp import extract

DISPUTE = ('أمس العصر صار خلاف بسيط بيني وبين جاري بسبب موقف السيارة. '
           'صار نقاش، بس ما هددني ولا اعتدى عليّ، وما صار أي ضرر أو خسارة. '
           'حرك سيارته وانتهى الخلاف، وأنا مش خايف منه وما في أي خطر عليّ حاليًا.')
EXTORTION = 'شخص هددني إنه إذا ما حولت 500 دينار رح ينشر صوري.'


def dispute_stub(session, model, final):
    return [{'kind':'ordinary_dispute','polarity':'reported',
             'quote':'خلاف بسيط بيني وبين جاري بسبب موقف السيارة',
             'source':'initial_statement', 'verification':'unverified'}]


def high_stub(session, model, final):
    return [
        {'kind':'extortion','polarity':'reported','quote':'شخص هددني إنه إذا ما حولت 500 دينار رح ينشر صوري.',
         'source':'initial_statement', 'verification':'unverified'},
        {'kind':'publication_threat','polarity':'reported','quote':'رح ينشر صوري',
         'source':'initial_statement', 'verification':'unverified'},
    ]


class FlowTests(unittest.TestCase):
    def test_resolved_dispute_asks_goal_then_finishes(self):
        flow = InterviewFlow()
        flow.new()
        reply = flow.receive(DISPUTE, 'test', extractor=dispute_stub)
        self.assertIn('شو بتحب', reply.messages[-1])
        reply = flow.receive('بدي نصائح وما بدي بلاغ', 'test', extractor=dispute_stub)
        self.assertTrue(reply.report_ready)
        self.assertEqual(flow.stage, 'complete')
        self.assertEqual(len(flow.session.answers), 1)
        self.assertEqual(flow.session.report['classification'], ['خلاف عادي'])
        self.assertEqual(flow.session.report['risk']['risk_index'], 8)
        self.assertNotIn('ذكر شعوره بالخوف', flow.session.report['summary'])

    def test_not_offering_draft_for_low_risk(self):
        flow = InterviewFlow()
        flow.new()
        flow.receive(DISPUTE, 'test', extractor=dispute_stub)
        reply = flow.receive('بدي نصائح', 'test', extractor=dispute_stub)
        self.assertNotIn('مسودة بلاغ', '\n'.join(reply.messages))

    def test_new_resets_state(self):
        flow = InterviewFlow()
        flow.new()
        flow.receive(DISPUTE, 'test', extractor=dispute_stub)
        flow.new()
        self.assertIsNone(flow.session)
        self.assertEqual(flow.stage,'narrative')

    def test_high_risk_prompts_draft_and_never_submits(self):
        flow=InterviewFlow()
        flow.new()
        flow.receive(EXTORTION, 'test', extractor=high_stub)
        reply = None
        for _ in range(8):
            if flow.stage != 'answering':
                break
            key = flow.session.answers[-1].key if flow.session.answers else None
            reply=flow.receive('ما بعرف', 'test', extractor=high_stub)
        self.assertEqual(flow.stage,'draft_consent')
        self.assertTrue(reply.report_ready)
        answer=flow.receive('نعم', 'test', extractor=high_stub)
        self.assertTrue(answer.draft_ready)
        self.assertEqual(flow.session.official_request,'draft_requested_not_submitted')
        self.assertEqual(flow.stage,'complete')

    def test_high_risk_declining_draft(self):
        flow=InterviewFlow()
        flow.new()
        flow.receive(EXTORTION, 'test', extractor=high_stub)
        for _ in range(8):
            if flow.stage != 'answering': break
            flow.receive('ما بعرف','test',extractor=high_stub)
        flow.receive('لا', 'test', extractor=high_stub)
        self.assertEqual(flow.session.official_request, 'declined_or_deferred')
        self.assertEqual(flow.stage, 'complete')

    def test_nonconsent_does_not_generate_draft(self):
        flow=InterviewFlow()
        flow.new()
        flow.receive(EXTORTION, 'test', extractor=high_stub)
        for _ in range(8):
            if flow.stage != 'answering': break
            flow.receive('ما بعرف', 'test', extractor=high_stub)
        reply=flow.receive('maybe', 'test', extractor=high_stub)
        self.assertFalse(reply.draft_ready)
        self.assertEqual(flow.stage,'draft_consent')

    def test_direct_danger_aborts_questions(self):
        flow=InterviewFlow()
        flow.new()
        reply=flow.receive('أنا في خطر مباشر هسا بسبب خلاف', 'test', extractor=lambda *args: [])
        self.assertTrue(reply.report_ready)
        self.assertNotEqual(flow.stage,'answering')
        self.assertIn('تنبيه سلامة', '\n'.join(reply.messages))

    def test_model_failure_is_not_disguised_as_success(self):
        def failing(*args): raise RuntimeError('outage')
        flow=InterviewFlow()
        flow.new()
        reply=flow.receive(DISPUTE, 'test', extractor=failing)
        self.assertIn('تعذّر التحليل', '\n'.join(reply.messages))
        for _ in range(8):
            if flow.stage != 'answering':break
            reply=flow.receive('ما بعرف', 'test', extractor=failing)
        self.assertTrue(reply.report_ready)
        self.assertFalse(flow.session.report['risk']['model_extraction_succeeded'])
        self.assertIsNone(flow.session.report['risk']['risk_index'])

    def test_idle_cannot_ingest_unrequested_text(self):
        flow=InterviewFlow()
        reply=flow.receive('نص غير مطلوب', 'test', extractor=dispute_stub)
        self.assertIsNone(flow.session)
        self.assertIn('/new',reply.messages[0])


class HostedLLMTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'LLM_API_KEY':'mock-only-not-real',
            'LLM_BASE_URL':'https://openrouter.ai/api/v1','LLM_JSON_MODE':'json_schema'})
        self.env.start()
    def tearDown(self): self.env.stop()

    def test_https_required(self):
        with patch.dict(os.environ, {'LLM_BASE_URL':'http://nonsecure.example/v1'}):
            with self.assertRaisesRegex(RuntimeError,'HTTPS'):
                hosted_completion('sys','text','test',{'type':'object'})

    def test_missing_key_fails_closed(self):
        with patch.dict(os.environ,{'LLM_API_KEY':''}):
            with self.assertRaisesRegex(RuntimeError,'LLM_API_KEY'):
                hosted_completion('sys','text','test',{'type':'object'})

    def test_correct_request_and_json_answer(self):
        class Response:
            def raise_for_status(self): pass
            def json(self): return {'choices':[{'message':{'content':'{"facts": []}'}}]}
        with patch('httpx.post', return_value=Response()) as call:
            raw=hosted_completion('sys','text','qwen/qwen3.5-9b',{'type':'object'})
        self.assertEqual(raw,'{"facts": []}')
        args,kwargs=call.call_args
        self.assertEqual(args[0],'https://openrouter.ai/api/v1/chat/completions')
        self.assertEqual(kwargs['json']['response_format']['type'],'json_schema')
        self.assertEqual(kwargs['json']['model'],'qwen/qwen3.5-9b')
        self.assertNotIn('mock-only-not-real', json.dumps(kwargs['json']))

    def test_empty_provider_response_is_error(self):
        class Response:
            def raise_for_status(self): pass
            def json(self): return {'choices':[{'message':{'content':''}}]}
        with patch('httpx.post', return_value=Response()):
            with self.assertRaisesRegex(RuntimeError,'JSON'):
                hosted_completion('sys','text','test',{'type':'object'})

    def test_cloud_extractor_preserves_existing_grounding(self):
        narrative='صارت مشكلة مع جاري وما هددني.'
        class Response:
            def raise_for_status(self): pass
            def json(self): return {'choices':[{'message':{'content':json.dumps({'facts':[
                {'kind':'verbal_threat','polarity':'reported','quote':'كلمات مختلقة لا توجد في الأصل'},
                {'kind':'verbal_threat','polarity':'denied','quote':'ما هددني'}
            ]})}}]}
        with patch.dict(os.environ, {'LLM_PROVIDER':'cloud'}), patch('httpx.post',return_value=Response()):
            out=extract(Session(narrative=narrative),'fake',False)
        self.assertFalse(any(x['quote']=='كلمات مختلقة لا توجد في الأصل' for x in out))
        self.assertIn(('verbal_threat','denied'),{(x['kind'],x['polarity']) for x in out})

if __name__=='__main__': unittest.main()
