"""Human-readable Arabic reports and safely escaped HTML/Markdown exports."""
from __future__ import annotations
from dataclasses import asdict
import html
import json
import os
from pathlib import Path
import re
from domain import LABELS

STATUS={'reported':'مذكور بالإفادة','denied':'منفي','uncertain':'غير مؤكد','conflicting':'متعارض','unknown':'غير متوفر'}
OFFICIAL={'not_asked':'لم تُطلب مسودة','declined_or_deferred':'رُفضت المسودة أو أُجلت',
          'draft_requested_not_submitted':'مسودة بطلب صريح؛ لم تُقدَّم رسميًا'}

def output_dir():
    folder=Path(os.getenv('REPORT_DIR','data/reports'))
    folder.mkdir(parents=True,exist_ok=True)
    return folder

def source_label(source):
    return 'الإفادة الأصلية' if source=='initial_statement' else 'الإجابة '+source.removeprefix('answer_')

def fact_line(f):
    return LABELS.get(f['kind'],f['kind'])+': «'+f['quote']+'» — '+source_label(f['source'])+' — غير متحقق'

def sections(report):
    risk=report['risk']
    index=f"{risk['risk_index']}/100" if risk['risk_index'] is not None else 'غير قابل للتقدير'
    sections=[('الإفادة الأصلية',[report.get('original_statement','')]),('ملخص الحادثة',[report['summary']]),
        ('نوع القضية وتصنيفاتها',['، '.join(report['classification'])]),
        ('الوقائع المذكورة بالإفادة',[fact_line(f) for f in report.get('reported_facts',[])] or ['لا توجد وقائع مؤكدة الدلالة قابلة للاستخراج.']),
        ('الوقائع المنفية',[fact_line(f) for f in report.get('denied_facts',[])] or ['لا يوجد نفي صريح مستخرج؛ هذا لا يثبت حدوث الواقعة.']),
        ('المعلومات غير المؤكدة والتعارضات',[STATUS[f['polarity']]+' — '+fact_line(f) for f in report.get('uncertain_facts',[])] or ['لا توجد معلومة غير مؤكدة مستخرجة.']),
        ('التسلسل الزمني',[LABELS.get(e['kind'],{'copy_reported':'إرسال نسخة بحسب الإفادة','renewed_threat':'تهديد لاحق','near_term_threat':'مهلة قريبة','media_possession_uncertain':'عدم يقين حول امتلاك المواد'}.get(e['kind'],e['kind']))+': «'+e['quote']+'» — '+source_label(e['source'])
            for e in report.get('chronological_case_state',{}).get('events',[])] or ['لم تذكر تطورات زمنية مستقلة بعد الإفادة الأصلية.']),
        ('الأدلة التي ذكر المشتكي توفرها',[fact_line(f) for f in report.get('mentioned_evidence',[])] or ['لم يذكر دليلًا قابلًا للاستخراج. لم يفحص النظام أي دليل.']),
        ('المعلومات الناقصة المهمة',[LABELS.get(k,{'other_side':'وصف التصرف المحدد'}.get(k,'معلومة تحتاج استيضاحًا')) for k in report.get('missing_information',[])] or ['لا توجد معلومات إضافية تستدعي سؤالًا وفق الخطة الحالية.']),
        ('الشدة والسلامة والأولوية',[f'المؤشر التجريبي: {index} — {risk["risk_level"]}',
            'السلامة الحالية: '+{'reported_current':'إفادة بخطر حالي','denied_by_user':'نفي بحسب المشتكي','conflicting':'إفادات متعارضة','unknown':'غير محسومة'}[risk.get('immediate_safety_concern','unknown')],
            'الاستعجال الرقمي: '+{'urgent':'عاجل للمراجعة','attention':'يستدعي الانتباه','not_indicated':'غير مستدل عليه'}[risk.get('digital_urgency','not_indicated')],
            'أولوية المراجعة: '+{'immediate':'فورية','priority':'مرتفعة','standard':'عادية'}[risk.get('review_priority','standard')],*risk['factors'],risk['disclaimer']]),
        ('المسار المقترح',[risk.get('route_label',risk['route'])]),
        ('هدف المشتكي وإرشادات عامة',[report.get('user_goal') or 'لم يحدد هدفًا صريحًا.',*report.get('assisted_resolution_guidance',[])]),
        ('حدود التحليل وحالة التحقق',report['limitations']),
        ('الأسئلة والإجابات',[f'{i}. {a["question"]}\nالإجابة: {a["answer"]}' for i,a in enumerate(report['interview'],1)] or ['انتهت المقابلة دون أسئلة إضافية.']),
        ('حالة مسودة البلاغ',[OFFICIAL.get(report['official_report_status'],'حالة تحتاج مراجعة')])]
    return sections

