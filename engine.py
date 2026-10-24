"""Stable public facade; implementation is split by responsibility."""
import re
from domain import Session, QA, LABELS, COMPLAINT_CATEGORIES
from arabic_semantics import is_direct_danger, yes_no
from interview_planner import covered, questions, next_question
from fact_state import FactState
from risk import assess
from reporting import structured_summary, assemble_report

def has_explicit(text: str, pattern: str) -> bool:
    return bool(re.search(pattern,text,re.I))

def complaint_codes(session: Session) -> set[str]:
    return FactState.build(session).classifications

def q(key: str,text: str):
    return key,text
