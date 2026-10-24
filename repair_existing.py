"""Rebuild a *separate* source-grounded report from a previously saved MVP case.

The original JSON and audit history are never overwritten. This is a deterministic
correction pass and is not a second LLM extraction or an accuracy certification.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from pathlib import Path
import json
import re
from engine import QA, Session, assemble_report
from storage import output_dir, render_markdown, render_html, render_official_draft


def rebuild(path: Path, suffix: str = "reviewed") -> list[Path]:
    if not suffix.isascii() or not suffix.replace("_", "").isalnum():
        raise ValueError("اسم النسخة يجب أن يتكون من أحرف إنجليزية وأرقام وشرطة سفلية فقط")
    raw = json.loads(path.read_text(encoding='utf-8'))
    if not all(k in raw for k in ('id', 'narrative', 'answers')):
        raise ValueError('الملف ليس سجل قضية MVP صالحًا')
    if not isinstance(raw['id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',raw['id']):
        raise ValueError('Unsafe case identifier')
    session = Session(
        id=raw['id'], created_utc=raw.get('created_utc', datetime.now(timezone.utc).isoformat()),
        narrative=raw['narrative'],
        answers=[QA(**item) for item in raw['answers']],
        initial_facts=raw.get('initial_facts', []),
        final_facts=raw.get('final_facts', []),
        extraction_errors=raw.get('extraction_errors', []),
        official_request=raw.get('official_request', 'not_asked'),
    )
    report = assemble_report(session, model_worked=not bool(session.extraction_errors))
    report['reanalysis_notice'] = ('إعادة صياغة آلية بقواعد تصحيح بعد حفظ القضية. '
                                   'لم يتغير السجل الأصلي، ولم تُراجع الوقائع بشريًا.')
    report['original_risk_index'] = (raw.get('report') or {}).get('risk', {}).get('risk_index')
    results = {
        f'.{suffix}.json': json.dumps({'id':session.id,'report':report,
                                      'original_case_file':str(path)}, ensure_ascii=False, indent=2),
        f'.{suffix}.md': render_markdown(report),
        f'.{suffix}.html': render_html(report),
    }
    if session.official_request == 'draft_requested_not_submitted':
        results[f'.official_draft.{suffix}.md'] = render_official_draft(report)
    output = []
    for ext, value in results.items():
        destination = path.parent / (session.id + ext)
        # Preserve existing corrected copies by requiring a new output name.
        if destination.exists():
            raise FileExistsError(f'النسخة المصححة موجودة أصلًا: {destination}')
        destination.write_text(value, encoding='utf-8')
        output.append(destination)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description='Create corrected COPY of an earlier MVP report')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--case', help='مثل TEST-ba946ca572b6')
    group.add_argument('--file', type=Path, help='مسار ملف JSON للقضية')
    parser.add_argument('--suffix', default='reviewed', help='اسم مستقل لنسخة التقرير، مثل timeline')
    args = parser.parse_args()
    target = output_dir() / (args.case + '.json') if args.case else args.file
    if not target.is_file():
        parser.error(f'لا يوجد ملف بهذا المسار: {target}')
    print('الملف الأصلي:', target)
    for file in rebuild(target, suffix=args.suffix):
        print('نسخة مراجعة:', file)
    print('لا يوجد تقديم بلاغ رسمي. لا تعتبر هذه العملية اعتمادًا للوقائع.')


if __name__ == '__main__':
    main()
