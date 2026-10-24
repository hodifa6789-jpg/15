"""Per-user ordering, persistent idempotency and concurrency across independent users."""
from __future__ import annotations
import asyncio
import json
from dataclasses import asdict
from bot_flow import InterviewFlow, FlowReply
from nlp import extract
from storage import render_markdown,render_html,render_official_draft

HELP='/new — قضية جديدة\n/status — حالة المقابلة والسؤال الحالي\n/report — التقارير\n/cancel — إلغاء وحذف الجلسة\n/reset — حذف بياناتك التجريبية\n/id — معرفك\n/help — المساعدة'
STAGES={'idle':'لا توجد مقابلة','narrative':'بانتظار وصف الحادثة','answering':'بانتظار إجابة','draft_consent':'بانتظار موافقة المسودة','complete':'مكتملة'}

def envelope(reply,flow):
    out=asdict(reply)
    out['documents']=[]
    if flow.session and flow.session.report and reply.report_ready:
        r=flow.session.report
        out['documents']=[{'name':flow.session.id+suffix,'content':value} for suffix,value in (
            ('.md',render_markdown(r)),('.html',render_html(r)),('.json',json.dumps(r,ensure_ascii=False,indent=2)))]
    if flow.session and flow.session.report and reply.draft_ready:
        out['documents'].append({'name':flow.session.id+'.official_draft.md','content':render_official_draft(flow.session.report)})
    out['stage']=flow.stage
    out['case_id']=flow.session.id if flow.session else None
    out['question_id']=flow.session.pending_question['id'] if flow.session and flow.session.pending_question else None
    return out

class InterviewService:
    def __init__(self,store,model,extractor=extract,max_parallel=3):
        self.store,self.model,self.extractor=store,model,extractor
        self.locks={}
        self.workers=asyncio.Semaphore(max_parallel)

    def snapshot(self,user_id):
        payload,revision,last=self.store.load(user_id)
        return InterviewFlow.from_dict(payload) if payload else InterviewFlow(),revision,last

    async def process(self,user_id,update_id,text,command=None,message_id=0,expected_case=None,deliver=None,arrival_revision=None):
        # Snapshot before yielding: two rapid answers cannot silently bind to two questions.
        _,observed_revision,_=self.store.load(user_id)
        if arrival_revision is not None: observed_revision=arrival_revision
        lock=self.locks.setdefault(user_id,asyncio.Lock())
        async with lock:
            seen=self.store.seen(user_id,update_id)
            if seen:
                if seen['delivered']: return {'messages':[],'documents':[],'duplicate':True}
                out=seen['result']
                if deliver:
                    await deliver(out)
                    self.store.delivered(user_id,update_id)
                return out
            self.store.purge()
            flow,revision,last_id=self.snapshot(user_id)
            if expected_case and (not flow.session or flow.session.id!=expected_case):
                reply=FlowReply(['هذا الزر يعود لقضية سابقة. استخدم /status.'])
            elif not command and message_id and last_id and message_id<=last_id:
                reply=FlowReply(['الرسالة قديمة؛ لم تُربط بالسؤال الحالي. استخدم /status.'])
            elif not command and observed_revision!=revision:
                reply=FlowReply(['وصلت رسالتك قبل عرض السؤال الحالي، لذلك لم نعتبرها إجابة عليه. اقرأ /status ثم أعد إرسال الإجابة.'])
            elif command in {'start','new'}:
                if command=='start' and flow.stage not in {'idle','complete'}:
                    reply=FlowReply(['لديك مقابلة محفوظة. استخدم /status للمتابعة أو /new لاستبدالها.'])
                else: reply=flow.new()
            elif command in {'cancel','reset'}:
                flow=InterviewFlow()
                reply=FlowReply(['تم إلغاء المقابلة وحذف بياناتها المحلية. الملفات التي نزلتها ورسائل Telegram تخضع لحذفها من طرفك.'])
            elif command=='help': reply=FlowReply([HELP])
            elif command=='status':
                current=flow.session.pending_question if flow.session else None
                reply=FlowReply(['الحالة: '+STAGES[flow.stage]+('\nرقم القضية: '+flow.session.id if flow.session else '')+
                    ('\nالسؤال الحالي: '+current['text'] if current else '')])
            elif command=='report':
                ready=bool(flow.session and flow.session.report)
                reply=FlowReply(['تقريرك التجريبي مرفق.' if ready else 'لم يكتمل التقرير بعد. استخدم /status.'],report_ready=ready,
                    draft_ready=bool(ready and flow.session.official_request=='draft_requested_not_submitted'))
            elif command: reply=FlowReply([HELP])
            else:
                async with self.workers:
                    reply=await asyncio.to_thread(flow.receive,text,self.model,self.extractor)
            out=envelope(reply,flow)
            self.store.commit(user_id,update_id,flow.to_dict(),out,revision,max(message_id,last_id),
                erase=command in {'reset','cancel'},clear_history=command=='new')
            if deliver:
                await deliver(out)
                self.store.delivered(user_id,update_id)
            return out
