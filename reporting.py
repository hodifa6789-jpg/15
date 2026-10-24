"""Deterministic Arabic reports: no generative summarization can add allegations."""
from dataclasses import asdict
from domain import LABELS, COMPLAINT_CATEGORIES
from fact_state import FactState
from risk import assess
from timeline import chronological_state
from interview_planner import plan
from arabic_semantics import clauses
from grounding import source_texts

def summary_context(session, fact):
    """Keep the supporting clause's details, with no generated people/places."""
    text=source_texts(session).get(fact['source'],'')
    start=fact.get('start',text.find(fact['quote']))
    end=start+len(fact['quote'])
    supporting=[c for c in clauses(text) if c.start<end and c.end>start]
    return text[supporting[0].start:supporting[-1].end] if supporting else fact['quote']

def structured_summary(session,facts=None):
    state=FactState.build(session)
    timeline=chronological_state(session)
    codes=state.classifications
    pieces=[]
    if codes=={'ordinary_dispute'}:
        original=[f for f in state.observations if f['kind']=='ordinary_dispute' and f['polarity']=='reported' and f['source']=='initial_statement']
        incident=min(original,key=lambda f:f['start']) if original else state.current['ordinary_dispute']
        pieces.append('أفاد المشتكي بخلاف كلامي: «'+summary_context(session,incident)+'»')
    elif codes:
        for kind in sorted(codes):
            pieces.append('أفاد بـ'+LABELS[kind]+': «'+summary_context(session,state.current[kind])+'»')
    else:
        pieces.append('لم يُحسم نوع الواقعة. الوصف الأصلي: «'+session.narrative[:280]+'»')
    for kind in ('payment_made','publication_occurred','reported_fear','physical_assault','financial_loss'):
        if state.status(kind)=='denied':
            text={'payment_made':'نفى دفع المبلغ حتى وقت الإفادة','publication_occurred':'نفى وقوع النشر حتى وقت الإفادة',
                  'reported_fear':'نفى الشعور بالخوف حاليًا','physical_assault':'نفى الاعتداء الجسدي','financial_loss':'نفى الخسارة المالية'}[kind]
            pieces.append(text)
    if state.status('resolved')=='reported':
        pieces.append('ذكر أن التصرف توقف أو أن الخلاف انتهى')
    if state.status('amount_demanded')=='reported':
        pieces.append('المبلغ المطلوب بحسب الإفادة: «'+state.current['amount_demanded']['quote']+'»')
    if timeline['reported_copy']:
        pieces.append('ذكر أن الطرف الآخر أرسل نسخة من صورة؛ لم تُفحص: «'+timeline['reported_copy']['quote']+'»')
    elif state.status('media_possession')=='uncertain':
        pieces.append('غير متأكد من امتلاك الطرف الآخر للمواد')
    for name in ('original_deadline','near_term_threat'):
        if timeline[name]:
            pieces.append(('المهلة الأولى: ' if name=='original_deadline' else 'أحدث موعد مذكور: ')+timeline[name]['quote'])
    if state.status('declines_draft')=='reported':
        pieces.append('ذكر أنه لا يرغب في تقديم بلاغ')
    if state.status('desired_outcome')=='reported':
        pieces.append('هدفه المعلن: «'+state.current['desired_outcome']['quote']+'»')
    if state.contradictions:
        pieces.append('توجد إفادات متعارضة؛ لم يُحسم التعارض تلقائيًا')
    return '. '.join(pieces)+'. جميع المعلومات من الإفادة ولم تُتحقق استقلالًا.'

