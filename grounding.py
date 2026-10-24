"""Validate literal provenance AND local semantic support before automated use."""
from __future__ import annotations
from hashlib import sha256
import re
from pydantic import ValidationError
from domain import FactProposal, GroundedFact, LABELS
from arabic_semantics import extract_rules, normalize, PATTERNS, yes_no, is_instruction


def source_texts(session) -> dict[str, str]:
    return {'initial_statement': session.narrative,
            **{f'answer_{i}': qa.answer for i, qa in enumerate(session.answers, 1)}}


def _enrich(fact: dict, sources: dict[str, str]) -> dict:
    f = dict(fact)
    text = sources[f['source']]
    start = f.get('start', text.find(f['quote']))
    end = start + len(f['quote'])
    identity = f"{f['source']}:{start}:{end}:{f['kind']}:{f['polarity']}"
    new = re.search(r'(?:تطور\s+جديد|رجع\s+(?:بعتلي|هددني)|مرة\s+ثانية|تهديد\s+جديد)', normalize(text))
    same = re.search(r'(?:نفس\s+(?:الرسالة|الواقعة|التهديد)|اقصد\s+الرسالة\s+نفسها|ما\s+وصلتني.*جديدة)', normalize(text))
    episode = f['source'] if new and not same else 'original'
    f.update(start=start, end=end, fact_id=sha256(identity.encode()).hexdigest()[:16],
             event_id=f"{f['kind']}:{episode}", verification='unverified')
    allowed = GroundedFact.model_fields
    return GroundedFact.model_validate({k:v for k,v in f.items() if k in allowed}).model_dump()


def normalize_facts(facts: list[dict], sources: dict[str, str], rejected: list | None = None) -> list[dict]:
    rules = extract_rules(sources)
    result = {}
    for raw in facts:
        try:
            original = {k:v for k,v in raw.items() if k in FactProposal.model_fields}
            item = FactProposal.model_validate(original).model_dump()
            quote, source = item['quote'], item['source']
            if source is None:
                matches = [s for s,t in sources.items() if quote in t]
                if len(matches) != 1:
                    raise ValueError('ambiguous_source')
                source = item['source'] = matches[0]
            if source not in sources or quote not in sources[source]:
                raise ValueError('missing_literal_source')
            if is_instruction(quote):
                raise ValueError('instruction_not_event')
            kind = item['kind']
            n = normalize(quote)
            if kind=='financial_fraud' and item['polarity']=='denied' and re.search(r'ما\s+(?:دفعت|حولت)', n):
                item['kind'] = kind = 'payment_made'
            if kind=='publication_occurred' and item['polarity']=='uncertain' and re.search(r'(?:معه|عنده)\s+(?:الصور|صوري)', n):
                item['kind'] = kind = 'media_possession'
            if kind=='harassment' and re.search(PATTERNS['reported_fear'], n) and not re.search(PATTERNS['harassment'], n):
                item['kind'] = kind = 'reported_fear'
            offsets = [m.start() for m in re.finditer(re.escape(quote), sources[source])]
            compatible = []
            for offset in offsets:
                supported = [f for f in rules if f['source']==source and f['kind']==kind
                    and f['polarity']==item['polarity']
                    and max(offset, f['start']) < min(offset+len(quote), f['end'])]
                if supported and (kind=='amount_demanded' or re.search(PATTERNS.get(kind,r'(?!)'), n)):
                    compatible.extend(supported)
            if not compatible:
                raise ValueError('semantic_support_unconfirmed')
            support = compatible[-1]
            if item['polarity']!='reported':
                item['quote'] = support['quote']
                item['start'] = support['start']
            else:
                item['start'] = next(o for o in offsets if max(o,support['start']) < min(o+len(quote),support['end']))
            item['temporal_context'] = support.get('temporal_context','unspecified')
            item['extraction_method'] = raw.get('extraction_method','llm_proposal')
            item['requires_review'] = False
            validated = _enrich(item, sources)
            result[validated['fact_id']] = validated
        except (ValueError, TypeError, KeyError, ValidationError):
            if rejected is not None:
                rejected.append({'kind':raw.get('kind') if isinstance(raw,dict) else None,
                                 'reason':'invalid_or_semantically_unsupported'})
    return list(result.values())


def recover_explicit(sources: dict[str,str]) -> list[dict]:
    return [_enrich(f,sources) for f in extract_rules(sources)]


ANSWER_KINDS = {'current_safety':'immediate_danger', 'publication_occurred':'publication_occurred',
    'media_possession':'media_possession','actual_assault':'physical_assault',
    'financial_loss':'financial_loss','payment_made':'payment_made','injury':'injury',
    'evidence':'evidence','threat_capability':'threat_capability','account_access':'account_access'}


def answer_facts(session) -> list[dict]:
    result=[]
    sources=source_texts(session)
    for i,qa in enumerate(session.answers,1):
        kind=ANSWER_KINDS.get(qa.key)
        choice=yes_no(qa.answer)
        if not kind or choice=='unknown' or not re.fullmatch(r'(?:نعم|لا|كلا|اه|ايوه|اجل)[.!،\s]*',normalize(qa.answer)):
            continue
        result.append(_enrich(dict(kind=kind, polarity='reported' if choice=='yes' else 'denied',
            quote=qa.answer, source=f'answer_{i}', temporal_context='current',
            extraction_method='explicit_answer_to_question'),sources))
    return result


def collected_facts(session) -> list[dict]:
    sources=source_texts(session)
    facts=normalize_facts(list(session.initial_facts)+list(session.final_facts),sources)
    facts+=recover_explicit(sources)+answer_facts(session)
    unique={}
    for f in facts:
        unique.setdefault((f['kind'],f['polarity'],f['source'],f['start'],f['end']), f)
    return list(unique.values())


def find_fact(facts: list[dict], kind: str, polarity: str | None = None):
    return next((f for f in reversed(facts) if f['kind']==kind and (polarity is None or f['polarity']==polarity)),None)


def metadata_from_facts(facts: list[dict]) -> dict:
    return {kind:{k:f[k] for k in ('polarity','quote','source','verification')}
            for kind in LABELS if (f:=find_fact(facts,kind)) is not None}
