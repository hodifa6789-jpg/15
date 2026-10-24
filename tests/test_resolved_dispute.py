"""Regression checks for ordinary, resolved disputes and explicit Arabic negation."""
from __future__ import annotations
import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch
from engine import Session, QA, next_question, covered, assemble_report, is_direct_danger
from grounding import collected_facts
from main import run

NARRATIVE = (
    'أمس العصر صار خلاف بسيط بيني وبين جاري بسبب موقف السيارة تحت العمارة. '
    'صار بينا نقاش وارتفعت أصواتنا شوي، لكنه ما هددني ولا اعتدى عليّ، '
    'وما صار أي ضرر أو خسارة. بالنهاية حرك سيارته وانتهى الخلاف. '
    'هاي أول مرة بصير بينا هيك موقف، وأنا مش خايف منه وما في أي خطر عليّ حاليًا.'
)


def explicit_mock(session, model, final):
    return [{
        'kind':'ordinary_dispute','polarity':'reported',
        'quote':'خلاف بسيط بيني وبين جاري بسبب موقف السيارة',
        'source':'initial_statement', 'verification':'unverified',
    }]


class ResolvedDisputeTests(unittest.TestCase):
    def test_existing_safety_and_harm_is_covered(self):
        keys = covered(Session(narrative=NARRATIVE))
        self.assertIn('current_safety',keys)
        self.assertIn('actual_assault',keys)
        self.assertIn('actual_harm',keys)
        self.assertIn('incident_time',keys)

    def test_benign_questions_are_about_user_goal(self):
        s = Session(narrative=NARRATIVE, initial_facts=explicit_mock(None,None,None))
        key, text = next_question(s)
        self.assertEqual(key,'desired_outcome')
        self.assertNotIn('تنفيذ تهديده',text)

    def test_early_finish_after_user_goal_answer(self):
        s = Session(narrative=NARRATIVE, initial_facts=explicit_mock(None,None,None))
        s.answers.append(QA('desired_outcome','?', 'بدي نصائح وما بدي أقدم بلاغ'))
        self.assertIsNone(next_question(s))

    def test_negative_fear_not_positive(self):
        s = Session(narrative=NARRATIVE, initial_facts=explicit_mock(None,None,None))
        facts=collected_facts(s)
        self.assertNotIn(('reported_fear','reported'), {(f['kind'],f['polarity']) for f in facts})
        self.assertIn(('reported_fear','denied'), {(f['kind'],f['polarity']) for f in facts})

    def test_raised_voices_not_automatic_harassment(self):
        s=Session(narrative=NARRATIVE, initial_facts=[
            {'kind':'harassment','polarity':'uncertain','quote':'ارتفعت أصواتنا شوي','source':'initial_statement'}])
        self.assertFalse(any(f['kind']=='harassment' for f in collected_facts(s)))

    def test_no_loss_does_not_deny_financial_fraud(self):
        s=Session(narrative=NARRATIVE,initial_facts=[
            {'kind':'financial_fraud','polarity':'denied','quote':'ما صار أي ضرر أو خسارة','source':'initial_statement'}])
        self.assertFalse(any(f['kind']=='financial_fraud' for f in collected_facts(s)))

    def test_summary_respects_negation_and_goal(self):
        s=Session(narrative=NARRATIVE,initial_facts=explicit_mock(None,None,None))
        s.answers.append(QA('desired_outcome','?','بدي نصائح حتى أتجنب تكرار الخلاف وما بدي أقدم بلاغ'))
        r=assemble_report(s)
        self.assertIn('خلاف كلامي',r['summary'])
        self.assertIn('نفى الشعور بالخوف',r['summary'])
        self.assertNotIn('ذكر شعوره بالخوف أو القلق',r['summary'])
        self.assertIn('لا يرغب في تقديم بلاغ',r['summary'])
        self.assertTrue(r['assisted_resolution_guidance'])
        self.assertEqual(r['classification'],['خلاف عادي'])
        self.assertEqual(r['risk']['risk_index'],8)
        self.assertEqual(r['risk']['route'],'assisted_resolution_pending_review')

    def test_threat_still_has_safety_first(self):
        s=Session(narrative='شخص يهددني بنشر صوري إذا ما حولتله 500 دينار')
        self.assertEqual(next_question(s)[0],'current_safety')
        self.assertFalse(is_direct_danger('أنا مش في خطر مباشر هسا'))

    def test_no_automatic_report_offer_for_resolved_dispute(self):
        outputs=[]
        values=iter([NARRATIVE,'بدي نصائح لتجنب الخلاف وما بدي أقدم بلاغ'])
        with tempfile.TemporaryDirectory() as tmp, patch('storage.output_dir', return_value=Path(tmp)):
            s=run('fake', extractor=explicit_mock,read=lambda _:next(values),write=outputs.append)
        self.assertEqual(len(s.answers),1)
        self.assertEqual(s.official_request,'not_asked')
        self.assertTrue(s.report['assisted_resolution_guidance'])
        self.assertNotIn('هل تريد إعداد مسودة بلاغ رسمي','\n'.join(outputs))

if __name__=='__main__':
    unittest.main()
