"""Small synthetic smoke benchmark, NOT independent reliability validation."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import Session
from nlp import extract

# Hand-authored examples, avoid training on these. Only complaint category codes
# are assessed, not safety probability or legal validity.
CASES = [
    ('بدي أشتكي على شخص هددني ينشر صوري إذا ما حولتله 500 دينار.', {'extortion','publication_threat'}),
    ('صار بيني وبين جاري خلاف على موقف السيارة، لكن ما هددني.', {'ordinary_dispute'}),
    ('شخص قال لي بشكل مباشر رح أقتلك إذا طلعت من البيت.', {'death_threat'}),
    ('انسحب مبلغ من حسابي البنكي دون إذني، ولم أعط أحد كلمة المرور.', {'unauthorized_debit'}),
    ('شخص دخل على حسابي وغير كلمة السر وصار هو يتحكم فيه.', {'account_takeover'}),
    ('الشخص نشر صوري الشخصية فعلًا على حسابه.', {'publication_occurred'}),
    ('هددني ينشر معلوماتي الخاصة، لكنه ما نشر أي شيء حتى الآن.', {'publication_threat'}),
    ('اختلفنا على تنظيف الدرج في العمارة بدون أي تهديد.', {'ordinary_dispute'}),
    ('اعتدى علي شخص وضربني في الشارع.', {'physical_assault'}),
    ('شخص نشر معلومات كاذبة عني أمام الناس لتشويه سمعتي.', {'defamation'}),
    ('طلب مني صاحب المحل 30 دينارًا مقابل تصليح الجهاز حسب الاتفاق.', set()),
    ('قال أحدهم إني هددته، لكن أنا لم أهدده.', set()),
]
TARGET = {'extortion','publication_threat','ordinary_dispute','death_threat',
          'unauthorized_debit','account_takeover','publication_occurred',
          'physical_assault','defamation','verbal_threat','financial_fraud'}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--model',default='qwen3.5:4b-q4_K_M')
    p.add_argument('--details',action='store_true',help='بيّن مصدر التصنيفات وأخطاء كل حالة')
    args=p.parse_args()
    tp=fp=fn=0
    durations=[]
    for i,(sentence, gold) in enumerate(CASES,1):
        s=Session(narrative=sentence)
        t=time.perf_counter()
        try:
            facts=extract(s,args.model,False)
            pred={x['kind'] for x in facts if x['polarity']=='reported' and x['kind'] in TARGET}
            llm={x['kind'] for x in facts if x['polarity']=='reported' and x['kind'] in TARGET and x.get('extraction_method')=='llm_proposal'}
            backstop={x['kind'] for x in facts if x['polarity']=='reported' and x['kind'] in TARGET and x.get('extraction_method')=='explicit_rule_candidate'}
        except Exception as e:
            print(f'{i:02}: ERROR {type(e).__name__}: {e}')
            pred=set()
            llm=backstop=set()
        duration=time.perf_counter()-t
        durations.append(duration)
        tp+=len(pred&gold)
        fp+=len(pred-gold)
        fn+=len(gold-pred)
        print(f'{i:02}: {duration:.1f}s | wanted={sorted(gold)} | found={sorted(pred)}')
        if args.details:
            print(f'      llm={sorted(llm)} rule_candidates={sorted(backstop)} extra={sorted(pred-gold)} missing={sorted(gold-pred)}')
    precision=tp/(tp+fp) if (tp+fp) else 0
    recall=tp/(tp+fn) if (tp+fn) else 0
    f1=2*precision*recall/(precision+recall) if (precision+recall) else 0
    print(f'\nMicro P={precision:.3f} R={recall:.3f} F1={f1:.3f} | TP={tp} FP={fp} FN={fn}')
    print(f'Average latency: {sum(durations)/len(durations):.1f}s')
    print('12 synthetic smoke tests only. NOT a safety, legal, or real-world accuracy certification.')
    print('CAUTION: this diagnostic set was inspected during remediation; improvement here is NOT independent evidence of generalization.')

if __name__=='__main__':
    main()
