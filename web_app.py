"""ACCP.AI browser application. One Uvicorn worker, SQLite, same-origin cookie sessions."""
from __future__ import annotations
import asyncio
from collections import defaultdict,deque
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import time
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI,Request,HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse,JSONResponse,Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,ConfigDict,Field
from bot_flow import InterviewFlow
from cloud_llm import hosted_completion
from config import validate_url
from storage import render_html,render_markdown,render_official_draft
from web_dialogue import WebDialogue,browser_messages
from web_store import WebStore,StaleSession

ROOT=Path(__file__).resolve().parent
COOKIE='accp_session'

class BodyLimit:
    """Bound streamed request bodies too; Content-Length is not trusted."""
    def __init__(self,app,limit=16000): self.app,self.limit=app,limit
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope['method'] in {'GET','HEAD'}:
            return await self.app(scope,receive,send)
        messages=[]
        size=0
        while True:
            message=await receive()
            size+=len(message.get('body',b''))
            if size>self.limit:
                return await JSONResponse({'detail':'الرسالة أطول من الحد المسموح.'},status_code=413)(scope,receive,send)
            messages.append(message)
            if not message.get('more_body',False): break
        async def replay():
            return messages.pop(0) if messages else await receive()
        await self.app(scope,replay,send)

class Input(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)

class Login(Input):
    password:str=Field(default='',max_length=256)

class ChatMessage(Input):
    text:str=Field(default='',max_length=3000)
    action:Literal['message','summary','explain','finish','consent_yes','consent_no']='message'
    request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{16,80}$')
    revision:int=Field(ge=0)

class ModelConfig(Input):
    key:str=Field(min_length=10,max_length=300)
    model:str=Field(min_length=1,max_length=160)
    base_url:str=Field(default='https://openrouter.ai/api/v1',max_length=300)

def server_credentials():
    key=os.getenv('LLM_API_KEY','').strip()
    model=os.getenv('LLM_MODEL','').strip()
    return {'key':key,'model':model,'base_url':validate_url(os.getenv('LLM_BASE_URL','https://openrouter.ai/api/v1'))} if key and model else None

