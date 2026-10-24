import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import Session, QA, next_question, covered, is_direct_danger, yes_no, assess, assemble_report
from main import run
from storage import render_html, render_markdown


def fake_extract(case, model, final):
    return [{'kind':'extortion','polarity':'reported',
             'quote':'يهددني ينشر صوري إذا ما حولتله 500 دينار',
             'source':'initial_statement','verification':'unverified'}]

class InterviewTests(unittest.TestCase):
    def test_first_question_from_threat_is_safety(self):
        s=Session(narrative='واحد يهددني ينشر صوري إذا ما حولتله 500 دينار')
        self.assertEqual(next_question(s)[0],'current_safety')

    def test_pivotal_media_question_asked_early(self):
        s=Session(narrative='في شخص يهددني ينشر صوري إذا ما حولتله 500 دينار')
        s.answers.append(QA('current_safety','?', 'لا'))
        self.assertEqual(next_question(s)[0],'publication_occurred')

    def test_known_media_publication_not_reasked(self):
        s=Session(narrative='هددني ينشر صوري بس لسه ما نشر الصور.')
        s.answers.append(QA('current_safety','?', 'لا'))
        self.assertNotEqual(next_question(s)[0],'publication_occurred')

    def test_known_evidence_not_reasked(self):
        s=Session(narrative='هددني ينشر صوري وعندي كل المحادثة محفوظة')
        self.assertIn('evidence',covered(s))

    def test_deadline_not_incident_time(self):
        s=Session(narrative='قال إذا ما دفعت خلال 24 ساعة رح ينشر صوري')
        self.assertNotIn('incident_time',covered(s))

    def test_no_duplicate_six_questions(self):
        s=Session(narrative='حدا هددني ينشر صوري إذا ما دفعت 500 دينار')
        for i in range(6):
            item=next_question(s)
            self.assertIsNotNone(item)
            s.answers.append(QA(item[0],item[1],'لا أعرف'))
        self.assertEqual(len(set(a.key for a in s.answers)),6)
        self.assertIsNone(next_question(s))

    def test_no_danger_negation(self):
        self.assertFalse(is_direct_danger('لا مش بخطر مباشر هسا'))
        self.assertTrue(is_direct_danger('أنا في خطر مباشر الآن'))
        self.assertEqual(yes_no('لا أعرف'),'unknown')

    def test_imminent_override_never_delayed(self):
        s=Session(narrative='أنا في خطر مباشر الآن')
        result=assess(s)
        self.assertTrue(result['urgent'])
        self.assertEqual(result['route'],'immediate_safety_screening')
        self.assertGreaterEqual(result['risk_index'],90)

    def test_extortion_report_without_verification(self):
        s=Session(narrative='حدا يهددني ينشر صوري إذا ما حولتله 500 دينار')
        s.final_facts = fake_extract(s,'unused',True)
        s.answers.append(QA('current_safety','?', 'لا'))
        report=assemble_report(s)
        self.assertIn('ابتزاز مزعوم',report['classification'])
        self.assertGreaterEqual(report['risk']['risk_index'],60)
        self.assertFalse(report['source_anchored_facts'][0]['verified'])

    def test_model_failure_cannot_be_presented_as_reliable(self):
        s=Session(narrative='خلاف عادي')
        s.answers.append(QA('current_safety','?', 'لا'))
        r=assemble_report(s,model_worked=False)['risk']
        self.assertEqual(r['route'],'insufficient_model_assessment')

    def test_html_escaping(self):
        s=Session(narrative='<script>alert(1)</script>')
        s.answers.append(QA('current_safety','?', 'لا'))
        r=assemble_report(s)
        result=render_html(r)
        self.assertNotIn('<script>',result)
        self.assertIn('&lt;script&gt;',result)

    def test_end_to_end_six_questions_and_draft_not_submitted(self):
        narrative='حدا يهددني ينشر صوري إذا ما حولتله 500 دينار'
        inputs=iter([narrative, 'لا','لا','نعم عنده الصور', 'عندي محادثات', 'مرتين', 'مبارح','نعم'])
        output=[]
        with tempfile.TemporaryDirectory() as td, patch('storage.output_dir', return_value=Path(td)):
            s=run('fake-model', extractor=fake_extract, read=lambda _:next(inputs), write=output.append)
        self.assertEqual(len(s.answers),6)
        self.assertEqual(s.official_request,'draft_requested_not_submitted')
        self.assertTrue(s.report)
        self.assertIn('لم يُرسل أي بلاغ', '\n'.join(output))

if __name__=='__main__':
    unittest.main()
