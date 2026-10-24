"""Bounded OpenAI-compatible gateway; provider errors never expose prompt or keys."""
from __future__ import annotations
import json
import os
import time
import httpx
from config import validate_url, integer

class LLMUnavailable(RuntimeError):
    pass

def hosted_completion(system: str,prompt: str,model: str,schema: dict,*,settings: dict | None=None) -> str:
    settings=settings or {}
    key=settings.get('key',os.getenv('LLM_API_KEY','')).strip()
    if not key: raise LLMUnavailable('LLM_API_KEY غير مضبوط على السيرفر.')
    base=validate_url(settings.get('base_url',os.getenv('LLM_BASE_URL','https://openrouter.ai/api/v1')))
    mode=os.getenv('LLM_JSON_MODE','auto')
    if mode not in {'auto','json_schema','json_object'}: raise LLMUnavailable('LLM_JSON_MODE invalid')
    budget=integer('LLM_TIMEOUT_SECONDS',35,5,120)
    attempts=integer('LLM_MAX_ATTEMPTS',3,1,3)
    deadline=time.monotonic()+budget
    current='json_schema' if mode=='auto' else mode
    instructions=system+'\nJSON schema:\n'+json.dumps(schema,ensure_ascii=False)
    for attempt in range(attempts):
        remaining=deadline-time.monotonic()
        if remaining<=0: break
        fmt={'type':'json_schema','json_schema':{'name':'accp_extraction','strict':True,'schema':schema}} if current=='json_schema' else {'type':'json_object'}
        payload={'model':model,'messages':[{'role':'system','content':instructions},{'role':'user','content':prompt}],
            'temperature':0,'max_tokens':3000,'response_format':fmt}
        retry=False
        try:
            response=httpx.post(base+'/chat/completions',headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},
                json=payload,timeout=httpx.Timeout(min(remaining,15),connect=min(remaining,5)),follow_redirects=False)
            response.raise_for_status()
            data=response.json()
        except httpx.HTTPStatusError as error:
            status=error.response.status_code
            # Unsupported schema is not an invitation to retry authentication or permission errors.
            hint=error.response.text[:2000].lower()
            if mode=='auto' and current=='json_schema' and status in {400,422} and any(x in hint for x in ('response_format','json_schema','structured output')):
                current='json_object'
                retry=True
            elif status in {408,429,500,502,503,504}:
                retry=True
            else:
                raise LLMUnavailable(f'خدمة النموذج أعادت HTTP {status}.') from None
        except (httpx.TimeoutException,httpx.RequestError):
            retry=True
        except (ValueError,TypeError):
            raise LLMUnavailable('خدمة النموذج لم ترجع JSON صالحًا.') from None
        if retry:
            if attempt+1<attempts:
                time.sleep(min(0.25*(2**attempt),max(0,deadline-time.monotonic())))
                continue
            break
        try:
            choice=data['choices'][0]
            if choice.get('finish_reason') in {'length','content_filter'}: raise ValueError()
            content=choice['message'].get('content')
            if not isinstance(content,str) or not content.strip() or len(content)>100_000: raise ValueError()
            json.loads(content)
            return content
        except (KeyError,IndexError,TypeError,ValueError):
            raise LLMUnavailable('خدمة النموذج لم تُرجع نص JSON صالحًا وكاملًا.') from None
    raise LLMUnavailable('تعذّر التحليل ضمن المهلة؛ يلزم إعادة المحاولة لاحقًا أو مراجعة بشرية.')
