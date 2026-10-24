import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import Session, QA, assemble_report, assess
from timeline import chronological_state, timeline_facts

INITIAL = ('اليوم العصر صار معي موقف مع شخص بعرفه. بعتلي رسالة على واتساب وطلب مني أحولله 500 دينار خلال 24 ساعة، '
           'وهددني إنه إذا ما حولت المصاري رح ينشر صوري الشخصية. أنا ما دفعتله أي مبلغ، ولحد الآن ما نشر الصور. '
           'عندي محادثات الواتساب محفوظة، لكن مش متأكد إذا هو فعلًا معه الصور أو بس بحاول يخوفني. وأنا قلقان إنه ينفذ تهديده.')
QAS = [
    ('current_safety','لا، أنا مش في خطر مباشر هسا، وما بحتاج مساعدة عاجلة. التهديد كان عن طريق واتساب، لكن لسه خايف إنه ينشر الصور خلال المهلة اللي حددها.'),
    ('threat_capability','لا، ما عندي أي دليل إضافي إنه الشخص قادر ينفذ تهديده غير رسالة الواتساب. هو حددلي مهلة 24 ساعة، لكن ما بعتلي صور تثبت إنه يمتلكها، وما بعرف إذا هو جاد أو بس بحاول يخوفني.'),
    ('recurrence','هاي أول مرة يهددني فيها. قبل هيك ما طلب مني مصاري ولا هددني بنشر صوري، وكل الموضوع بدأ اليوم العصر لما بعتلي رسالة واتساب.'),
    ('actual_harm','لا، لحد هسا ما تعرضت لأي أذى جسدي، وما خسرت أي مصاري، وما حولت للشخص أي مبلغ. والصور لسه ما اننشرت حسب علمي. لكن التهديد سببلي خوف وقلق، خصوصًا إنه أعطاني مهلة 24 ساعة.'),
    ('change_factor','نعم، في تطور جديد صار بعد ما بلشنا المحادثة. الشخص رجع بعتلي رسالة على واتساب، وأرسل نسخة من إحدى صوري الشخصية، وقال إنه رح ينشرها الليلة إذا ما حولتله المصاري. قبل هالرسالة ما كنت متأكد إذا معه الصور، لكن هسا عندي الرسالة والصورة اللي بعتها محفوظتين. ولسه ما نشر إشي حسب علمي.'),
    ('current_status','نعم، التهديد لسه مستمر. آخر رسالة وصلتني كانت اليوم، لما بعتلي نسخة من الصورة وقال إنه رح ينشرها الليلة إذا ما حولتله الـ500 دينار. لحد هسا ما دفعتله أي مبلغ، وما نشر الصور حسب علمي، بس أنا خايف إنه ينفذ تهديده. وما وصلتني منه رسائل جديدة بعد آخر تهديد.'),
]

def fixture(include_update=True):
    s=Session(id='TEST-timeline001', narrative=INITIAL)
    for key,ans in (QAS if include_update else QAS[:4]):
        s.answers.append(QA(key,'?',ans))
    s.initial_facts = [
        {'kind':'extortion','polarity':'reported','quote':'طلب مني أحولله 500 دينار خلال 24 ساعة','source':'initial_statement'},
        {'kind':'publication_threat','polarity':'reported','quote':'هددني إنه إذا ما حولت المصاري رح ينشر صوري الشخصية','source':'initial_statement'},
        {'kind':'media_possession','polarity':'uncertain','quote':'مش متأكد إذا هو فعلًا معه الصور','source':'initial_statement'},
    ]
    s.final_facts = list(s.initial_facts)
    return s

