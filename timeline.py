"""Chronological, source-anchored state for narrow, explicit Arabic case updates.

Not a truth detector. 'reported' means claimed by the speaker, not verified.
Heuristic coverage is intentionally limited; ambiguity requires human review.
"""
from __future__ import annotations
import re

COPY_SENT = re.compile(
    r'(?:بعتلي|بعثلي|بعت\s+لي|أرسل(?:\s+لي)?|ارسل(?:\s+لي)?)'
    r'\s+(?:نسخة\s+من\s+)?(?:إحدى\s+)?(?:صوري|صورتي|صور\s+شخصية|الصورة|الصور)',
    re.IGNORECASE,
)
POSSESSION_UNCERTAIN = re.compile(
    r'(?:مش|مو|غير|لست)\s+متأكد\s+(?:إذا|اذا|إن)\s+(?:هو\s+)?(?:فعلًا\s+)?(?:معه|عنده|يمتلك)\s+(?:الصور|صوري)',
    re.IGNORECASE,
)
NEAR_DEADLINE = re.compile(
    r'(?:رح|راح|سوف)\s+(?:ينشر|ينزل)(?:ها|هم)?\s+(?:الليلة|اليوم|هسا|هلأ)',
    re.IGNORECASE,
)
HOURS = re.compile(r'(?:خلال|مهلة(?:\s+الـ)?|بعد\s+انتهاء\s+مهلة\s+الـ?)\s*\d{1,3}\s*ساعة', re.IGNORECASE)
REPEAT = re.compile(r'(?:رجع\s+(?:بعتلي|بعثلي|أرسل|ارسل|هددني)|(?:مرة\s+ثانية|مرة\s+أخرى|مجدداً)\s+(?:هددني|بعتلي|أرسل))', re.IGNORECASE)
THREAT = re.compile(r'(?:هدد|يهدد|رح\s+ينشر|راح\s+ينشر|إذا\s+ما|اذا\s+ما)', re.IGNORECASE)


def _record(source: str, quote: str, status: str = 'reported') -> dict:
    return {'source': source, 'quote': quote, 'polarity': status, 'verification': 'unverified'}


def _legacy_observations(session) -> dict:
    sources = [('initial_statement', session.narrative)] + [
        (f'answer_{i}', item.answer) for i, item in enumerate(session.answers, 1)
    ]
    media = None
    early_deadline = None
    latest_deadline = None
    new_threat = None
    copy_evidence = None
    events = []
    for source, text in sources:
        for match in POSSESSION_UNCERTAIN.finditer(text):
            media = _record(source, match.group(), 'uncertain')
            media['status'] = 'uncertain'
            events.append({'kind': 'media_possession_uncertain', **_record(source, match.group(), 'uncertain')})
        for match in COPY_SENT.finditer(text):
            # E.g. "ما بعتلي صورة" must not become evidence of possession.
            prefix = text[max(0, match.start() - 10): match.start()]
            if re.search(r'(?<!\w)(?:ما|لم|ولا|مش|مو)\s*$', prefix):
                continue
            media = _record(source, match.group())
            media['status'] = 'copy_reported'
            copy_evidence = dict(media)
            events.append({'kind': 'copy_reported', **_record(source, match.group())})
        for match in HOURS.finditer(text):
            found = _record(source, match.group())
            if early_deadline is None:
                early_deadline = found
            latest_deadline = found
        for match in NEAR_DEADLINE.finditer(text):
            prefix = text[max(0, match.start()-30):match.start()]
            if re.search(r'(?:ما\s+قال(?:\s+إنه)?|لم\s+يقل|ما\s+بعتقد\s+إنه|نفى\s+أنه)\s*$', prefix):
                continue
            latest_deadline = _record(source, match.group())
            latest_deadline['status'] = 'near_term_publication_threat'
            events.append({'kind': 'near_term_threat', **_record(source, match.group())})
        if source != 'initial_statement' and REPEAT.search(text) and THREAT.search(text):
            match = REPEAT.search(text)
            assert match is not None
            tail = text[match.start():]
            boundary = re.search(r'[.؟!\n]', tail)
            quote = tail[:boundary.start()] if boundary else tail[:180]
            new_threat = _record(source, quote.strip())
            events.append({'kind': 'renewed_threat', **new_threat})
    return {
        'media_possession': media,
        'reported_copy': copy_evidence,
        'original_deadline': early_deadline,
        'latest_deadline': latest_deadline,
        'renewed_threat': new_threat,
        'near_term_threat': latest_deadline if latest_deadline and latest_deadline.get('status') == 'near_term_publication_threat' else None,
        'events': events,
        'disclaimer': 'ملخص زمني ناتج عن قواعد سياقية محدودة؛ كل الادعاءات غير متحقق منها وتحتاج مراجعة بشرية.',
    }


