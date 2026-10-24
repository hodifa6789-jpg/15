"""Serializable interview state machine, independent from Telegram and persistence."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
import re
import uuid
from engine import Session,QA,is_direct_danger,assemble_report,yes_no
from fact_state import FactState
from interview_planner import plan
from nlp import extract

MAX_QUESTIONS=6
NOTICE='ACCP.AI نظام تجريبي لتوثيق الإفادات والفرز الأولي؛ ليس جهة قضائية ولا يرسل بلاغات. استخدم حالات اصطناعية وتجنب البيانات الشخصية الحساسة.'
SAFETY='تنبيه سلامة: إن كان هناك خطر مباشر الآن، ابتعد عن مصدر الخطر إن أمكن بأمان واطلب مساعدة الطوارئ المحلية أو شخصًا موثوقًا فورًا. لا تنتظر نهاية المقابلة.'

@dataclass
class FlowReply:
    messages: list[str]=field(default_factory=list)
    report_ready: bool=False
    draft_ready: bool=False

@dataclass
class InterviewFlow:
    session: Session | None=None
    stage: str='idle'
    stop_for_safety: bool=False

    def to_dict(self): return asdict(self)

    @classmethod
    def from_dict(cls,data):
        if data.get('stage') not in {'idle','narrative','answering','draft_consent','complete'}:
            raise ValueError('Invalid persisted stage')
        return cls(Session.from_dict(data['session']) if data.get('session') else None,
                   data['stage'],bool(data.get('stop_for_safety',False)))

    def new(self):
        self.session=None
        self.stage='narrative'
        self.stop_for_safety=False
        return FlowReply([NOTICE,'أهلًا بك. احكيلي شو صار من البداية للنهاية برسالة واحدة. قد ننهي المقابلة مباشرة أو نسأل حتى 6 أسئلة حسب المعلومات الناقصة.'])

    def receive(self,text,model,extractor=extract):
        text=text.strip()
        if not text: return FlowReply(['الرسالة فارغة. اكتب وصفًا أو إجابة.'])
        limit=3000 if self.stage=='narrative' else 1500
        if len(text)>limit: return FlowReply([f'الحد المسموح {limit} حرفًا. اختصر البيانات غير الضرورية.'])
        if self.stage=='idle': return FlowReply(['استخدم /new لبدء مقابلة.'])
        if self.stage=='complete': return FlowReply(['اكتملت المقابلة. استخدم /report للتقرير أو /new لقضية جديدة.'])
        if self.stage=='draft_consent': return self._consent(text)
        notices=[]
        initial=self.stage=='narrative'
        if initial:
            self.session=Session(narrative=text)
        else:
            if not self.session or not self.session.pending_question:
                return FlowReply(['تعذّر ربط الإجابة بسؤال معروض. استخدم /status أو /new.'])
            pending=self.session.pending_question
            self.session.answers.append(QA(pending['key'],pending['text'],text,pending['id'],pending.get('safety_revision','')))
            self.session.pending_question=None
        self.stage='answering'
        # Local urgent screening happens before an external call, including new danger in any answer.
        if is_direct_danger(text) or (not initial and pending['key']=='current_safety' and FactState.build(self.session).status('immediate_danger')=='reported'):
            self.stop_for_safety=True
            self.session.model_extraction_succeeded=False
            self.session.analysis_mode='local_safety_screen'
            notices.append(SAFETY)
            return self._finish(notices)
        # One extraction per substantial turn; bare answers use the saved question context.
        short=not initial and (yes_no(text)!='unknown' and len(text.split())<=4 or text in {'ما بعرف','لا أعرف','لا اعرف'})
        if not short:
            try:
                facts=extractor(self.session,model,not initial)
                if initial: self.session.initial_facts=facts
                else: self.session.final_facts=facts
            except Exception as error:
                self.session.extraction_errors.append(type(error).__name__)
                self.session.model_extraction_succeeded=False
                notices.append('تعذّر التحليل السحابي؛ سنكمل بالقواعد المحلية، والتقرير سيبيّن أن التقييم غير مكتمل.')
        return self._advance(notices)

    def _advance(self,notices):
        decision=plan(self.session)
        self.session.planner_trace.append(decision.audit())
        if decision.selected:
            question=decision.selected
            self.session.pending_question={'id':uuid.uuid4().hex[:16],'key':question.key,'text':question.text,
                'safety_revision':FactState.build(self.session).safety_revision}
            return FlowReply(notices+[f'السؤال {len(self.session.answers)+1}/{MAX_QUESTIONS} كحد أقصى: {question.text}'])
        return self._finish(notices)

    def _finish(self,notices):
        self.session.pending_question=None
        report=assemble_report(self.session,model_worked=self.session.model_extraction_succeeded)
        risk=report['risk']
        score=f"{risk['risk_index']}/100" if risk['risk_index'] is not None else 'غير قابل للتقدير'
        notices.append('النتيجة الأولية — ACCP.AI\nرقم القضية: '+self.session.id+
            '\nالتصنيف: '+'، '.join(report['classification'])+'\nمؤشر الشدة التجريبي: '+score+
            '\nالمسار: '+risk['route_label']+'\n'+report['summary']+'\n'+risk['disclaimer']+
            ('\nإرشادات عامة:\n'+'\n'.join('• '+x for x in report['assisted_resolution_guidance']) if report['assisted_resolution_guidance'] else '')+
            '\nاستخدم /report للحصول على التقرير.')
        self.stage='complete'
        state=FactState.build(self.session)
        if risk['review_priority']=='priority' and state.status('declines_draft')!='reported' and not self.stop_for_safety:
            self.stage='draft_consent'
            notices.append('هل ترغب في إنشاء مسودة بلاغ للمراجعة فقط؟ أجب نعم أو لا. لن تُرسل إلى أي جهة.')
        return FlowReply(notices,report_ready=True)

    def _consent(self,text):
        choice=text.strip().lower()
        if choice in {'نعم','اه','آه','أيوه','ايوه','yes','y'}:
            self.session.official_request='draft_requested_not_submitted'
            self.session.report['official_report_status']=self.session.official_request
            self.stage='complete'
            return FlowReply(['تم إنشاء مسودة للمراجعة. لم يُرسل أي بلاغ إلى أي جهة.'],draft_ready=True)
        if choice in {'لا','لأ','كلا','no','n'}:
            self.session.official_request='declined_or_deferred'
            self.session.report['official_report_status']=self.session.official_request
            self.stage='complete'
            return FlowReply(['لن ننشئ مسودة. يمكنك تنزيل تقريرك عبر /report.'])
        return FlowReply(['أجب «نعم» لإنشاء المسودة أو «لا» للتجاوز.'])
