import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from bot_flow import InterviewFlow
from grounding import collected_facts
from persistence import SessionStore,ConcurrentWrite
from service import InterviewService
from worker_lock import WorkerLock

def local(session,*args): return collected_facts(session)

class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'sessions.sqlite3'
        self.store=SessionStore(self.path)
        self.service=InterviewService(self.store,'mock',local)
    def tearDown(self): self.tmp.cleanup()

    async def test_restart_restores_actual_question(self):
        await self.service.process(1,1,'',command='new',message_id=1)
        await self.service.process(1,2,'هددني بنشر صوري',message_id=2)
        before=self.service.snapshot(1)[0].session.pending_question
        other=InterviewService(SessionStore(self.path),'mock',local)
        self.assertEqual(other.snapshot(1)[0].session.pending_question,before)
        await other.process(1,3,'لا',message_id=3)
        self.assertEqual(other.snapshot(1)[0].session.answers[0].question_id,before['id'])

    async def test_duplicate_after_restart_does_not_apply_twice(self):
        await self.service.process(1,1,'',command='new')
        result=await self.service.process(1,2,'هددني بنشر صوري')
        self.store.delivered(1,2)
        other=InterviewService(SessionStore(self.path),'mock',local)
        duplicate=await other.process(1,2,'هددني بنشر صوري')
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(len(other.snapshot(1)[0].session.answers),0)

    async def test_undelivered_outbox_replays_without_reinference(self):
        await self.service.process(1,1,'',command='new')
        result=await self.service.process(1,2,'خلاف على الموقف وانتهى الخلاف. بدي نصائح.')
        self.assertTrue(result['documents'])
        other=InterviewService(SessionStore(self.path),'mock',lambda *a: self.fail('replayed inference'))
        self.assertEqual(await other.process(1,2,'same'),result)

    async def test_two_rapid_messages_do_not_answer_unseen_question(self):
        await self.service.process(1,1,'',command='new')
        def slow(session,*args):
            time.sleep(.05)
            return local(session)
        self.service.extractor=slow
        results=await asyncio.gather(self.service.process(1,2,'هددني بنشر صوري',message_id=2),
                                     self.service.process(1,3,'لا',message_id=3))
        self.assertIn('قبل عرض السؤال',results[1]['messages'][0])
        self.assertEqual(len(self.service.snapshot(1)[0].session.answers),0)

    async def test_separate_users_progress_concurrently(self):
        await self.service.process(1,1,'',command='new')
        await self.service.process(2,2,'',command='new')
        starts=[]
        def slow(session,*args):
            starts.append(time.monotonic()); time.sleep(.08)
            return local(session)
        self.service.extractor=slow
        await asyncio.gather(self.service.process(1,3,'خلاف على الموقف'),self.service.process(2,4,'ضربني'))
        self.assertLess(abs(starts[0]-starts[1]),.07)
        self.assertNotEqual(self.service.snapshot(1)[0].session.id,self.service.snapshot(2)[0].session.id)

    async def test_reset_deletes_case_and_old_responses(self):
        await self.service.process(1,1,'',command='new')
        await self.service.process(1,2,'خلاف على الموقف')
        await self.service.process(1,3,'',command='reset')
        self.assertIsNone(self.store.load(1)[0])
        self.assertTrue(self.store.seen(1,2)['delivered'])
        self.assertIsNone(self.store.seen(1,2)['result'])
        self.assertTrue(self.store.seen(1,3))

    async def test_stale_case_consent_is_rejected(self):
        await self.service.process(1,1,'',command='new')
        await self.service.process(1,2,'هددني بنشر صوري')
        result=await self.service.process(1,3,'yes',expected_case='TEST-old')
        self.assertIn('سابقة',result['messages'][0])
        self.assertEqual(self.service.snapshot(1)[0].session.official_request,'not_asked')

    async def test_document_not_exposed_to_other_user(self):
        await self.service.process(1,1,'',command='new')
        await self.service.process(1,2,'خلاف وانتهى الخلاف. بدي نصائح.')
        result=await self.service.process(2,3,'',command='report')
        self.assertFalse(result['documents'])

    async def test_user_length_validation_before_model(self):
        await self.service.process(1,1,'',command='new')
        result=await self.service.process(1,2,'س'*3001)
        self.assertIn('3000',result['messages'][0])
        self.assertIsNone(self.service.snapshot(1)[0].session)

    async def test_model_failure_persisted_not_hidden(self):
        await self.service.process(1,1,'',command='new')
        def failing(*args): raise RuntimeError('secret must not survive')
        self.service.extractor=failing
        result=await self.service.process(1,2,'خلاف وانتهى الخلاف. بدي نصائح.')
        self.assertTrue(result['report_ready'])
        flow=self.service.snapshot(1)[0]
        self.assertIsNone(flow.session.report['risk']['risk_index'])
        self.assertNotIn('secret',json.dumps(flow.to_dict()))

    def test_transaction_rejects_stale_revision(self):
        self.store.commit(1,1,InterviewFlow().to_dict(),{},0)
        with self.assertRaises(ConcurrentWrite):
            self.store.commit(1,2,InterviewFlow().to_dict(),{},0)
        self.assertIsNone(self.store.seen(1,2))

    def test_retention_erases_expired_records(self):
        self.store.commit(1,1,InterviewFlow().to_dict(),{},0)
        with self.store.connection() as db:
            db.execute('UPDATE sessions SET updated_at=0')
            db.execute('UPDATE updates SET created_at=0')
        self.store.purge()
        self.assertIsNone(self.store.load(1)[0])
        self.assertIsNone(self.store.seen(1,1))

    def test_singleton_process_lock(self):
        with WorkerLock(Path(self.tmp.name)/'test.lock'):
            with self.assertRaises(RuntimeError):
                with WorkerLock(Path(self.tmp.name)/'test.lock'): pass

if __name__=='__main__': unittest.main()
