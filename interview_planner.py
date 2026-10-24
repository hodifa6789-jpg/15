"""FactState -> missing information -> scored candidates -> explicit stop decision.

Utilities are documented engineering priorities, not learned probabilities or
statistical expected information gain. Question wording never controls planning.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from fact_state import FactState
from domain import LABELS

QUESTIONS = {
    'current_safety':'هل أنت في خطر مباشر الآن أو تحتاج مساعدة عاجلة؟',
    'publication_occurred':'هل نُشرت المواد بالفعل، أم لم تُنشر، أم لا تعرف؟',
    'media_possession':'شو بتعرف عن امتلاك الطرف الآخر للمواد؟ عادي إذا المعلومة غير مؤكدة.',
    'threat_capability':'هل عندك معلومات عن قدرة الطرف الآخر على الوصول إليك أو تنفيذ ما ورد في الرسالة؟',
    'evidence':'هل توجد رسائل أو شهود أو مستندات متعلقة بما حدث؟ لا ترسل بياناتها الحساسة.',
    'recurrence':'هل حدث التصرف مرة واحدة أم تكرر؟',
    'incident_time':'متى حدثت الواقعة تقريبًا؟ هذا مختلف عن أي مهلة ذُكرت.',
    'financial_loss':'هل حصلت خسارة مالية فعلية، أم لم تحصل، أم لا تعرف؟',
    'payment_made':'هل دفعت أو حولت أي مبلغ بالفعل؟',
    'injury':'هل حدثت إصابة جسدية، أم لم تحدث، أم لا تعرف؟',
    'account_access':'هل ما زال الطرف الآخر قادرًا على الدخول للحساب، أم استعدت التحكم، أم لا تعرف؟',
    'desired_outcome':'شو بتحب نساعدك فيه: نصائح، توثيق ما حدث، أم شيء آخر؟',
    'ongoing_issue':'هل انتهى الخلاف أم بقيت نقطة تحتاج حلًا؟',
    'current_status':'هل التصرف الذي وصفته مستمر أم توقف؟',
    'deadline':'هل ذُكر موعد أو مهلة في الرسالة؟',
    'requested_action':'ما الطلب الذي ورد، إن وُجد؟',
    'other_side':'شو التصرف المحدد اللي صار معك؟ اذكر ما حدث دون تفسير نوايا الآخرين.',
    'relationship':'ما علاقتك بالطرف الآخر؟',
}
SLOTS = {'current_safety':('immediate_danger',),'publication_occurred':('publication_occurred',),
    'media_possession':('media_possession',),'evidence':('evidence',),'recurrence':('recurrence',),
    'incident_time':('time',),'financial_loss':('financial_loss',),'payment_made':('payment_made',),
    'injury':('injury',),'account_access':('account_access',),
    'desired_outcome':('desired_outcome','declines_draft'),'ongoing_issue':('resolved',),
    'current_status':('resolved',),'deadline':('threat_deadline',),
    'requested_action':('amount_demanded',),'relationship':('relationship',),
    'threat_capability':('threat_capability',)}

@dataclass(frozen=True)
class Candidate:
    key: str
    target: str
    impact: int
    uncertainty: float
    burden: int
    reason: str
    text: str

    @property
    def utility(self):
        return round(self.impact*self.uncertainty-self.burden,2)

@dataclass
class Decision:
    selected: Candidate | None
    candidates: list[Candidate]
    stop_reason: str | None
    missing: list[str]

    def audit(self):
        return {'selected':self.selected.key if self.selected else None,
                'stop_reason':self.stop_reason,'missing':self.missing,
                'candidates':[dict(asdict(c),utility=c.utility) for c in self.candidates]}

def covered(session, state: FactState | None = None) -> set[str]:
    state=state or FactState.build(session)
    done={a.key for a in session.answers}
    for key,kinds in SLOTS.items():
        if any(state.known(k) for k in kinds):
            done.add(key)
    if state.status('verbal_threat')=='denied':
        done.add('threat_capability')
    if state.status('physical_assault')=='denied':
        done.update({'actual_assault','injury'})
    if state.known('injury') and state.known('financial_loss'):
        done.add('actual_harm')
    if state.known('resolved'):
        done.update({'current_status','ongoing_issue'})
    return done


def plan(session, total: int = 6) -> Decision:
    state=FactState.build(session)
    used=covered(session,state)
    codes=state.classifications
    candidates=[]
    threats=bool(codes & {'verbal_threat','death_threat','publication_threat','extortion'})
    harm=bool(codes & {'physical_assault','death_threat'})
    digital=bool(codes & {'extortion','publication_threat','publication_occurred','account_takeover','unauthorized_debit'})

    def add(key, impact, reason, target=None, text=None, force=False):
        if key in used and not force:
            return
        target=target or SLOTS.get(key,(key,))[0]
        status=state.status(target)
        uncertainty=1.0 if status=='unknown' else 0.8 if status=='conflicting' else 0.35
        candidates.append(Candidate(key,target,impact,uncertainty,3,reason,text or QUESTIONS[key]))

    if state.status('immediate_danger')=='reported':
        return Decision(None,[],'immediate_safety_guidance',[])
    for conflict in state.contradictions:
        kind=conflict['kind']
        key='clarify_'+kind
        if key not in used:
            add(key,135,'تعارض يؤثر على دقة التقرير',kind,
                f'وردت إفادتان مختلفتان بخصوص «{LABELS[kind]}». هل تتعلقان بوقتين مختلفين أم تحتاج إحداهما تصحيحًا؟')
    if threats or harm or state.status('immediate_danger')=='uncertain':
        add('current_safety',110,'التحقق من السلامة مبرر بواقعة ذات صلة')
        previous=[a for a in session.answers if a.key=='current_safety']
        new_serious=any(f['source'].startswith('answer_') and f['kind'] in {'death_threat','physical_assault','threat_capability'}
                       and f['polarity']=='reported' for f in state.observations)
        if previous and new_serious and previous[-1].safety_revision and previous[-1].safety_revision!=state.safety_revision:
            add('current_safety',150,'ظهرت واقعة سلامة جديدة بعد السؤال السابق',force=True)
    benign=codes=={'ordinary_dispute'}
    if benign:
        add('desired_outcome',75,'تحديد المسار المناسب لهدف المشتكي')
        add('ongoing_issue',65,'معرفة الحاجة لحل مستمر')
    elif codes:
        if codes & {'publication_threat','extortion'}:
            add('publication_occurred',100,'يفصل التهديد عن التنفيذ')
            add('media_possession',88,'استيضاح قدرة التنفيذ دون افتراضها')
            add('deadline',72,'قد يغير الاستعجال الرقمي')
        if codes & {'financial_fraud','unauthorized_debit','extortion'}:
            add('financial_loss',64,'يفصل الخسارة الفعلية عن محاولة الطلب')
        if 'account_takeover' in codes:
            add('account_access',95,'معرفة استمرار السيطرة على الحساب')
        if harm:
            add('injury',92,'تحديد الحاجة لدعم إضافي')
        if threats:
            add('threat_capability',70,'قد يغير أولوية المراجعة')
        add('evidence',82,'يحسن توثيق البلاغ دون اعتماد الدليل')
        add('recurrence',76,'يفصل واقعة واحدة عن سلوك متكرر')
        add('incident_time',68,'يحسن التسلسل الزمني')
        add('desired_outcome',62,'يحترم طلب المستخدم')
        add('current_status',60,'معرفة استمرار الواقعة')
        add('relationship',45,'سياق ضروري للمراجعة')
    else:
        add('other_side',90,'الوصف لا يدعم تصنيفًا محددًا')
        add('desired_outcome',65,'تحديد نوع المساعدة المطلوبة')
    candidates.sort(key=lambda c:(-c.utility,c.key))
    missing=[c.target for c in candidates]
    if len(session.answers)>=min(6,total):
        return Decision(None,candidates,'question_limit',missing)
    if not candidates or candidates[0].utility<12:
        return Decision(None,candidates,'sufficient_information',missing)
    return Decision(candidates[0],candidates,None,missing)


def next_question(session,total: int=6):
    if session.pending_question:
        return session.pending_question['key'],session.pending_question['text']
    decision=plan(session,total)
    return (decision.selected.key,decision.selected.text) if decision.selected else None

def questions(session):
    return [(c.key,c.text) for c in plan(session).candidates]
