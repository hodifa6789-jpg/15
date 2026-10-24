import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import QA, Session, covered, next_question, assess, assemble_report, is_direct_danger
from grounding import normalize_facts, recover_explicit, source_texts
from storage import save, render_official_draft
from repair_existing import rebuild

NARRATIVE = ('اليوم العصر صار معي موقف مع شخص بعرفه. بعتلي رسالة على واتساب '
             'وطلب مني أحولله 500 دينار خلال 24 ساعة، وهددني إنه إذا ما حولت '
             'المصاري رح ينشر صوري الشخصية. أنا ما دفعتله أي مبلغ، ولحد الآن '
             'ما نشر الصور. عندي محادثات الواتساب محفوظة، لكن مش متأكد إذا '
             'هو فعلًا معه الصور أو بس بحاول يخوفني. وأنا قلقان إنه ينفذ تهديده.')
ANSWERS = [
    ('current_safety', 'لا، أنا مش في خطر مباشر هسا، وما بحتاج مساعدة عاجلة. التهديد كان عن طريق واتساب.'),
    ('media_possession', 'بصراحة، أنا مش متأكد إذا معه الصور فعلًا أو بس بحاول يخوفني.'),
    ('recurrence', 'هاي أول مرة يهددني فيها. كل الموضوع بدأ اليوم العصر برسالة واتساب.'),
    ('incident_time', 'الموضوع صار اليوم العصر، زي ما حكيتلك من أول الشكوى.'),
    ('actual_harm', 'لا، لحد الآن ما خسرت أي مصاري، والشخص ما نشر الصور. لكن صرت خايف إنه ينفذ كلامه.'),
    ('location', 'كل التواصل صار عن طريق واتساب، وما تقابلنا وجهًا لوجه.'),
]


def sample_session():
    s = Session(id='TEST-synthetic123', narrative=NARRATIVE)
    s.answers = [QA(k, '?', v) for k, v in ANSWERS]
    s.initial_facts = [
        {'kind':'extortion','polarity':'reported','quote':'طلب مني أحولله 500 دينار خلال 24 ساعة','source':'initial_statement'},
        {'kind':'publication_threat','polarity':'reported','quote':'هددني إنه إذا ما حولت المصاري رح ينشر صوري الشخصية','source':'initial_statement'},
        {'kind':'financial_fraud','polarity':'denied','quote':'ما دفعتله أي مبلغ','source':'initial_statement'},
        {'kind':'publication_occurred','polarity':'uncertain','quote':'مش متأكد إذا هو فعلًا معه الصور','source':'initial_statement'},
    ]
    s.final_facts = [
        *s.initial_facts[:3],
        {'kind':'publication_occurred','polarity':'denied','quote':'ما نشر الصور','source':'initial_statement'},
        {'kind':'harassment','polarity':'reported','quote':'صرت خايف إنه ينفذ كلامه','source':'answer_5'},
    ]
    s.official_request = 'draft_requested_not_submitted'
    return s