def md_escape(text):
    return re.sub(r'([\\`*_{}\[\]()<>#!|])',r'\\\1',str(text))

def render_markdown(report):
    parts=[f'# تقرير ACCP.AI التجريبي — {md_escape(report["case_id"])}',
        'تاريخ الإنشاء (UTC): '+report['created_utc'],'جميع الوقائع غير متحقق منها — للمراجعة فقط']
    for title,lines in sections(report):
        parts.append('## '+title)
        parts.extend(md_escape(line) for line in lines)
    return '\n\n'.join(parts)+'\n'

def render_html(report):
    escape=html.escape
    body=''.join('<section><h2>'+escape(title)+'</h2>'+''.join('<p>'+escape(str(line)).replace('\n','<br>')+'</p>' for line in lines)+'</section>' for title,lines in sections(report))
    return '''<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'">
<title>تقرير ACCP.AI</title><style>
:root{color-scheme:light}body{background:#edf2f5;color:#142d38;font:17px/1.85 Tahoma,Arial,sans-serif;margin:0}
main{max-width:920px;margin:32px auto;padding:40px;background:white;border-top:8px solid #137f80}
h1{font-size:28px}h2{font-size:20px;color:#11666d;border-bottom:1px solid #dde7eb;padding-bottom:9px}
section{margin:32px 0}p{white-space:normal;overflow-wrap:anywhere}header{background:#e9f5f2;padding:24px;border-radius:10px}
.meta{font-size:14px;color:#49636b}@media(max-width:650px){main{margin:0;padding:22px}}
@media print{body{background:white}main{margin:0;padding:0;border:0}section{break-inside:avoid}h2{break-after:avoid}@page{size:A4;margin:18mm}}
</style></head><body><main><header><h1>تقرير إفادة تجريبية — ACCP.AI</h1><p class="meta">'''+escape(report['case_id'])+'<br>'+escape(report['created_utc'])+'</p><p>إفادات غير متحقق منها — للمراجعة البشرية فقط</p></header>'+body+'</main></body></html>'

def render_official_draft(report):
    if report.get('official_report_status')!='draft_requested_not_submitted':
        raise PermissionError('Explicit draft consent required')
    lines=['# مسودة بلاغ للمراجعة — لم تُقدَّم رسميًا','مرجع داخلي: '+report['case_id'],
        'الجهة المستقبلة: [تحدد عند المراجعة]','بيانات مقدم البلاغ: [يستكملها صاحبها خارج النظام عند الحاجة]',
        '## عرض الوقائع بحسب الإفادة',report['summary'],'## الإفادة الأصلية',report['original_statement'],
        '## التفاصيل ومصادرها']
    for kind,d in report.get('structured_details',{}).items():
        lines.append(LABELS.get(kind,kind)+': '+STATUS.get(d['polarity'],d['polarity'])+' — «'+d['quote']+'»')
    lines+=['## الأدلة','لم يفحص النظام أدلة. راجع ما ذكرت توفره وأرفقه بنفسك عند الحاجة.',
        '## المطلوب','أطلب مراجعة الوقائع والأدلة وتحديد الخطوة المناسبة من الجهة المختصة.',
        '## حدود المسودة','جميع الوقائع إفادات غير متحقق منها. تحتاج المسودة تدقيق صاحبها ومختص. لم تُرسل إلى أي جهة.']
    headings={0,4,6,8,len(lines)-6,len(lines)-4,len(lines)-2}
    return '\n\n'.join(line if i in headings else md_escape(line) for i,line in enumerate(lines))+'\n'

def save(session):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',session.id): raise ValueError('Unsafe case id')
    base=output_dir()/session.id
    docs={'.json':json.dumps(asdict(session),ensure_ascii=False,indent=2),
        '.md':render_markdown(session.report) if session.report else 'المقابلة غير مكتملة',
        '.html':render_html(session.report) if session.report else 'المقابلة غير مكتملة'}
    if session.official_request=='draft_requested_not_submitted' and session.report:
        docs['.official_draft.md']=render_official_draft(session.report)
    paths=[]
    for suffix,content in docs.items():
        path=base.with_suffix(suffix)
        temporary=path.with_suffix(suffix+'.tmp')
        temporary.write_text(content,encoding='utf-8')
        temporary.replace(path)
        paths.append(path)
    return paths