def chronological_state(session) -> dict:
    """Retain original chronology API; collapse confirmations into event identities."""
    from arabic_semantics import extract_rules, normalize, is_instruction
    from grounding import source_texts
    sources=source_texts(session)
    state=_legacy_observations(session)
    rules=extract_rules(sources)
    allowed={}
    for f in rules:
        if f['polarity']=='reported':
            allowed.setdefault(f['source'],set()).add(f['kind'])
    events={}
    latest_identity={}
    for event in state['events']:
        kind=event['kind']
        source=event['source']
        text=normalize(sources[source])
        if is_instruction(sources[source]):
            continue
        if kind=='copy_reported' and 'media_possession' not in allowed.get(source,set()):
            continue
        if kind=='near_term_threat' and 'threat_deadline' not in allowed.get(source,set()):
            continue
        if kind=='renewed_threat' and re.search(r'(?:ما|لم)\s+رجع|نفس\s+الرسالة|اقصد\s+الرسالة',text):
            continue
        # Latest confirmation updates provenance, never increments an event count.
        explicitly_new=bool(re.search(r'(?:تطور\s+جديد|رجع\s+(?:بعتلي|هددني)|مرة\s+ثانية|تهديد\s+جديد)',text))
        same=bool(re.search(r'(?:نفس\s+(?:الرسالة|الواقعة|التهديد)|اقصد\s+الرسالة)',text))
        key=kind+':'+source if explicitly_new and not same else latest_identity.get(kind,kind+':original')
        latest_identity[kind]=key
        if key not in events:
            events[key]=dict(event,event_id='event-'+key,observations=[])
        events[key]['observations'].append({'source':source,'quote':event['quote']})
    state['events']=list(events.values())
    for name,kind in [('reported_copy','copy_reported'),('renewed_threat','renewed_threat'),('near_term_threat','near_term_threat')]:
        if kind not in latest_identity:
            state[name]=None
    if not state['reported_copy'] and state['media_possession'] and state['media_possession'].get('status')=='copy_reported':
        state['media_possession']=None
    state['disclaimer']='الزمن بحسب تسلسل الإفادات؛ العبارات النسبية محفوظة دون اختراع تاريخ. إعادة التأكيد ليست حدثًا مستقلًا.'
    return state


def timeline_facts(session) -> list[dict]:
    state = chronological_state(session)
    out = []
    media = state['media_possession']
    if media and media.get('status') == 'copy_reported':
        out.append({'kind':'media_possession','polarity':'reported','quote':media['quote'],
                    'source':media['source'],'verification':'unverified'})
    if state['reported_copy']:
        copy = state['reported_copy']
        out.append({'kind':'evidence','polarity':'reported','quote':copy['quote'],
                    'source':copy['source'],'verification':'unverified'})
    if state['near_term_threat']:
        deadline = state['near_term_threat']
        out.append({'kind':'threat_deadline','polarity':'reported','quote':deadline['quote'],
                    'source':deadline['source'],'verification':'unverified'})
    return out
