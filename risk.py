"""Transparent, uncalibrated severity; separate physical/digital/review decisions."""
from __future__ import annotations
import re
from domain import LABELS
from fact_state import FactState
from timeline import chronological_state

WEIGHTS={'death_threat':75,'physical_assault':55,'extortion':65,'account_takeover':45,
 'unauthorized_debit':40,'financial_fraud':38,'publication_threat':30,'publication_occurred':48,
 'verbal_threat':23,'harassment':13,'defamation':13,'privacy_violation':18,'ordinary_dispute':8}
ROUTE_LABELS={'assisted_resolution':'إرشادات لحل الخلاف','information_only':'معلومات وإرشادات عامة',
 'additional_information_required':'معلومات إضافية أو مراجعة بشرية','standard_human_review':'مراجعة بشرية',
 'priority_human_review':'مراجعة بشرية ذات أولوية','immediate_safety_guidance':'إرشادات سلامة فورية',
 'official_draft_available':'مسودة اختيارية متاحة للمراجعة'}

def assess(session,model_worked: bool=True) -> dict:
    state=FactState.build(session)
    timeline=chronological_state(session)
    codes=state.classifications
    primary=max((k for k in codes if k in WEIGHTS),key=lambda k:WEIGHTS[k],default=None)
    score=0
    breakdown=[]
    def add(kind,points,record,description):
        nonlocal score
        points=min(points,100-score)
        score+=points
        breakdown.append({'factor':kind,'contribution':points,'source_quote':record.get('quote',''),
            'source':record.get('source',''),'fact_id':record.get('fact_id'),
            'review_status':'unverified','description':description})
    if primary:
        add(primary,WEIGHTS[primary],state.current[primary],'أساس نوع الواقعة: '+LABELS[primary])
    immediate=state.status('immediate_danger')=='reported'
    concern='reported_current' if immediate else 'conflicting' if state.status('immediate_danger')=='conflicting' else 'denied_by_user' if state.status('immediate_danger')=='denied' else 'unknown'
    if immediate:
        add('immediate_safety',max(0,90-score),state.current['immediate_danger'],'إفادة عن خطر مباشر حالي')
    # Related harm indicators share one bucket; no injury + generic harm stacking.
    harms=[]
    for kind,weight in [('injury',18),('financial_loss',15),('publication_occurred',18)]:
        if kind!=primary and state.status(kind)=='reported':
            harms.append((kind,weight,state.current[kind]))
    if harms:
        kind,points,record=max(harms,key=lambda x:x[1])
        add(kind,points,record,'أثر إضافي مذكور؛ أعلى عامل ضرر فقط')
    threats=bool(codes & {'extortion','publication_threat','death_threat','verbal_threat'})
    if threats:
        if timeline['reported_copy']:
            add('reported_copy_received',8,timeline['reported_copy'],'إرسال نسخة كما ورد في الإفادة؛ لم تُفحص')
        elif state.status('threat_capability')=='reported':
            add('threat_capability',8,state.current['threat_capability'],'مؤشر قدرة تنفيذ مذكور')
        if timeline['renewed_threat']:
            add('renewed_threat',10,timeline['renewed_threat'],'تهديد لاحق مستقل بحسب الإفادة')
        elif state.status('recurrence')=='reported':
            add('recurrence',10,state.current['recurrence'],'تكرار مذكور؛ لا يضاف مع التهديد المتجدد')
        deadline=state.current.get('threat_deadline')
        if timeline['near_term_threat']:
            add('new_near_term_deadline',10,timeline['near_term_threat'],'أحدث مهلة قريبة؛ تحل محل وزن المهلة القديمة')
        elif deadline and deadline['polarity']=='reported':
            m=re.search(r'(\d+)\s*ساعة',deadline['quote'])
            if m and int(m[1])<=48:
                add('short_deadline',8,deadline,'مهلة تهديد خلال 48 ساعة بحسب الإفادة')
    serious_conflict=any(c['kind'] in {'death_threat','physical_assault','immediate_danger','extortion'} for c in state.contradictions)
    priority='immediate' if immediate else 'priority' if ('death_threat' in codes or score>=60 or serious_conflict) else 'standard'
    digital='urgent' if timeline['near_term_threat'] or codes & {'publication_occurred','account_takeover'} else 'attention' if codes & {'extortion','publication_threat','unauthorized_debit'} else 'not_indicated'
    if immediate:
        route='immediate_safety_guidance'
    elif priority=='priority':
        route='priority_human_review'
    elif state.contradictions or not codes:
        route='additional_information_required'
    elif codes=={'ordinary_dispute'}:
        route='assisted_resolution' if state.status('resolved')=='reported' else 'information_only'
    else:
        route='standard_human_review'
    index=score if primary and model_worked and not state.contradictions else None
    if immediate:
        index=max(90,score)
    if not model_worked and not immediate and priority!='priority':
        route='additional_information_required'
    level='عاجل' if immediate else 'مرتفع جدًا' if score>=80 else 'مرتفع' if score>=60 else 'متوسط' if score>=30 else 'منخفض مبدئيًا'
    if index is None and not immediate:
        level='غير محسوم — مراجعة عاجلة' if priority=='priority' else 'غير محسوم'
    # Backward-compatible route field; all UI uses recommended_route.
    legacy={'immediate_safety_guidance':'immediate_safety_screening','assisted_resolution':'assisted_resolution_pending_review',
        'information_only':'review_before_resolution','standard_human_review':'specialist_screening',
        'additional_information_required':'insufficient_model_assessment' if not model_worked else 'insufficient_evidence'}.get(route,route)
    if not immediate and priority=='priority' and score<80 and 'death_threat' not in codes and model_worked:
        legacy='specialist_review'
    factors=[f"{f['description']} (+{f['contribution']})" for f in breakdown] or ['المعلومات لا تكفي لحساب مؤشر ذي معنى']
    if 'death_threat' in codes or serious_conflict:
        factors.append('التهديد الجسيم أو تعارض إفاداته يبقي أولوية المراجعة حتى دون وقوع أذى')
    return {'risk_index':index,'severity_index':index,'risk_level':level,'route':legacy,
        'recommended_route':route,'route_label':ROUTE_LABELS[route], 'urgent':immediate,
        'immediate_safety_concern':concern,'digital_urgency':digital,'review_priority':priority,
        'factors':factors,'factor_breakdown':breakdown,'requires_human_review':True,
        'model_extraction_succeeded':model_worked,'case_state_changes':timeline['events'],
        'uncertainty':'غير معاير؛ يعتمد على إفادات غير متحقق منها',
        'disclaimer':'مؤشر شدة تجريبي 0–100 وليس احتمال وقوع الضرر أو حكمًا قانونيًا. الأوزان غير معايرة؛ جميع المعلومات تحتاج مراجعة بشرية.'}