class TimelineEscalationTests(unittest.TestCase):
    def test_new_copy_supersedes_earlier_uncertainty(self):
        s = fixture()
        r = assemble_report(s)
        self.assertEqual(r['structured_details']['media_possession']['polarity'], 'reported')
        self.assertEqual(r['structured_details']['media_possession']['source'], 'answer_6')
        self.assertIn('أرسل نسخة', r['summary'])
        self.assertNotIn('غير متأكد من امتلاك', r['summary'])
        self.assertTrue(any(f['kind']=='media_possession' and f['polarity']=='uncertain'
                            and f['superseded_in_current_state'] for f in r['source_anchored_facts']))

    def test_deadline_evolves_from_24h_to_tonight(self):
        r=assemble_report(fixture())
        self.assertIn('الليلة', r['structured_details']['threat_deadline']['quote'])
        self.assertIn('الليلة', r['summary'])
        self.assertIn('24 ساعة', r['summary'])

    def test_risk_reflects_update(self):
        before=assess(fixture(False))
        after=assess(fixture(True))
        self.assertEqual(before['risk_index'],73)
        self.assertGreater(after['risk_index'], before['risk_index'])
        self.assertEqual(after['route'],'priority_human_review')
        factors={b['factor'] for b in after['factor_breakdown']}
        self.assertTrue({'reported_copy_received','renewed_threat','new_near_term_deadline'} <= factors)
        self.assertEqual(after['risk_index'], sum(b['contribution'] for b in after['factor_breakdown']))
        self.assertFalse(after['urgent'])

    def test_original_sources_preserved_unverified(self):
        r=assemble_report(fixture())
        self.assertTrue(r['chronological_case_state']['events'])
        self.assertFalse(any(f['verified'] for f in r['source_anchored_facts']))
        self.assertTrue(all(f['review']=='pending' for f in r['source_anchored_facts']))
        self.assertTrue(all(f['quote'] in (fixture().narrative if f['source']=='initial_statement' else QAS[int(f['source'].split('_')[1])-1][1]) for f in r['source_anchored_facts']))

    def test_denied_copy_does_not_infer_possession(self):
        s = Session(narrative='شخص يهددني بنشر الصور لكن مش متأكد إذا معه الصور')
        s.answers.append(QA('threat_capability','?','ما بعتلي صور ولا أرسل نسخة من الصورة'))
        self.assertIsNone(chronological_state(s)['reported_copy'])

    def test_new_threat_without_new_copy(self):
        s = fixture(False)
        s.answers.append(QA('change_factor','?', 'رجع بعتلي رسالة وقال رح ينشرها الليلة'))
        state = chronological_state(s)
        self.assertIsNone(state['reported_copy'])
        self.assertTrue(state['renewed_threat'])
        self.assertTrue(state['near_term_threat'])

    def test_no_score_if_extractor_failed_but_new_threat_escalates_review(self):
        r=assess(fixture(),model_worked=False)
        self.assertIsNone(r['risk_index'])
        self.assertEqual(r['route'],'priority_human_review')

    def test_no_automatic_publication_from_sent_copy(self):
        s=fixture()
        r=assemble_report(s)
        self.assertFalse(any(f['kind']=='publication_occurred' and f['polarity']=='reported'
                             for f in r['source_anchored_facts']))
        self.assertTrue(any(f['kind']=='evidence' and 'نسخة' in f['quote']
                            for f in r['source_anchored_facts']))

if __name__=='__main__': unittest.main()

class ExtraSafetyRegressionTests(unittest.TestCase):
    def test_new_direct_danger_overrides_earlier_denial(self):
        s = fixture(False)
        s.answers.append(QA('change_factor', '?', 'هسا الشخص واقف عند بابي'))
        r=assess(s)
        self.assertTrue(r['urgent'])
        self.assertEqual(r['route'],'immediate_safety_screening')

    def test_deny_tonight_threat(self):
        s = Session(narrative='حدا هددني ينشر صوري')
        s.answers.append(QA('change_factor','?', 'ما قال رح ينشرها الليلة، بس هدّد بدون موعد.'))
        state=chronological_state(s)
        self.assertFalse(state['near_term_threat'])

    def test_copy_quote_captures_entire_word(self):
        r=assemble_report(fixture())
        self.assertEqual(r['structured_details']['media_possession']['quote'], 'بعتلي نسخة من الصورة')
