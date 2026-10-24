"""Observations -> current semantic state, retaining history and contradictions."""
from __future__ import annotations
from dataclasses import dataclass
import re
from arabic_semantics import normalize
from domain import COMPLAINT_CATEGORIES
from grounding import collected_facts, source_texts

@dataclass
class FactState:
    observations: list[dict]
    current: dict[str,dict]
    contradictions: list[dict]
    classifications: set[str]

    def status(self, kind: str) -> str:
        return self.current.get(kind,{}).get('polarity','unknown')

    def known(self, kind: str) -> bool:
        return self.status(kind) != 'unknown'

    @classmethod
    def build(cls, session) -> 'FactState':
        observations=collected_facts(session)
        sources=source_texts(session)
        current, contradictions={},[]
        order={source:i for i,source in enumerate(sources)}
        for fact in sorted(observations,key=lambda f:(order[f['source']],f['start'],f['end'])):
            kind=fact['kind']
            old=current.get(kind)
            value=dict(fact)
            if old and fact.get('temporal_context')=='past' and old.get('temporal_context')!='past':
                continue  # A denial about an earlier period does not retract today's incident.
            if old:
                value['evidence_refs']=list(dict.fromkeys(old.get('evidence_refs',[old['fact_id']])+[fact['fact_id']]))
                if old['polarity']!=fact['polarity']:
                    text=normalize(sources[fact['source']])
                    temporal = old.get('temporal_context')=='past' and fact.get('temporal_context')=='current'
                    explicit_change=bool(re.search(r'(?:هسا|حاليا|الان|تطور\s+جديد|تصحيح|كنت\s+غلطان|اقصد|وصلتني\s+بعد)',text))
                    refinement=old['polarity']=='uncertain' and fact['polarity'] in {'reported','denied'} and fact['source']!=old['source']
                    if temporal or explicit_change and fact['source']!=old['source'] or refinement:
                        value['transition_from']=old['polarity']
                    elif old['polarity'] in {'reported','denied','conflicting'} and fact['polarity'] in {'reported','denied'}:
                        value['polarity']='conflicting'
                        value['requires_review']=True
                        contradictions.append({'kind':kind,'sources':list(dict.fromkeys([old['source'],fact['source']])),
                            'evidence_refs':value['evidence_refs']})
                    elif fact['polarity']=='uncertain':
                        value['polarity']=old['polarity']
            else:
                value['evidence_refs']=[fact['fact_id']]
            current[kind]=value
        codes={k for k,v in current.items() if k in COMPLAINT_CATEGORIES and v['polarity']=='reported'}
        if codes & {'death_threat','publication_threat','extortion'}:
            codes.discard('verbal_threat')
        if codes-{'ordinary_dispute','other'}:
            codes.discard('ordinary_dispute')
        return cls(observations,current,contradictions,codes)

    @property
    def safety_revision(self) -> str:
        supported=[f['fact_id'] for f in self.observations if f['polarity']=='reported'
                   and f['kind'] in {'death_threat','physical_assault','immediate_danger','threat_capability'}]
        from hashlib import sha256
        return sha256('|'.join(sorted(supported)).encode()).hexdigest()[:12]
