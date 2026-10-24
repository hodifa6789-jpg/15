"""Generate clearly labelled synthetic reports; NO external inference or Telegram."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bot_flow import InterviewFlow
from grounding import collected_facts
from storage import render_html,render_markdown,render_official_draft

BENIGN='صار خلاف بيني وبين جاري حول موقف السيارة. انتهى الخلاف. ما هددني ولا اعتدى علي. ما صار ضرر أو خسارة. أنا مش خايف وما في خطر حاليًا. بدي نصائح وما بدي بلاغ.'
EXTORTION='اليوم العصر شخص بعرفه طلب مني 500 دينار خلال 24 ساعة وهددني بنشر صوري إذا ما دفعت. ما دفعت أي مبلغ. لم يتم نشر الصور. مش متأكد إذا معه الصور. عندي رسائل محفوظة. بدي توثيق.'
NEW_EVENT='في تطور جديد: رجع بعتلي رسالة وأرسل نسخة من الصورة وقال رح ينشرها الليلة. ما نشر الصور وما دفعت. ما في خطر حاليًا.'

def mocked_extractor(session,*args):
    session.analysis_mode='synthetic_mock_no_external_request'
    return collected_facts(session)

def generate(folder):
    folder.mkdir(parents=True,exist_ok=True)
    results=[]
    for name,narrative in [('synthetic_neighbors',BENIGN),('synthetic_extortion',EXTORTION)]:
        flow=InterviewFlow(); flow.new()
        transcript=[{'role':'user','text':narrative}]
        reply=flow.receive(narrative,'synthetic-mock',mocked_extractor)
        transcript.append({'role':'assistant','messages':reply.messages})
        while flow.stage=='answering':
            key=flow.session.pending_question['key']
            if name=='synthetic_extortion' and len(flow.session.answers)==1:
                answer=NEW_EVENT
            else:
                answer={'current_safety':'لا','recurrence':'هاي أول مرة',
                    'financial_loss':'ما خسرت مصاري','threat_capability':'ما بعرف',
                    'current_status':'التهديد لسه مستمر. أقصد نفس الرسالة. ما وصلتني رسائل جديدة.'}.get(key,'ما بعرف')
            transcript.append({'role':'user','question_id':flow.session.pending_question['id'],'question_key':key,'text':answer})
            reply=flow.receive(answer,'synthetic-mock',mocked_extractor)
            transcript.append({'role':'assistant','messages':reply.messages})
        if flow.stage=='draft_consent':
            reply=flow.receive('نعم','synthetic-mock',mocked_extractor)
            transcript.extend([{'role':'user','text':'نعم'},{'role':'assistant','messages':reply.messages}])
        report=flow.session.report
        report['limitations'].insert(0,'مثال اصطناعي بمحاكاة المستخرج؛ لم يُستدعَ نموذج حي ولم تُختبر خدمات خارجية. الدرجات هنا توضيح لقواعد الشدة فقط.')
        for suffix,content in [('json',json.dumps(report,ensure_ascii=False,indent=2)),
            ('md',render_markdown(report)),('html',render_html(report)),
            ('conversation.json',json.dumps(transcript,ensure_ascii=False,indent=2))]:
            (folder/f'{name}.{suffix}').write_text(content,encoding='utf-8')
        if flow.session.official_request=='draft_requested_not_submitted':
            (folder/f'{name}.official_draft.md').write_text(render_official_draft(report),encoding='utf-8')
        results.append({'example':name,'questions':len(flow.session.answers),'classification':report['classification'],
                        'route':report['risk']['recommended_route'],'score':report['risk']['risk_index']})
    print(json.dumps(results,ensure_ascii=True,indent=2))

if __name__=='__main__': generate(Path(__file__).resolve().parents[1]/'examples')
