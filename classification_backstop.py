"""Compatibility entry points backed by the shared semantic grounding layer."""
from domain import COMPLAINT_CATEGORIES
from arabic_semantics import clauses, extract_rules
from grounding import normalize_facts, recover_explicit

def segments(text):
    return (c.text for c in clauses(text))

def supported(kind: str, source: str) -> bool:
    return any(f['kind']==kind and f['polarity']=='reported' for f in extract_rules({'text':source}))

def explicit_category_candidates(sources: dict[str,str]) -> list[dict]:
    return [f for f in recover_explicit(sources) if f['kind'] in COMPLAINT_CATEGORIES and f['polarity']=='reported']

def merge_categories(facts, sources):
    valid=normalize_facts(facts,sources)+explicit_category_candidates(sources)
    specific={f['source'] for f in valid if f['kind']=='death_threat' and f['polarity']=='reported'}
    valid=[f for f in valid if not (f['kind']=='verbal_threat' and f['polarity']=='reported' and f['source'] in specific and len(clauses(sources[f['source']]))==1)]
    return list({(f['kind'],f['polarity'],f['source'],f['quote']):f for f in valid}.values())
