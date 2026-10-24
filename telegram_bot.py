"""Private Telegram adapter: persistent sessions, ordered updates and safe exports."""
from __future__ import annotations
import asyncio
import io
import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.constants import ChatAction, ChatType
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from bot_flow import InterviewFlow
from config import Settings
from persistence import SessionStore
from service import InterviewService, HELP

log=logging.getLogger('accp.telegram')

def config():
    settings=Settings.from_env()
    return settings.token,set(settings.allowed),settings.model

def is_permitted(update,allowed):
    return bool(update.effective_chat and update.effective_chat.type==ChatType.PRIVATE and
                update.effective_user and update.effective_user.id in allowed)

def chunks(text,limit=3600):
    # Telegram offsets/limits use UTF-16 units; emoji consume two units.
    chunk=[]
    size=0
    for char in text:
        units=2 if ord(char)>0xffff else 1
        if size+units>limit:
            yield ''.join(chunk)
            chunk=[]
            size=0
        chunk.append(char)
        size+=units
    if chunk: yield ''.join(chunk)

async def reply(update,text,reply_markup=None):
    parts=list(chunks(text))
    for i,part in enumerate(parts):
        await update.effective_message.reply_text(part,reply_markup=reply_markup if i==len(parts)-1 else None,
                                                  protect_content=True)

async def send_document(update,content,filename):
    data=io.BytesIO(content.encode('utf-8'))
    data.name=filename
    await update.effective_message.reply_document(document=data,filename=filename,
        caption='تقرير تجريبي للمراجعة — جميع الوقائع غير متحقق منها.',protect_content=True)

async def gate(update,context):
    if not is_permitted(update,context.application.bot_data['allowed']):
        if update.effective_chat and update.effective_chat.type==ChatType.PRIVATE:
            await reply(update,'هذا بوت عرض خاص. استخدم /id لمعرفة معرفك؛ يضيفه مسؤول العرض إلى قائمة السماح.')
        return False
    return True

async def id_command(update,context):
    if update.effective_chat and update.effective_chat.type==ChatType.PRIVATE and update.effective_user:
        await reply(update,f'معرّف Telegram الخاص بك: {update.effective_user.id}')

def keyboard(result):
    case=result.get('case_id')
    if result.get('stage')=='draft_consent' and case:
        return InlineKeyboardMarkup([[InlineKeyboardButton('إنشاء مسودة للمراجعة',callback_data='consent:yes:'+case),
            InlineKeyboardButton('تجاوز',callback_data='consent:no:'+case)]])
    return InlineKeyboardMarkup([[InlineKeyboardButton('الحالة',callback_data='cmd:status'),
        InlineKeyboardButton('التقرير',callback_data='cmd:report')]])

async def dispatch(update,context,command=None,text=None,expected_case=None):
    if not await gate(update,context): return
    service=context.application.bot_data['service']
    user_id=update.effective_user.id
    _,arrival_revision,_=service.store.load(user_id)
    value=text if text is not None else (update.effective_message.text or '')
    if not command and not service.store.seen(user_id,update.update_id):
        flow,_,_=service.snapshot(user_id)
        if flow.stage in {'narrative','answering'}:
            await reply(update,'جارٍ مراجعة الإفادة…')
            await context.bot.send_chat_action(chat_id=update.effective_chat.id,action=ChatAction.TYPING)
    async def deliver(result):
        messages=result.get('messages',[])
        for index,message in enumerate(messages):
            await reply(update,message,keyboard(result) if index==len(messages)-1 else None)
        for document in result.get('documents',[]):
            await send_document(update,document['content'],document['name'])
    await service.process(user_id,update.update_id,value,command,
        message_id=0 if update.callback_query else update.effective_message.message_id,
        expected_case=expected_case,deliver=deliver,arrival_revision=arrival_revision)

async def start(update,context):
    command=(update.effective_message.text or '/start').split()[0].split('@')[0].removeprefix('/')
    await dispatch(update,context,command=command)

async def help_command(update,context): await dispatch(update,context,command='help')
async def cancel(update,context): await dispatch(update,context,command='cancel')
async def reset(update,context): await dispatch(update,context,command='reset')
async def status(update,context): await dispatch(update,context,command='status')
async def report_command(update,context): await dispatch(update,context,command='report')
async def text_message(update,context): await dispatch(update,context)

async def callback(update,context):
    if not update.callback_query: return
    await update.callback_query.answer()
    data=update.callback_query.data or ''
    if data in {'cmd:status','cmd:report'}:
        await dispatch(update,context,command=data.split(':')[1])
    elif data.startswith('consent:'):
        parts=data.split(':')
        if len(parts)==3 and parts[1] in {'yes','no'}:
            await dispatch(update,context,text=parts[1],expected_case=parts[2])

async def other_message(update,context):
    if await gate(update,context):
        await reply(update,'هذه النسخة تقبل النصوص فقط. لا ترسل صورًا خاصة أو وثائق حساسة.')

async def on_error(update,context):
    log.error('handler_failed type=%s',type(context.error).__name__)
    if isinstance(update,Update) and update.effective_message:
        try: await reply(update,'تعذّر إكمال الطلب. حالتك المحفوظة متاحة عبر /status والتقرير عبر /report؛ لا ترسل بيانات حساسة.')
        except Exception: pass

async def cleanup_loop(store):
    while True:
        await asyncio.sleep(3600)
        await asyncio.to_thread(store.purge)

async def post_init(app):
    commands=[BotCommand(name,label) for name,label in [('start','البدء أو متابعة المقابلة'),('new','قضية جديدة'),
        ('help','المساعدة'),('status','الحالة'),('report','التقرير'),('cancel','إلغاء الجلسة'),('reset','حذف البيانات'),('id','معرفك')]]
    await app.bot.set_my_commands(commands)
    app.bot_data['cleanup_task']=asyncio.create_task(cleanup_loop(app.bot_data['service'].store))

async def post_shutdown(app):
    task=app.bot_data.get('cleanup_task')
    if task:
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass

def build_application(settings,store=None):
    os.environ['LLM_PROVIDER']='cloud'
    app=(Application.builder().token(settings.token).concurrent_updates(16)
         .post_init(post_init).post_shutdown(post_shutdown).build())
    store=store or SessionStore(settings.database,settings.retention_hours)
    app.bot_data.update(allowed=set(settings.allowed),model=settings.model,
        service=InterviewService(store,settings.model,max_parallel=settings.max_parallel_llm))
    for names,handler in [(['start','new'],start),('id',id_command),('help',help_command),('status',status),
        ('report',report_command),('cancel',cancel),('reset',reset)]:
        app.add_handler(CommandHandler(names,handler))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_message))
    app.add_handler(MessageHandler(~filters.COMMAND,other_message))
    app.add_error_handler(on_error)
    return app

def main():
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s')
    for name in ('httpx','httpcore','telegram'):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    settings=Settings.from_env()
    from worker_lock import WorkerLock
    with WorkerLock(settings.database.parent/'polling.lock'):
        app=build_application(settings)
        log.info('worker_started allowed_users=%d',len(settings.allowed))
        app.run_polling(drop_pending_updates=False,allowed_updates=['message','callback_query'])

if __name__=='__main__': main()