def create_app(database=None,*,password=None,public_url=None,credentials=None,extractor=None):
    public_url=(public_url if public_url is not None else os.getenv('PUBLIC_URL','')).rstrip('/')
    if not public_url and os.getenv('RAILWAY_PUBLIC_DOMAIN'): public_url='https://'+os.environ['RAILWAY_PUBLIC_DOMAIN']
    password=password if password is not None else os.getenv('WEB_ACCESS_PASSWORD','')
    production=bool(public_url or os.getenv('RAILWAY_ENVIRONMENT_ID'))
    if production and len(password)<12: raise RuntimeError('Set WEB_ACCESS_PASSWORD with at least 12 characters before public deployment')
    if public_url and (urlsplit(public_url).scheme!='https' or urlsplit(public_url).path not in {'','/'}):
        raise RuntimeError('PUBLIC_URL must be the HTTPS site origin')
    data=Path(os.getenv('DATA_DIR','data'))
    if os.getenv('RAILWAY_ENVIRONMENT_ID'):
        mount=os.getenv('RAILWAY_VOLUME_MOUNT_PATH','')
        if not mount or Path(mount).resolve()!=data.resolve(): raise RuntimeError('Attach a persistent volume at DATA_DIR')
    store=WebStore(database or data/'web.sqlite3',int(os.getenv('DATA_RETENTION_HOURS','24')))
    base_credentials=credentials if credentials is not None else server_credentials()
    personal={}
    locks=defaultdict(asyncio.Lock)
    quotas=defaultdict(deque)
    workers=asyncio.Semaphore(3)

    async def cleanup():
        while True:
            await asyncio.sleep(3600)
            await asyncio.to_thread(store.purge)
            # Session credentials have the same maximum memory lifetime as case data.
            cutoff=time.time()-store.ttl
            for identity in list(personal):
                if personal[identity]['created']<cutoff: personal.pop(identity,None)
            for identity in list(quotas):
                if not quotas[identity] or quotas[identity][-1]<time.monotonic()-3600:
                    quotas.pop(identity,None)

    @asynccontextmanager
    async def lifespan(app):
        task=asyncio.create_task(cleanup())
        yield
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass
        personal.clear()

    app=FastAPI(title='ACCP.AI Web',docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.state.store=store
    app.state.personal_credentials=personal

    @app.middleware('http')
    async def security(request,call_next):
        host=request.url.hostname
        allowed_hosts={'localhost','127.0.0.1','testserver'}
        if public_url: allowed_hosts.add(urlsplit(public_url).hostname)
        if request.url.path=='/health': allowed_hosts.add('healthcheck.railway.app')
        if host not in allowed_hosts: return JSONResponse({'detail':'عنوان المضيف غير مسموح.'},status_code=400)
        if request.method not in {'GET','HEAD'}:
            origin=request.headers.get('origin')
            expected=public_url or str(request.base_url).rstrip('/')
            if (origin and origin.rstrip('/')!=expected) or request.headers.get('sec-fetch-site')=='cross-site':
                return JSONResponse({'detail':'الطلب من موقع آخر غير مسموح.'},status_code=403)
            if request.headers.get('content-type','').split(';')[0]!='application/json':
                return JSONResponse({'detail':'يلزم طلب JSON.'},status_code=415)
            try: length=int(request.headers.get('content-length','0'))
            except ValueError: length=20001
            if length>16000: return JSONResponse({'detail':'الرسالة أطول من الحد المسموح.'},status_code=413)
        try: response=await call_next(request)
        except Exception as error:
            logging.getLogger('accp.web').warning('request_failed type=%s',type(error).__name__)
            response=JSONResponse({'detail':'تعذّر إكمال الطلب. حدّث الصفحة لاستعادة الحالة المحفوظة.'},status_code=500)
        response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
            'Referrer-Policy':'no-referrer','X-Frame-Options':'DENY',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid(request,error):
        return JSONResponse({'detail':'راجع طول الرسالة والحقول المطلوبة ثم أعد المحاولة.'},status_code=422)

    @app.exception_handler(Exception)
    async def safe_error(request,error):
        logging.getLogger('accp.web').warning('request_failed type=%s',type(error).__name__)
        return JSONResponse({'detail':'تعذّر إكمال الطلب. حدّث الصفحة لاستعادة آخر حالة محفوظة.'},status_code=500)

    def session(request,mutation=False):
        token=request.cookies.get(COOKIE,'')
        state=store.load(token)
        if not state: raise HTTPException(401,'انتهت الجلسة. افتح جلسة جديدة.')
        if mutation and not hmac.compare_digest(request.headers.get('x-csrf-token',''),state['csrf']):
            raise HTTPException(403,'حدّث الصفحة ثم أعد المحاولة.')
        return token,state

    def model_for(token):
        value=personal.get(store.identifier(token))
        if value and value['created']>time.time()-store.ttl: return value['credentials']
        return base_credentials

    def throttle(identity,limit=30):
        now=time.monotonic()
        queue=quotas[identity]
        while queue and now-queue[0]>60: queue.popleft()
        if len(queue)>=limit: raise HTTPException(429,'طلبات كثيرة خلال دقيقة. انتظر قليلًا ثم حاول.')
        queue.append(now)

    def view(token,state):
        flow=InterviewFlow.from_dict(state['flow']) if state['flow'] else InterviewFlow()
        settings=model_for(token)
        s=flow.session
        return {'csrf':state['csrf'],'revision':state['revision'],'messages':state['history'],
            'stage':flow.stage,'case_id':s.id if s else None,'question':s.pending_question if s else None,
            'answered':min(6,sum(a.key!='user_update' for a in s.answers)) if s else 0,
            'report':s.report if s else None,'model':settings['model'] if settings else None,
            'mode':'cloud' if settings else 'offline','last_error':state.get('last_error',False),
            'base_url':os.getenv('LLM_BASE_URL','https://openrouter.ai/api/v1')}

    def new_session(response):
        token,state=store.create()
        response.set_cookie(COOKIE,token,httponly=True,secure=production,samesite='strict',max_age=store.ttl,path='/')
        return token,state

    @app.get('/')
    async def index(): return FileResponse(ROOT/'web'/'index.html')

    @app.get('/health')
    async def health(): return {'status':'ok','application':'accp-web'}

    @app.get('/api/state')
    async def get_state(request:Request):
        token=request.cookies.get(COOKIE,'')
        state=store.load(token)
        response=JSONResponse({})
        if not state:
            if password: return JSONResponse({'login_required':True},status_code=401)
            token,state=new_session(response)
        response.body=json.dumps(view(token,state),ensure_ascii=False).encode()
        response.headers['content-length']=str(len(response.body))
        return response

    @app.post('/api/login')
    async def login(payload:Login,request:Request):
        identity='login:'+hashlib.sha256((request.client.host if request.client else 'unknown').encode()).hexdigest()
        throttle(identity,10)
        if not hmac.compare_digest(payload.password,password): raise HTTPException(401,'رمز الدخول غير صحيح.')
        response=JSONResponse({})
        token,state=new_session(response)
        response.body=json.dumps(view(token,state),ensure_ascii=False).encode()
        response.headers['content-length']=str(len(response.body))
        return response

    @app.post('/api/message')
    async def message(payload:ChatMessage,request:Request):
        token,_=session(request,True)
        identity=store.identifier(token)
        async with locks[identity]:
            state=store.load(token)
            if not state: raise HTTPException(401,'انتهت الجلسة.')
            fingerprint=hashlib.sha256((payload.action+'\0'+payload.text).encode()).hexdigest()
            previous=state['requests'].get(payload.request_id)
            if previous:
                if previous!=fingerprint: raise HTTPException(409,'معرف الطلب مستخدم لرسالة أخرى.')
                return view(token,state)
            if state['revision']!=payload.revision: raise HTTPException(409,'تغيرت المحادثة في نافذة أخرى. حدّثها قبل الإجابة.')
            throttle(identity)
            text=payload.text.strip()
            if payload.action=='message' and not text: raise HTTPException(422,'اكتب رسالة أولًا.')
            flow=InterviewFlow.from_dict(state['flow']) if state['flow'] else InterviewFlow()
            if payload.action.startswith('consent_'):
                if flow.stage!='draft_consent': raise HTTPException(409,'لا توجد موافقة معلقة لهذه الحالة.')
                text='نعم' if payload.action=='consent_yes' else 'لا'
            action=payload.action if not payload.action.startswith('consent_') else 'message'
            labels={'summary':'لخّص ما قلته','explain':'وضح لي السؤال','finish':'أنهِ المقابلة وأعد التقرير'}
            dialogue=WebDialogue(model_for(token),extractor)
            async with workers:
                reply,failed=await asyncio.to_thread(dialogue.receive,flow,text or labels.get(action,''),action)
            state['history'].append({'role':'user','text':text or labels.get(action,'')})
            for value in browser_messages(reply,flow):
                state['history'].append({'role':'assistant','text':value})
            state['history']=state['history'][-80:]
            state['flow']=flow.to_dict()
            state['last_error']=failed
            state['requests'][payload.request_id]=fingerprint
            state['requests']=dict(list(state['requests'].items())[-40:])
            try: store.save(token,state,payload.revision)
            except StaleSession: raise HTTPException(409,'تغيرت الحالة. حدّث الصفحة.') from None
            return view(token,state)

    @app.post('/api/reset')
    async def reset(request:Request):
        token,_=session(request,True)
        identity=store.identifier(token)
        async with locks[identity]:
            store.delete(token)
            current_settings=personal.pop(identity,None)
            response=JSONResponse({})
            fresh,state=new_session(response)
            if current_settings: personal[store.identifier(fresh)]=current_settings
            response.body=json.dumps(view(fresh,state),ensure_ascii=False).encode()
            response.headers['content-length']=str(len(response.body))
            return response

    @app.post('/api/model')
    async def configure(payload:ModelConfig,request:Request):
        token,_=session(request,True)
        identity=store.identifier(token)
        throttle('model:'+identity,5)
        try: base=validate_url(payload.base_url)
        except RuntimeError: raise HTTPException(422,'استخدم عنوان HTTPS صالحًا للمزود.') from None
        allowed={'openrouter.ai','api.openai.com','api.groq.com','api.together.xyz','api.deepseek.com'}
        allowed.add(urlsplit(os.getenv('LLM_BASE_URL','https://openrouter.ai/api/v1')).hostname)
        if urlsplit(base).hostname not in allowed or urlsplit(base).port not in {None,443}:
            raise HTTPException(422,'المزود غير مسموح من الواجهة. اضبط LLM_BASE_URL على الخادم لمزود آخر موثوق.')
        settings={'key':payload.key.strip(),'model':payload.model.strip(),'base_url':base}
        schema={'type':'object','additionalProperties':False,'required':['ready'],'properties':{'ready':{'type':'boolean'}}}
        async with locks[identity]:
            async with workers:
                try:
                    raw=await asyncio.to_thread(hosted_completion,'أعد JSON مطابقًا للمخطط.', '{"ready": true}',settings['model'],schema,settings=settings)
                    if json.loads(raw).get('ready') is not True: raise ValueError()
                except Exception: raise HTTPException(502,'لم ينجح اختبار النموذج. راجع المفتاح واسم النموذج ورصيد المزود؛ لم يُحفظ المفتاح.') from None
            personal[identity]={'credentials':settings,'created':time.time()}
            return view(token,store.load(token))

    @app.delete('/api/model')
    async def remove_model(request:Request):
        token,state=session(request,True)
        async with locks[store.identifier(token)]: personal.pop(store.identifier(token),None)
        return view(token,state)

    @app.get('/api/report/{format}')
    async def report(format:str,request:Request):
        token,state=session(request)
        flow=InterviewFlow.from_dict(state['flow']) if state['flow'] else InterviewFlow()
        if not flow.session or not flow.session.report: raise HTTPException(404,'لا يوجد تقرير جاهز بعد.')
        record=flow.session.report
        if format=='json': content,mime,suffix=json.dumps(record,ensure_ascii=False,indent=2),'application/json','.json'
        elif format=='html': content,mime,suffix=render_html(record),'text/html','.html'
        elif format=='md': content,mime,suffix=render_markdown(record),'text/markdown','.md'
        elif format=='draft':
            try: content=render_official_draft(record)
            except PermissionError: raise HTTPException(403,'إنشاء المسودة يحتاج موافقة صريحة.') from None
            mime,suffix='text/markdown','.official_draft.md'
        else: raise HTTPException(404,'صيغة غير مدعومة.')
        return Response(content,media_type=mime,headers={'Content-Disposition':f'attachment; filename="{flow.session.id}{suffix}"'})

    app.mount('/static',StaticFiles(directory=ROOT/'web'),name='static')
    app.add_middleware(BodyLimit)
    return app

def main():
    import uvicorn
    from worker_lock import WorkerLock
    for name in ('httpx','httpcore','uvicorn.access'): logging.getLogger(name).setLevel(logging.CRITICAL)
    host=os.getenv('HOST','0.0.0.0' if os.getenv('RAILWAY_ENVIRONMENT_ID') else '127.0.0.1')
    if host not in {'127.0.0.1','localhost'} and len(os.getenv('WEB_ACCESS_PASSWORD',''))<12:
        raise RuntimeError('Public binding requires WEB_ACCESS_PASSWORD (12+ characters)')
    port=int(os.getenv('PORT','8000'))
    with WorkerLock(Path(os.getenv('DATA_DIR','data'))/'web.lock'):
        app=create_app()
        print(f'ACCP.AI Web: http://{host}:{port}',flush=True)
        uvicorn.run(app,host=host,port=port,workers=1,access_log=False,log_level='warning',proxy_headers=False)

if __name__=='__main__': main()
