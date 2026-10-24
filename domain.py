"""Domain contracts; reported is never equivalent to externally verified."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal
import uuid
from pydantic import BaseModel, ConfigDict, Field, field_validator

LABELS = {
    'ordinary_dispute': 'خلاف عادي', 'harassment': 'مضايقة',
    'verbal_threat': 'تهديد لفظي', 'death_threat': 'تهديد خطير بالقتل',
    'extortion': 'ابتزاز مزعوم', 'publication_threat': 'تهديد بالنشر',
    'publication_occurred': 'نشر فعلي مُبلّغ عنه', 'physical_assault': 'اعتداء جسدي مُبلّغ عنه',
    'financial_fraud': 'احتيال مالي مُبلّغ عنه', 'unauthorized_debit': 'عملية مالية غير مصرح بها',
    'account_takeover': 'استيلاء على حساب مُبلّغ عنه', 'defamation': 'تشهير مُبلّغ عنه',
    'privacy_violation': 'انتهاك خصوصية مُبلّغ عنه', 'other': 'واقعة غير مصنّفة',
    'immediate_danger': 'خطر مباشر مُبلّغ عنه', 'injury': 'أذى مُبلّغ عنه',
    'recurrence': 'تكرار مُبلّغ عنه', 'evidence': 'دليل مذكور دون فحص',
    'relationship': 'علاقة بين الأطراف', 'time': 'وقت الحادث', 'location': 'مكان الواقعة',
    'payment_made': 'حالة الدفع', 'reported_fear': 'خوف مُبلّغ عنه',
    'media_possession': 'امتلاك الطرف الآخر للمواد', 'amount_demanded': 'مبلغ مطلوب',
    'threat_deadline': 'مهلة التهديد',
}

LABELS.update({'financial_loss': 'خسارة مالية', 'resolved': 'انتهاء الواقعة',
    'desired_outcome': 'هدف المشتكي', 'declines_draft': 'عدم الرغبة ببلاغ',
    'threat_capability': 'إمكانية الوصول أو التنفيذ', 'account_access': 'استمرار الوصول للحساب'})
COMPLAINT_CATEGORIES = frozenset(list(LABELS)[:14])
Polarity = Literal['reported', 'denied', 'uncertain', 'unknown', 'conflicting']

class FactProposal(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: str
    polarity: Literal['reported', 'denied', 'uncertain']
    quote: str = Field(min_length=1, max_length=1200)
    source: str | None = None
    temporal_context: Literal['current', 'past', 'future', 'unspecified'] = 'unspecified'

    @field_validator('kind')
    @classmethod
    def known_kind(cls, value: str) -> str:
        if value not in LABELS:
            raise ValueError('Unknown fact kind')
        return value

class Extraction(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    facts: list[FactProposal] = Field(default_factory=list, max_length=60)

class GroundedFact(FactProposal):
    source: str
    verification: Literal['unverified'] = 'unverified'
    extraction_method: str = 'llm_proposal'
    requires_review: bool = False
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    fact_id: str
    event_id: str

@dataclass
class QA:
    key: str
    question: str
    answer: str
    question_id: str = ''
    safety_revision: str = ''

@dataclass
class Session:
    id: str = field(default_factory=lambda: 'TEST-' + uuid.uuid4().hex[:12])
    created_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    narrative: str = ''
    answers: list[QA] = field(default_factory=list)
    initial_facts: list[dict] = field(default_factory=list)
    final_facts: list[dict] = field(default_factory=list)
    extraction_errors: list[str] = field(default_factory=list)
    report: dict | None = None
    official_request: str = 'not_asked'
    pending_question: dict | None = None
    planner_trace: list[dict] = field(default_factory=list)
    model_proposals: list[dict] = field(default_factory=list)
    rejected_proposals: list[dict] = field(default_factory=list)
    llm_calls: int = 0
    model_extraction_succeeded: bool = True
    analysis_mode: str = 'not_run'
    schema_version: int = 2

    @classmethod
    def from_dict(cls, data: dict) -> 'Session':
        from dataclasses import fields
        allowed = {f.name for f in fields(cls)}
        values = {k: v for k, v in data.items() if k in allowed}
        values['answers'] = [QA(**a) for a in values.get('answers', [])]
        return cls(**values)
