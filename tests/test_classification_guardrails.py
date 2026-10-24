"""No-network targeted diagnosis of explicit Arabic statement classifications."""
import sys
import types
import json
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from classification_backstop import explicit_category_candidates, merge_categories
from engine import Session, assemble_report
from nlp import extract

class GuardrailTests(unittest.TestCase):
    def codes(self, sentence):
        return {x['kind'] for x in explicit_category_candidates({'initial_statement':sentence})}

    def test_money_for_nonpublication(self):
        self.assertTrue({'extortion','publication_threat'} <= self.codes('شخص هددني ينشر صوري إذا ما دفعتله 300 دينار'))

    def test_publication_threat_without_money_no_extortion(self):
        c = self.codes('هددني ينشر معلوماتي الخاصة بكرة')
        self.assertIn('publication_threat', c)
        self.assertNotIn('extortion', c)

    def test_alleged_death_threat_not_generic_verbal_only(self):
        t = 'شخص قال لي رح أقتلك عندما تخرج من المدرسة'
        f = merge_categories([{'kind':'verbal_threat','polarity':'reported','quote':t,
                               'source':'initial_statement'}], {'initial_statement':t})
        self.assertIn('death_threat', {x['kind'] for x in f})
        self.assertNotIn('verbal_threat', {x['kind'] for x in f})

    def test_dispute_with_denied_threat_remains_dispute(self):
        self.assertIn('ordinary_dispute', self.codes('بيننا خلاف حول تنظيف العمارة لكن ما هددني ولا آذاني'))

    def test_dispute_without_risk(self):
        self.assertIn('ordinary_dispute', self.codes('اختلفنا على مكان الوقوف في الحارة'))

    def test_no_dispute_when_negated(self):
        self.assertNotIn('ordinary_dispute', self.codes('ما صار بينا خلاف على السيارة'))

    def test_unauthorized_debit_not_takeover(self):
        t='انخصم مبلغ من حسابي البنكي بدون إذني، ولم أعط كلمة مروري لحدا'
        f = merge_categories([{'kind':'account_takeover','polarity':'reported','quote':'لم أعط كلمة مروري لحدا','source':'initial_statement'}], {'initial_statement':t})
        self.assertIn('unauthorized_debit', {x['kind'] for x in f})
        self.assertNotIn('account_takeover', {x['kind'] for x in f})

    def test_explicit_account_takeover_kept(self):
        t='دخل على حسابي وغير كلمة السر وصار يتحكم فيه'
        f=merge_categories([{'kind':'account_takeover','polarity':'reported','quote':'دخل على حسابي','source':'initial_statement'}], {'initial_statement':t})
        self.assertIn('account_takeover',{x['kind'] for x in f})

    def test_normal_debit_is_not_unauthorized(self):
        self.assertNotIn('unauthorized_debit', self.codes('انخصم مبلغ من حسابي البنكي بعد عملية شراء وافقت عليها'))

    def test_negated_account_access_not_confirmed(self):
        t='ما دخل على حسابي أحد، بس نسيت كلمة السر'
        f=merge_categories([{'kind':'account_takeover','polarity':'reported','quote':'دخل على حسابي','source':'initial_statement'}], {'initial_statement':t})
        self.assertNotIn('account_takeover',{x['kind'] for x in f})

    def test_hypothetical_theater_no_death(self):
        self.assertNotIn('death_threat',self.codes('في المسرحية قال لي رح أقتلك'))

    def test_reported_accusation_no_threat(self):
        self.assertNotIn('death_threat',self.codes('قال أحدهم إني هددته، بس أنا لم أفعل'))

    def test_agreed_payment_not_extortion(self):
        self.assertNotIn('extortion',self.codes('اتفقت أدفع للمحل 100 دينار مقابل إصلاح الجهاز'))

    def test_no_network_model_empty_uses_cautious_backstop(self):
        text='حدا هددني ينشر صوري إذا ما حولتله 400 دينار'
        fake_module=types.SimpleNamespace(Client=lambda **kwargs: types.SimpleNamespace(
            chat=lambda **kwargs: types.SimpleNamespace(message=types.SimpleNamespace(content=json.dumps({'facts': []})))))
        with patch.dict(sys.modules, {'ollama':fake_module}):
            facts=extract(Session(narrative=text), 'fake', False)
        self.assertTrue({'extortion','publication_threat'} <= {x['kind'] for x in facts})
        self.assertTrue(all(x['verification']=='unverified' for x in facts))
        self.assertTrue(all(x.get('extraction_method')=='explicit_rule_candidate' for x in facts if x['kind'] in {'extortion','publication_threat'}))

    def test_direct_death_threat_requires_priority_human_review(self):
        session=Session(narrative='شخص قال لي رح أقتلك إذا طلعت من البيت')
        session.answers=[]
        report=assemble_report(session)
        self.assertIn('تهديد خطير بالقتل', report['classification'])
        self.assertEqual(report['risk']['route'], 'priority_human_review')
        self.assertFalse(report['risk']['urgent'])  # Priority review is not a claim of immediate danger.

    def test_false_publication_threat_in_denial_is_rejected(self):
        t='صار خلاف، لكن ما هددني ينشر صوري'
        original=[{'kind':'publication_threat','polarity':'reported','quote':'هددني ينشر صوري','source':'initial_statement'}]
        out=merge_categories(original, {'initial_statement':t})
        self.assertNotIn('publication_threat',{x['kind'] for x in out})

    def test_report_never_marks_rule_as_verified(self):
        session=Session(narrative='شخص هددني ينشر صوري إذا ما دفعتله 200 دينار')
        session.answers=[]
        report=assemble_report(session)
        self.assertIn('ابتزاز مزعوم',report['classification'])
        self.assertTrue(all(not x['verified'] and x['review']=='pending' for x in report['source_anchored_facts']))

if __name__=='__main__':
    unittest.main()