class UserCaseRegressionTests(unittest.TestCase):
    def test_time_today_afternoon_is_covered(self):
        s = Session(narrative=NARRATIVE)
        self.assertIn('incident_time', covered(s))
        self.assertIn('deadline', covered(s))
        self.assertIn('requested_action', covered(s))
        self.assertIn('media_possession', covered(s))
        self.assertIn('publication_occurred', covered(s))
        self.assertIn('evidence', covered(s))

    def test_questions_do_not_repeat_existing_time_or_possession(self):
        s = Session(narrative=NARRATIVE)
        questions = []
        for _ in range(6):
            key, question = next_question(s)
            questions.append(key)
            s.answers.append(QA(key, question, 'لا أعرف'))
        self.assertEqual(len(set(questions)), 6)
        self.assertNotIn('incident_time', questions)
        self.assertNotIn('media_possession', questions)
        self.assertNotIn('publication_occurred', questions)
        self.assertNotIn('evidence', questions)
        self.assertEqual(questions[0], 'current_safety')

    def test_payment_not_fraud(self):
        s = sample_session()
        f = normalize_facts(s.final_facts, source_texts(s))
        self.assertIn(('payment_made','denied'), {(x['kind'],x['polarity']) for x in f})
        self.assertNotIn(('financial_fraud','denied'), {(x['kind'],x['polarity']) for x in f})

    def test_fear_not_harassment(self):
        s = sample_session()
        report = assemble_report(s)
        self.assertNotIn('مضايقة', report['classification'])
        self.assertIn('ابتزاز مزعوم', report['classification'])
        self.assertIn(('reported_fear','reported'), {(x['kind'],x['polarity']) for x in report['source_anchored_facts']})

    def test_uncertain_possession_not_publication(self):
        s = sample_session()
        facts = normalize_facts(s.initial_facts, source_texts(s))
        self.assertIn(('media_possession','uncertain'), {(x['kind'],x['polarity']) for x in facts})
        self.assertNotIn(('publication_occurred','uncertain'), {(x['kind'],x['polarity']) for x in facts})

    def test_if_no_payment_is_not_payment_denial(self):
        s = Session(narrative='إذا ما دفعت 500 دينار رح ينشر صوري.')
        s.initial_facts = [{'kind':'payment_made','polarity':'denied','quote':'ما دفعت 500 دينار', 'source':'initial_statement'}]
        f = normalize_facts(s.initial_facts, source_texts(s))
        self.assertFalse(f)
        self.assertNotIn('payment_made', {x['kind'] for x in recover_explicit(source_texts(s))})

    def test_deadline_is_separate_and_explained(self):
        r = assess(sample_session())
        self.assertTrue(any(x['factor'] == 'short_deadline' for x in r['factor_breakdown']))
        self.assertTrue(any(x['factor'] == 'extortion' for x in r['factor_breakdown']))
        self.assertEqual(r['risk_index'], sum(x['contribution'] for x in r['factor_breakdown']))
        self.assertFalse(r['urgent'])

    def test_report_has_structured_facts_and_summary(self):
        report = assemble_report(sample_session())
        self.assertNotEqual(report['summary'], NARRATIVE)
        self.assertIn('threat_deadline', report['structured_details'])
        self.assertIn('time', report['structured_details'])
        self.assertEqual(report['structured_details']['payment_made']['polarity'], 'denied')
        self.assertFalse(any(x['verified'] for x in report['source_anchored_facts']))

    def test_draft_created_only_on_request(self):
        s = sample_session()
        assemble_report(s)
        with tempfile.TemporaryDirectory() as td, patch('storage.output_dir', return_value=Path(td)):
            files = save(s)
            drafts = [p for p in files if p.name.endswith('.official_draft.md')]
            self.assertEqual(len(drafts), 1)
            draft = drafts[0].read_text(encoding='utf-8')
            self.assertIn('لم تُقدَّم رسميًا', draft)
            self.assertIn('500 دينار', draft)
            self.assertIn('24 ساعة', draft)

    def test_rebuild_does_not_change_original(self):
        s = sample_session()
        assemble_report(s)
        with tempfile.TemporaryDirectory() as td, patch('storage.output_dir', return_value=Path(td)):
            original = save(s)[0]
            before = original.read_bytes()
            outputs = rebuild(original)
            self.assertEqual(before, original.read_bytes())
            self.assertEqual(len(outputs), 4)
            fixed = json.loads(next(x for x in outputs if x.name.endswith('.reviewed.json')).read_text(encoding='utf-8'))
            self.assertNotIn('مضايقة', fixed['report']['classification'])
            self.assertEqual(fixed['report']['official_report_status'], 'draft_requested_not_submitted')

    def test_new_imminence_not_ignored_by_earlier_negation(self):
        self.assertTrue(is_direct_danger('لا مش بخطر مباشر، بس الشخص واقف عند بابي هسا'))
        self.assertFalse(is_direct_danger('لا مش بخطر مباشر هسا'))
        self.assertFalse(is_direct_danger('الشخص مش واقف عند بابي'))

    def test_no_future_threat_as_current_danger(self):
        self.assertFalse(is_direct_danger('قال رح ينشر صوري خلال 24 ساعة'))

    def test_another_category_does_not_get_extortion_score(self):
        s = Session(narrative='صار خلاف بيني وبين جاري على موقف السيارة')
        s.final_facts = [{'kind':'ordinary_dispute','polarity':'reported','quote':'خلاف بيني وبين جاري', 'source':'initial_statement'}]
        s.answers.append(QA('current_safety','?','لا'))
        r = assess(s)
        self.assertLess(r['risk_index'], 30)


if __name__ == '__main__':
    unittest.main()
