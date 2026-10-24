"""Reproducible synthetic metrics; offline rules and real hosted inference separate."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from evaluation.heldout_release import CASES
from domain import Session,QA,COMPLAINT_CATEGORIES
from fact_state import FactState
from grounding import source_texts
from bot_flow import InterviewFlow
from nlp import extract

def metrics(tp,fp,fn):
    p=tp/(tp+fp) if tp+fp else 0
    r=tp/(tp+fn) if tp+fn else 0
    return {'precision':p,'recall':r,'f1':2*p*r/(p+r) if p+r else 0,'tp':tp,'fp':fp,'fn':fn}

def evaluate(live=False,model=''):
    rows=[]
    durations=[]
    counts={kind:[0,0,0] for kind in sorted(COMPLAINT_CATEGORIES-{'other'})}
    repeated=leading=completed=unsupported_quotes=unsupported_states=0
    for case in CASES:
        session=Session(narrative=case['narrative'])
        session.answers=[QA(key,'سياق تقييم اصطناعي',answer) for key,answer in case['turns']]
        start=time.perf_counter()
        error=None
        if live:
            try: session.final_facts=extract(session,model,True)
            except Exception as e: error=type(e).__name__
        state=FactState.build(session)
        duration=time.perf_counter()-start
        durations.append(duration)
        gold=set(case['gold_classes'])
        predicted=state.classifications
        for kind,values in counts.items():
            values[0]+=int(kind in predicted and kind in gold)
            values[1]+=int(kind in predicted and kind not in gold)
            values[2]+=int(kind not in predicted and kind in gold)
        sources=source_texts(session)
        bad_quotes=sum(sources[f['source']][f['start']:f['end']]!=f['quote'] for f in state.observations)
        actual={(k,f['polarity']) for k,f in state.current.items()}
        # Category subsumption: generic verbal threat is not a separate class under a specific threat.
        if predicted & {'death_threat','publication_threat','extortion'}:
            actual.discard(('verbal_threat','reported'))
        gold_facts={tuple(f) for f in case['gold_fact_states']}
        extras=actual-gold_facts
        unsupported_quotes+=bad_quotes
        unsupported_states+=len(extras)
        # Conversation completion/wording audit uses deterministic local extraction in ALL modes;
        # avoids pretending unknown scripted replies are real hosted conversations.
        flow=InterviewFlow(); flow.new()
        local=lambda *a: []
        flow.receive(case['narrative'],'offline',local)
        keys=[]
        while flow.stage=='answering' and len(keys)<7:
            pending=flow.session.pending_question
            keys.append(pending['key'])
            if 'تنفيذ تهديده' in pending['text'] or 'تهديد حقيقي' in pending['text']: leading+=1
            flow.receive('ما بعرف','offline',local)
        repeated+=len(keys)-len(set(keys))
        if flow.stage=='draft_consent': flow.receive('لا','offline',local)
        completed+=int(flow.stage=='complete' and len(flow.session.answers)<=6)
        rows.append({'id':case['id'],'gold':sorted(gold),'predicted':sorted(predicted),
            'false_labels':sorted(predicted-gold),'missed_labels':sorted(gold-predicted),
            'extra_fact_states':[list(x) for x in sorted(extras)],
            'missed_fact_states':[list(x) for x in sorted(gold_facts-actual)],
            'unsupported_quote_count':bad_quotes,'latency_ms':duration*1000,'question_keys':keys,'live_error':error})
    total=[sum(v[i] for v in counts.values()) for i in range(3)]
    per_class={kind:metrics(*v) for kind,v in counts.items()}
    populated=[v['f1'] for v in per_class.values() if v['tp']+v['fn']>0]
    data_path=Path(__file__).with_name('heldout_release.py')
    return {'dataset':'synthetic-final-heldout-authored-reference','dataset_sha256':hashlib.sha256(data_path.read_bytes()).hexdigest(),
        'mode':'hosted_llm_plus_semantic_validation' if live else 'offline_semantic_rules',
        'case_count':len(CASES),'micro':metrics(*total),'macro_f1_present_classes':statistics.mean(populated),
        'per_class':per_class,'false_classification_count':total[1],
        'unsupported_quote_count':unsupported_quotes,'extra_fact_states_against_reference':unsupported_states,
        'duplicate_questions':repeated,'leading_question_heuristic_flags':leading,
        'interview_completion_rate':completed/len(CASES),
        'latency_ms':{'mean':statistics.mean(durations)*1000,'p95':sorted(durations)[int(.95*(len(durations)-1))]*1000},
        'live_successful_requests':sum(row['live_error'] is None for row in rows) if live else None,
        'limitations':['Synthetic references authored before this run; not independently adjudicated by a human.',
            'Offline results do not measure a hosted model, Telegram network or Railway deployment.',
            'Question metrics use scripted unknown answers; leading-question detection is a narrow heuristic.',
            'Extra fact states measure disagreement with the synthetic annotation, not verified fabrication.',
            'No statistical safety, legal validity or real-world accuracy claim.'], 'cases':rows}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--model',default=os.getenv('LLM_MODEL',''))
    parser.add_argument('--output',type=Path,default=Path('quality/independent-results.json'))
    args=parser.parse_args()
    if args.live:
        if not args.model or not os.getenv('LLM_API_KEY'): parser.error('--live requires LLM_MODEL and LLM_API_KEY')
        os.environ['LLM_PROVIDER']='cloud'
    result=evaluate(args.live,args.model)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('mode','case_count','micro','false_classification_count','extra_fact_states_against_reference',
        'unsupported_quote_count','duplicate_questions','interview_completion_rate','latency_ms')},indent=2))
    if args.live and result['live_successful_requests']<len(CASES): raise SystemExit(2)

if __name__=='__main__': main()
