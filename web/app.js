'use strict';
const $=id=>document.getElementById(id);
let state=null,busy=false,pending=null;
const examples={neighbors:'صار خلاف بيني وبين جاري حول موقف السيارة. انتهى الخلاف. ما هددني ولا اعتدى علي. ما صار ضرر أو خسارة. أنا مش خايف وما في خطر حاليًا. بدي نصائح وما بدي بلاغ.',extortion:'اليوم العصر شخص بعرفه طلب مني 500 دينار خلال 24 ساعة وهددني بنشر صوري إذا ما دفعت. ما دفعت أي مبلغ. لم يتم نشر الصور. مش متأكد إذا معه الصور. عندي رسائل محفوظة. بدي توثيق.',account:'شخص دخل حسابي دون إذني وغيّر كلمة السر. ما انخصم أي مبلغ من حساب البنك. معي رسائل التنبيه وبدي أعرف شو أعمل.'};
function notice(text){$('notice').textContent=text;$('notice').hidden=!text;}
async function api(path,options={}){
 const response=await fetch(path,{...options,headers:{'Content-Type':'application/json',...(state?{'X-CSRF-Token':state.csrf}:{}),...options.headers},credentials:'same-origin'});
 let data;try{data=await response.json();}catch{throw new Error('تعذّر قراءة رد الخادم. حاول مرة ثانية.');}
 if(!response.ok){if(response.status===401&&!path.endsWith('/login')){$('login-dialog').showModal();}const error=new Error(data.detail||'أدخل رمز الدخول للمتابعة.');error.status=response.status;throw error;}
 return data;
}
function addMessage(role,text){const row=document.createElement('article');row.className='message-row '+role;const body=document.createElement('div');body.className='message-body';const name=document.createElement('span');name.className='message-name';name.textContent=role==='user'?'أنت':'ACCP.AI · مساعدك';const bubble=document.createElement('div');bubble.className='bubble';bubble.textContent=text;body.append(name,bubble);if(role==='assistant'){const icon=document.createElement('span');icon.className='assistant-avatar';icon.textContent='a·';icon.setAttribute('aria-hidden','true');row.append(icon);}row.append(body);$('messages').append(row);}
function scrollDown(){requestAnimationFrame(()=>{$('scroll-region').scrollTop=state?.messages.length||busy?$('scroll-region').scrollHeight:0;});}
function render(next){state=next;const messages=next.messages||[];$('messages').replaceChildren();messages.forEach(x=>addMessage(x.role,x.text));$('welcome').hidden=messages.length>0;$('context-actions').hidden=messages.length===0;$('consent').hidden=next.stage!=='draft_consent';$('show-report').hidden=!next.report;$('report-dot').textContent=next.report?'جاهز':'غير جاهز';$('side-report').disabled=!next.report;$('explain-button').hidden=!next.question;$('finish-button').hidden=!next.question;$('stage-label').textContent=next.stage==='answering'?`السؤال ${next.answered+1} · حتى 6 أسئلة`:next.report?'التقرير جاهز':next.stage==='draft_consent'?'بانتظار اختيارك':'محادثة جديدة';$('model-label').textContent=next.last_error?'تعذّر الاستدلال':next.mode==='cloud'?'نموذج سحابي':'تجربة محلية';$('model-status').classList.toggle('cloud',next.mode==='cloud');$('model-status').classList.toggle('error',next.last_error);$('mode-note').textContent=next.mode==='cloud'?`النموذج: ${next.model}`:'تجربة محلية محدودة — اربط نموذجًا من الإعدادات لتفعيل الاستدلال.';$('message').placeholder=next.stage==='draft_consent'?'اختَر نعم أو لا، أو استخدم الأزرار أعلاه…':next.report?'أضف تفصيلًا جديدًا، أو اسأل عن التقرير…':next.question?'اكتب إجابتك، أو «ما بعرف» إذا المعلومة غير متوفرة…':'احكيلي شو صار، وشو بتحب نساعدك فيه…';scrollDown();}
function setBusy(value){busy=value;$('send').disabled=value;$('thinking').hidden=!value;$('message').disabled=value;document.querySelectorAll('.context-actions button,#accept-draft,#decline-draft,#new-chat,#mobile-new').forEach(e=>e.disabled=value);$('thinking-text').textContent=state?.mode==='cloud'?'أقرأ إفادتك وأربط التفاصيل…':'أرتّب التفاصيل محليًا…';scrollDown();}
async function send(text,action='message'){
 if(busy||!state)return;
 if(action==='message'&&!text.trim())return;
 notice('');setBusy(true);
 const signature=action+'\0'+text;
 if(!pending||pending.signature!==signature||pending.revision!==state.revision)pending={signature,request_id:crypto.randomUUID(),revision:state.revision};
 const body={text,action,revision:pending.revision,request_id:pending.request_id};
 $('welcome').hidden=true;addMessage('user',text||({summary:'لخّص ما قلته',explain:'وضّح السؤال',finish:'أنهِ المقابلة',consent_yes:'نعم، أنشئ المسودة',consent_no:'لا، يكفيني التقرير'}[action]));scrollDown();
 try{const next=await api('/api/message',{method:'POST',body:JSON.stringify(body)});render(next);pending=null;$('message').value='';$('message').style.height='auto';}
 catch(error){notice(error.message);if(error.status===409){pending=null;try{render(await api('/api/state'));}catch{}}else{render(state);}}
 finally{setBusy(false);$('message').focus();}
}
$('chat-form').addEventListener('submit',e=>{e.preventDefault();send($('message').value.trim());});
$('message').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('chat-form').requestSubmit();}});
$('message').addEventListener('input',()=>{$('message').style.height='auto';$('message').style.height=Math.min(145,$('message').scrollHeight)+'px';});
document.querySelectorAll('[data-example]').forEach(button=>button.addEventListener('click',()=>{$('message').value=examples[button.dataset.example];$('message').dispatchEvent(new Event('input'));$('message').focus();}));
document.querySelectorAll('[data-action]').forEach(button=>button.addEventListener('click',()=>send('',button.dataset.action)));
$('accept-draft').addEventListener('click',()=>send('','consent_yes'));$('decline-draft').addEventListener('click',()=>send('','consent_no'));
function settings(){if(!state)return;$('provider').value=state.base_url;$('model').value=state.model||'';$('api-key').value='';$('settings-result').textContent='';$('settings-dialog').showModal();}
$('settings').addEventListener('click',settings);$('model-status').addEventListener('click',settings);
document.querySelectorAll('.close-dialog').forEach(button=>button.addEventListener('click',()=>button.closest('dialog').close()));
$('settings-dialog').addEventListener('close',()=>{$('api-key').value='';});
$('login-dialog').addEventListener('cancel',e=>e.preventDefault());
$('settings-form').addEventListener('submit',async e=>{e.preventDefault();$('save-model').disabled=true;$('settings-result').textContent='أختبر الاتصال بالنموذج…';try{const next=await api('/api/model',{method:'POST',body:JSON.stringify({key:$('api-key').value,model:$('model').value,base_url:$('provider').value})});render(next);$('api-key').value='';$('settings-dialog').close();notice('نجح اتصال النموذج. رسائلك التالية ستستخدم الاستدلال السحابي.');}catch(error){$('settings-result').textContent=error.message;}finally{$('save-model').disabled=false;}});
$('clear-model').addEventListener('click',async()=>{try{render(await api('/api/model',{method:'DELETE',body:'{}'}));$('api-key').value='';$('settings-dialog').close();notice(state.mode==='cloud'?'حُذف مفتاح الجلسة. يُستخدم الآن النموذج المضبوط على الخادم.':'حُذف مفتاح الجلسة. التجربة المحلية مفعلة.');}catch(error){$('settings-result').textContent=error.message;}});
function reportBlock(title,text){const block=document.createElement('section');block.className='report-block';const heading=document.createElement('h3');heading.textContent=title;const p=document.createElement('p');p.textContent=text;block.append(heading,p);$('report-content').append(block);}
function showReport(){if(!state?.report){notice('رح يظهر التقرير بعد ما تكتمل المعلومات أو تنهي المقابلة.');return;}const report=state.report;$('report-content').replaceChildren();const tags=document.createElement('div');tags.className='report-tags';for(const text of report.classification){const tag=document.createElement('span');tag.textContent=text;tags.append(tag);}$('report-content').append(tags);reportBlock('ملخص إفادتك',report.summary);reportBlock('المسار المقترح',report.risk.route_label);reportBlock('مؤشر الشدة التجريبي',report.risk.risk_index===null?'غير قابل للتقدير بالمعلومات والتحليل المتاحين':`${report.risk.risk_index} / 100 — ${report.risk.risk_level}`);reportBlock('إرشادات عامة',(report.assisted_resolution_guidance||[]).join('\n')||'تحتاج التفاصيل إلى مراجعة بشرية.');reportBlock('ما ذكرته من نفي',(report.denied_facts||[]).map(f=>'«'+f.quote+'»').filter((x,i,a)=>a.indexOf(x)===i).join('\n')||'لا يوجد نفي صريح مستخرج.');reportBlock('حدود التحليل',(report.limitations||[]).join('\n'));$('draft-link').hidden=report.official_report_status!=='draft_requested_not_submitted';$('report-dialog').showModal();}
$('show-report').addEventListener('click',showReport);$('side-report').addEventListener('click',showReport);
function resetDialog(){if(state?.messages.length){$('reset-dialog').showModal();}else{$('message').value='';$('message').focus();}}
$('new-chat').addEventListener('click',resetDialog);$('mobile-new').addEventListener('click',resetDialog);
$('confirm-reset').addEventListener('click',async()=>{try{render(await api('/api/reset',{method:'POST',body:'{}'}));pending=null;$('reset-dialog').close();$('message').value='';notice('');}catch(error){notice(error.message);}});
$('login-form').addEventListener('submit',async e=>{e.preventDefault();try{render(await api('/api/login',{method:'POST',body:JSON.stringify({password:$('access-password').value})}));$('access-password').value='';$('login-dialog').close();notice('');}catch(error){$('login-error').textContent=error.message;}});
async function boot(){try{render(await api('/api/state'));}catch(error){if(error.status!==401)notice('تعذّر الاتصال بالتطبيق. تأكد أن الخادم يعمل، ثم حدّث الصفحة.');}}
boot();
