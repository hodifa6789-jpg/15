"""CLI using the same interview state machine as Telegram."""
from __future__ import annotations
import argparse
import os
from bot_flow import InterviewFlow,NOTICE,SAFETY
from nlp import extract
from domain import Session
from storage import save

def run(model: str,question_count: int=6,extractor=extract,read=input,write=print)->Session:
    if not 0<=question_count<=6: raise ValueError('question_count must be 0..6')
    flow=InterviewFlow()
    for message in flow.new().messages: write(message)
    while flow.stage in {'narrative','answering','draft_consent'}:
        if flow.stage=='answering' and len(flow.session.answers)>=question_count:
            result=flow._finish(['انتهت الأسئلة ضمن الحد المحدد.'])
        else:
            value=read('المشتكي: ').strip()
            if flow.stage=='narrative' and not value: return Session()
            result=flow.receive(value,model,extractor)
        for message in result.messages: write(message)
    if flow.session:
        for path in save(flow.session): write(str(path))
    return flow.session or Session()

def main():
    parser=argparse.ArgumentParser(description='ACCP.AI adaptive interview demo')
    parser.add_argument('--model',default=os.getenv('LLM_MODEL',''))
    parser.add_argument('--provider',choices=['cloud','offline','ollama'],default='cloud')
    parser.add_argument('--questions',type=int,choices=range(0,7),default=6)
    args=parser.parse_args()
    os.environ['LLM_PROVIDER']=args.provider
    if args.provider!='offline' and not args.model: parser.error('Set LLM_MODEL or --model')
    run(args.model,args.questions)

if __name__=='__main__': main()