def guidance_for(state,risk):
    if risk['urgent']:
        return ['ابتعد عن مصدر الخطر إن كان ذلك ممكنًا بأمان، واطلب مساعدة الطوارئ المحلية أو شخصًا موثوقًا فورًا. لا تنتظر إكمال المقابلة.']
    if state.classifications=={'ordinary_dispute'}:
        return ['اتفق بهدوء مع الطرف الآخر على طريقة واضحة لتجنب تكرار الخلاف.',
            'إذا كان الخلاف حول موقف أو مرفق مشترك، راجع قواعد المبنى مع الإدارة.',
            'يمكن الاستعانة بوسيط مناسب عند الحاجة، وتجنب التصعيد الكلامي.']
    tips=[]
    if state.classifications & {'extortion','publication_threat','publication_occurred','harassment','death_threat','verbal_threat'}:
        tips+=['احتفظ بالمراسلات وتوقيتها دون إعادة نشر المواد الخاصة، وراجع شخصًا مختصًا أو جهة مناسبة.',
               'إذا ظهر خطر مباشر، اطلب المساعدة البشرية فورًا.']
    if state.classifications & {'account_takeover','unauthorized_debit','financial_fraud'}:
        tips+=['استخدم قناة الدعم الرسمية للخدمة أو البنك، وراجع أمان حسابك من جهاز موثوق. لا تشارك كلمة المرور أو رمز التحقق.']
    return tips

def assemble_report(session,model_worked=True):
    state=FactState.build(session)
    timeline=chronological_state(session)
    risk=assess(session,model_worked)
    details={kind:{k:v[k] for k in ('polarity','quote','source','verification')} for kind,v in state.current.items()}
    for field,key in [('media_possession','media_possession'),('threat_deadline','latest_deadline')]:
        if timeline[key]:
            details[field]={k:timeline[key][k] for k in ('polarity','quote','source','verification')}
    facts=[dict(f,verified=False,review='pending',superseded_in_current_state=(
        f['kind']=='media_possession' and f['polarity']=='uncertain' and bool(timeline['reported_copy']))) for f in state.observations]
    proposed=sorted({p['kind'] for p in session.model_proposals if p.get('kind') in COMPLAINT_CATEGORIES and p.get('polarity')=='reported'})
    report={'schema_version':2,'case_id':session.id,'created_utc':session.created_utc,
        'original_statement':session.narrative,'summary':structured_summary(session),
        'classification':[LABELS[k] for k in sorted(state.classifications)] or ['غير محسوم — يلزم فحص بشري'],
        'classification_audit':{'model_proposed':proposed,'fact_supported':sorted(state.classifications),
            'final_experimental':sorted(state.classifications),'human_review_required':True},
        'source_anchored_facts':facts,'current_fact_state':state.current,'contradictions':state.contradictions,
        'reported_facts':[v for v in state.current.values() if v['polarity']=='reported'],
        'denied_facts':[v for v in state.current.values() if v['polarity']=='denied'],
        'uncertain_facts':[v for v in state.current.values() if v['polarity'] in {'uncertain','conflicting'}],
        'structured_details':details,'chronological_case_state':timeline,
        'mentioned_evidence':[f for f in facts if f['kind']=='evidence' and f['polarity']=='reported'],
        'missing_information':list(dict.fromkeys(plan(session).missing)),
        'user_goal':state.current.get('desired_outcome',{}).get('quote'),
        'assisted_resolution_guidance':guidance_for(state,risk),'risk':risk,
        'interview':[asdict(a) for a in session.answers], 'planner_trace':session.planner_trace,
        'analysis':{'mode':session.analysis_mode,'llm_calls':session.llm_calls,'errors':session.extraction_errors,
            'rejected_proposal_count':len(session.rejected_proposals)},
        'limitations':['جميع المعلومات إفادات غير متحقق منها، ولم تُفحص الأدلة الأصلية.',
            'قد تفوت المعالجة اللغوية تعبيرات أو تسندها خطأ؛ وجود اقتباس لا يضمن صحة التفسير.',
            'غياب إشارة خطر لا يثبت غيابه؛ مؤشر الشدة غير معاير ولا يمثل احتمالًا.',
            'يلزم تدقيق بشري قبل أي إجراء رسمي. النظام تجريبي للتوثيق والفرز الأولي فقط.'],
        'official_report_status':session.official_request}
    session.report=report
    return report
