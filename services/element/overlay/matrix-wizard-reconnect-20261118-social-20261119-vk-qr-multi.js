(() => {
 'use strict';
 if(window.__matrixWizardLoaded)return;window.__matrixWizardLoaded=true;
 const LABELS={threads:'Threads',ok:'Одноклассники',vk:'VK',whatsapp:'WhatsApp',telegram:'Telegram',instagram:'Instagram',facebook:'Facebook',twitter:'X / Twitter',tiktok:'TikTok',imo:'IMO',email:'Gmail',wechat:'WeChat',linkedin:'LinkedIn',taobao:'Taobao',pinduoduo:'Pinduoduo'};
 const FLOWS={password:'Email и пароль','email-password':'Пароль приложения Gmail',qr:'QR-код',phone:'По номеру телефона',android:'Логин и пароль',instagram:'Сессия Instagram','instagram-password':'Пароль Instagram',facebook:'Сессия Facebook',messenger:'Сессия Messenger','messenger-lite':'Логин и пароль',cookies:'Сессия браузера','session-cookie':'Сессия TikTok'};
 const FIELD_NAMES={phone_number:'Номер телефона',phone:'Номер телефона',username:'Логин или email',email:'Email',password:'Пароль','2fa_password':'Пароль двухэтапной проверки',code:'Код подтверждения',phone_code:'Код подтверждения','2fa_code':'Код подтверждения',identifier:'Логин / email / телефон',cookies:'Данные сессии',pin:'PIN сообщений'};
 const twitterResumeProcess=new URL(location.href).searchParams.get('matrix_twitter_login');let twitterResumeUsed=false;
 let dialog,view,caption,message,methodSelect,accountList,retryButton,ownerButton,current=null,opening=0,lastOpener=null;
 const sdk=()=>{try{return window.mxMatrixClientPeg?.get?.()||null;}catch(_){return null;}};
 function node(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text)e.textContent=text;return e;}
 function button(label,cls,fn){const b=node('button',cls,label);b.type='button';b.addEventListener('click',fn);return b;}
 function say(text,tone=''){message.textContent=text;message.dataset.tone=tone;}
 function isCurrent(ctx){return current===ctx&&!ctx.closed&&sdk()===ctx.client&&ctx.client.getUserId()===ctx.user;}
 async function api(ctx,path,{body,signal,timeout=75000,keepalive=false,accountService=false}={}){
  if(sdk()!==ctx.client||ctx.client.getUserId()!==ctx.user)throw new Error('Сессия Matrix изменилась. Откройте подключение заново.');
  const token=ctx.client.getAccessToken?.();if(!token)throw new Error('Войдите в Matrix и повторите действие.');
  const url=new URL((accountService?'/matrix-accounts':'/matrix-onboarding/'+ctx.platform)+path,location.origin);url.searchParams.set('user_id',ctx.user);if(!accountService&&['imo','wechat','taobao','pinduoduo'].includes(ctx.platform)&&ctx.loginId)url.searchParams.set('login_id',ctx.loginId);
  const abort=new AbortController();const forward=()=>abort.abort();
  if(signal?.aborted)abort.abort();else signal?.addEventListener('abort',forward,{once:true});
  const timer=setTimeout(()=>abort.abort(),timeout);
  try{
   const response=await fetch(url,{method:body===undefined?'GET':'POST',headers:{Authorization:'Bearer '+token,Accept:'application/json',...(body===undefined?{}:{'Content-Type':'application/json'})},body:body===undefined?undefined:JSON.stringify(body),credentials:'omit',cache:'no-store',redirect:'error',signal:abort.signal,keepalive});
   const type=response.headers.get('Content-Type')||'';
   if(!type.includes('application/json'))throw new Error('Сервис подключения недоступен. Обновите Matrix и повторите.');
   const data=await response.json();
   if(!response.ok){
    const error=new Error(response.status===401?'Сессия Matrix истекла. Войдите заново.':response.status===403?'Для этой учётной записи подключение запрещено.':response.status===429?'Слишком много попыток. Подождите перед новым входом.':String(data.error||'Не удалось выполнить шаг входа.').slice(0,700));
    error.status=response.status;error.errcode=data.errcode;throw error;
   }
   return data;
  }finally{clearTimeout(timer);signal?.removeEventListener('abort',forward);}
 }
 async function cancel(ctx){
  if(!ctx)return;ctx.closed=true;ctx.abort.abort();
  if(ctx.process&&!ctx.complete){try{await api(ctx,'/v3/login/cancel/'+encodeURIComponent(ctx.process),{body:{},timeout:10000,keepalive:true});}catch(_){}try{await api(ctx,'/v1/refresh',{accountService:true,body:{clear_pending_login:{platform:ctx.platform,login_id:ctx.process}},timeout:10000,keepalive:true});}catch(_){}}
 }
 function clearSecrets(){for(const e of view?.querySelectorAll('input,textarea')||[])e.value='';}
 function close(){++opening;const old=current;current=null;clearSecrets();void cancel(old);}
 function ensureDialog(){
  if(dialog)return;
  dialog=node('dialog','mc-dialog mw-dialog');dialog.id='matrixOnboardingDialog';dialog.setAttribute('aria-labelledby','matrixOnboardingTitle');
  const header=node('header');const heading=node('div');heading.append(node('small','','ПОДКЛЮЧЕНИЕ АККАУНТА'),node('h2','',''));
  heading.lastChild.id='matrixOnboardingTitle';header.append(heading,button('×','mc-close',()=>dialog.close()));header.lastChild.setAttribute('aria-label','Закрыть');
  const body=node('div','mc-dialog-body');
  const label=node('label','mw-method');label.append(node('span','','Способ входа'));
  methodSelect=node('select');methodSelect.setAttribute('aria-label','Способ входа');label.append(methodSelect);
  methodSelect.addEventListener('change',()=>{const p=current?.platform;if(p)void begin(p,methodSelect.value,['imo','wechat','taobao','pinduoduo'].includes(p)?current.requestedLoginId:null);});
  accountList=node('div','mw-accounts');accountList.hidden=true;
  caption=node('p','mw-caption');view=node('div','mw-view');view.setAttribute('aria-live','polite');
  message=node('p','mc-status');message.setAttribute('role','status');
  body.append(label,accountList,caption,view,message);
  const footer=node('footer');
  retryButton=button('Начать заново','mc-secondary',()=>{if(current){if(current.reconnecting)void show(current.platform,lastOpener,{login_id:current.requestedLoginId,reconnect:true});else if(['imo','wechat'].includes(current.platform)||!methodSelect.options.length)void show(current.platform,lastOpener);else void begin(current.platform,methodSelect.value);}});
  ownerButton=button('Открыть бота','mc-secondary',()=>{if(current)void openBot(current);});
  footer.append(retryButton,ownerButton,button('Закрыть','mc-secondary',()=>dialog.close()));
  dialog.append(header,body,footer);document.body.append(dialog);
  dialog.addEventListener('close',()=>{close();lastOpener?.isConnected&&lastOpener.focus();});
  dialog.addEventListener('click',e=>{if(e.target!==dialog)return;const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close();});
 }
 
 async function renderShoppingChats(ctx,info){
  if(!isCurrent(ctx))return;view.replaceChildren();ctx.loginId=info.logins[0].id;
  caption.textContent=ctx.platform==='pinduoduo'?'Переписки с магазинами Pinduoduo. Новые сообщения и ваши сообщения из приложения появляются автоматически. Пока поддерживается текст до 500 символов. Служебные уведомления и чаты «拼小圈» пока недоступны.':'Переписки Taobao. Новые сообщения и ваши сообщения из приложения появляются автоматически. Сначала загружаются 20 последних чатов; остальные можно открыть здесь. Пока поддерживается текст до 500 символов.';
  if(info.logins[0].state?.state_event!=='CONNECTED')view.append(button('Повторный вход по QR-коду','mc-primary',()=>void begin(ctx.platform,'qr',ctx.loginId)));
  const search=node('input');search.type='search';search.placeholder='Найти магазин или контакт';search.setAttribute('aria-label','Найти переписку '+LABELS[ctx.platform]);
  const list=node('div','mw-shopping-chats');view.append(search,list);say('Загружаем список переписок…');
  try{
   const data=await api(ctx,'/v3/chats',{signal:ctx.abort.signal,timeout:45000});if(!isCurrent(ctx))return;
   const draw=()=>{list.replaceChildren();const query=search.value.trim().toLocaleLowerCase();
    for(const chat of (data.chats||[]).filter(c=>!query||String(c.name).toLocaleLowerCase().includes(query))){
     const open=button(String(chat.name),'mc-secondary',async()=>{open.disabled=true;say('Открываем переписку…');
      try{const room=await api(ctx,'/v3/chats/open',{body:{id:chat.id},signal:ctx.abort.signal,timeout:75000});if(!isCurrent(ctx))return;await ctx.client.joinRoom(room.room_id);location.hash='#/room/'+encodeURIComponent(room.room_id);dialog.close();}
      catch(e){if(isCurrent(ctx)){say(e.message,'error');open.disabled=false;}}
     });list.append(open);
    }
   };search.addEventListener('input',draw);draw();say('Переписок: '+(data.chats||[]).length+'. Выберите нужную.');
  }catch(e){if(isCurrent(ctx)){say(e.message,'error');view.append(button('Обновить список','mc-primary',()=>void renderShoppingChats(ctx,info)));}}
 }

 function renderAccounts(data){
  accountList.replaceChildren();
  for(const row of data.logins||[]){
   const line=node('div');line.append(node('strong','',String(row.name||row.profile?.name||row.id||'Аккаунт')));
   const status=row.state?.state_event||row.state_event;line.append(node('span','',status==='CONNECTED'?'Подключён':status==='BAD_CREDENTIALS'?'Нужен повторный вход':'Проверка соединения'));if(window.MatrixAccountTools){const key=current.platform+':'+row.id;line.append(button('Удалить подключение','mat-delete',()=>void window.MatrixAccountTools.remove(key)));}accountList.append(line);
  }
  accountList.hidden=current?.platform==='imo'||!accountList.childElementCount;
 }
 async function show(platform,opener,request=null){
  if(!LABELS[platform])return;ensureDialog();lastOpener=opener||document.activeElement;
  const turn=++opening;const old=current;current=null;clearSecrets();void cancel(old);
  retryButton.textContent='Начать заново';const c=sdk();dialog.querySelector('h2').textContent=LABELS[platform];view.replaceChildren();accountList.hidden=true;methodSelect.replaceChildren();methodSelect.disabled=true;
  caption.textContent='';methodSelect.closest('label').hidden=platform==='imo';ownerButton.hidden=['imo','threads','ok','vk'].includes(platform);say('Проверяем подключение…');if(!dialog.open)dialog.showModal();
  if(!c?.getUserId?.()){say('Сначала войдите в Matrix.','error');return;}
  const ctx={platform,client:c,user:c.getUserId(),abort:new AbortController(),process:null,closed:false,requestedLoginId:request?.login_id||null,reconnecting:request?.reconnect===true};current=ctx;
  try{
   const info=await api(ctx,'/v3/whoami',{signal:ctx.abort.signal,timeout:15000});
   if(turn!==opening||!isCurrent(ctx))return;
   ctx.bot=info.bridge_bot;if(ctx.requestedLoginId&&!info.logins?.some(a=>String(a.id)===String(ctx.requestedLoginId)))throw Error('Этот аккаунт больше не найден. Обновите список аккаунтов.');if(ctx.requestedLoginId){const target=info.logins?.find(a=>a.id===ctx.requestedLoginId);dialog.querySelector('h2').textContent=LABELS[platform]+' · '+(target?.name||ctx.requestedLoginId);}renderAccounts(info);if(['email','wechat'].includes(platform))ownerButton.hidden=true;
   let pending=null;
   try{pending=(await api(ctx,'/v1/accounts',{accountService:true,signal:ctx.abort.signal,timeout:15000})).pending_logins?.[platform]||null;}catch(_){}
   if(!isCurrent(ctx))return;
   if(pending&&!ctx.reconnecting){
    ctx.resumed=true;methodSelect.closest('label').hidden=true;methodSelect.disabled=true;
    if(platform==='twitter'&&pending.step_id==='fi.mau.twitter.login.juicebox_pin'){
     pending={...pending,instructions:'Для завершения входа X требуется PIN X Chat. Создайте PIN из четырёх цифр и сохраните его у себя.',user_input:{fields:(pending.user_input?.fields||[]).map(f=>({...f,type:'password'}))}};
    }
    const clean=new URL(location.href);clean.searchParams.delete('matrix_twitter_login');history.replaceState(history.state,'',clean.href);
    renderStep(ctx,pending);return;
   }
   const flows=(info.login_flows||[]).filter(f=>!['bot','manual','messenger-lite-android'].includes(f.id)&&(f.id!=='password'||platform==='linkedin'));
   if(!flows.length)throw new Error('Нет доступных способов входа.');
   for(const f of flows){const o=node('option','',FLOWS[f.id]||f.name||f.id);o.value=f.id;methodSelect.append(o);}
   const preferred=platform==='linkedin'&&flows.some(f=>f.id==='password')?'password':['whatsapp','telegram'].includes(platform)?'qr':platform==='instagram'?'instagram':platform==='facebook'?'facebook':flows[0].id;
   methodSelect.value=flows.some(f=>f.id===preferred)?preferred:flows[0].id;methodSelect.disabled=false;
   if(ctx.reconnecting&&ctx.requestedLoginId){
    const target=info.logins.find(a=>String(a.id)===String(ctx.requestedLoginId));
    caption.textContent='Восстановление подключения: '+String(target.name||target.profile?.name||target.id)+'. Используйте тот же аккаунт сервиса.';
    if(target.state?.state_event==='CONNECTED'){ctx.complete=true;view.append(node('div','mw-success','✓ Аккаунт уже подключён'));say('Сервис подтвердил соединение. Повторный вход сейчас не требуется.','ok');dispatchEvent(new CustomEvent('matrix-accounts-changed'));return;}
    const preferredReconnect=({imo:'new_qr',wechat:'qr',taobao:'qr',pinduoduo:'qr',facebook:'messenger-lite',instagram:'instagram-password'})[platform];
    if(preferredReconnect&&flows.some(f=>f.id===preferredReconnect))methodSelect.value=preferredReconnect;
    await begin(platform,methodSelect.value,['imo','wechat','taobao','pinduoduo','vk'].includes(platform)?ctx.requestedLoginId:null);
   }else if(['taobao','pinduoduo'].includes(platform)&&info.logins?.length){ctx.complete=true;await renderShoppingChats(ctx,info);}
   else if(platform==='wechat'&&info.logins?.length){ctx.complete=true;await renderWeChatAccounts(ctx,info,ctx.requestedLoginId);}
   else if(platform==='imo'&&info.logins?.length){ctx.complete=true;await renderIMOAccounts(ctx,info,ctx.requestedLoginId);}
   else if(platform==='vk'&&info.logins?.length){renderVKAccounts(ctx,info);}
   else if(['threads','ok'].includes(platform)&&info.logins?.length){ctx.complete=true;methodSelect.closest('label').hidden=true;caption.textContent=(info.logins[0].setup_pending||info.logins[0].state?.state_event!=='CONNECTED')?'Вход выполнен. Обмен сообщениями пока не подтверждён; проверьте состояние аккаунта.':'Аккаунт подключён. Новые входящие и ваши исходящие сообщения появляются в его вкладке слева.';view.append(node('div','mw-success',(info.logins[0].setup_pending||info.logins[0].state?.state_event!=='CONNECTED')?'Вход выполнен — сообщения ещё проверяются':'✓ Аккаунт подключён'));view.append(button('Переподключить','mc-secondary',()=>void window.MatrixAccountTools?.reconnect(platform+':'+info.logins[0].id)));say('Состояние: '+String(info.logins[0].state?.error||info.logins[0].state?.state_event||'Проверяется'));}
   else await begin(platform,methodSelect.value);
  }catch(error){if(isCurrent(ctx))say(error.name==='AbortError'?'Сервер не ответил вовремя. Нажмите «Начать заново».':error.message,'error');}
 }
 async function begin(platform,flow,loginId=null){
  const previous=current;if(!previous||previous.platform!==platform)return;
  const ctx={platform,loginId,requestedLoginId:previous.requestedLoginId,reconnecting:previous.reconnecting,client:previous.client,user:previous.user,bot:previous.bot,abort:new AbortController(),process:null,closed:false};
  current=ctx;clearSecrets();view.replaceChildren();caption.textContent='';say('Начинаем подключение…');methodSelect.disabled=true;
  await cancel(previous);
  if(!isCurrent(ctx))return;
  try{
   // Do not abort creation on close: capture its ID and cancel a late response.
   const step=await api(ctx,'/v3/login/start/'+encodeURIComponent(flow),{body:['wechat','vk'].includes(platform)&&(loginId||previous.requestedLoginId)?{login_id:loginId||previous.requestedLoginId}:{},timeout:35000});ctx.process=step.login_id;
   if(!isCurrent(ctx)){await cancel(ctx);return;}
   methodSelect.disabled=false;renderStep(ctx,step);
  }catch(error){if(isCurrent(ctx)){methodSelect.disabled=false;say(error.name==='AbortError'?'Не удалось дождаться начала входа. Проверьте подключение.':error.message,'error');}}
 }
 function stepPath(step,type){return '/v3/login/step/'+encodeURIComponent(step.login_id)+'/'+encodeURIComponent(step.step_id)+'/'+type+(step.txn_id?'?txn_id='+encodeURIComponent(step.txn_id):'');}
 async function advance(ctx,step,type,values={}){
  if(!isCurrent(ctx))return;
  try{
   const next=await api(ctx,stepPath(step,type),{body:values,signal:ctx.abort.signal});
   if(isCurrent(ctx))renderStep(ctx,next);
  }catch(error){
   if(!isCurrent(ctx))return;
   if(ctx.platform==='vk'&&[404,410].includes(error.status)){
    clearSecrets();view.replaceChildren();const expired=ctx.process;ctx.process=null;caption.textContent='Время прежней попытки VK истекло. Откройте новое окно входа; данные автоматически не отправляются.';
    view.append(button('Открыть новый вход VK','mc-primary',()=>void begin('vk','web-login')));say('Нажмите «Открыть новый вход VK».','error');
    if(expired)void api(ctx,'/v1/refresh',{accountService:true,body:{clear_pending_login:{platform:'vk',login_id:expired}},timeout:15000}).catch(()=>{});return;
   }
   if(ctx.platform==='facebook'&&error.status===404&&(error.errcode==='M_NOT_FOUND'||error.message==='Login not found')){
    clearSecrets();view.replaceChildren();caption.textContent='Предыдущая попытка входа Facebook завершилась или была отменена. Начните новую попытку.';
    say('Нужно заново начать вход Facebook.','error');methodSelect.disabled=false;
    view.append(button('Начать новый вход Facebook','mc-primary',()=>void begin('facebook',methodSelect.value||'messenger-lite')));
    const expired=ctx.process;ctx.process=null;
    if(expired)void api(ctx,'/v1/refresh',{accountService:true,body:{clear_pending_login:{platform:'facebook',login_id:expired}},timeout:15000}).catch(()=>{});
    return;
   }
   const msg=error.name==='AbortError'?'Не удалось дождаться ответа сервера.':'Ошибка входа: '+error.message;
   say(msg+' Проверьте соединение. Можно начать заново.','error');
   for(const b of view.querySelectorAll('button'))b.disabled=false;
   // Keep the same transaction ID for an explicit wait retry; never resend credentials automatically.
   if(type==='display_and_wait'&&!view.querySelector('.mw-wait-retry'))view.append(button('Проверить ещё раз','mc-secondary mw-wait-retry',e=>{e.currentTarget.remove();void advance(ctx,step,type);}));
  }
 }
 function fieldLabel(f){if(current?.platform==='facebook'&&f.id==='mfatype')return 'Способ подтверждения';if(current?.platform==='facebook'&&f.id==='otp_code')return 'Код подтверждения Facebook';if(current?.platform==='linkedin'&&f.id==='identifier')return 'Email или телефон LinkedIn';if(current?.platform==='linkedin'&&f.id==='action')return 'Способ подтверждения';if(current?.platform==='email'&&f.id==='password')return 'Пароль приложения Gmail';if(current?.platform==='email'&&f.id==='email')return 'Адрес Gmail';if(f.id==='pin'&&current?.platform==='twitter')return 'PIN X Chat';return FIELD_NAMES[f.id]||FIELD_NAMES[f.type]||f.name||f.id;}
 function renderVKBrowser(ctx,step,display){
  if(!/^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(display.data||'')||display.data.length>3000000||!Number.isInteger(display.width)||!Number.isInteger(display.height)||display.width<100||display.width>1920||display.height<100||display.height>1080){say('VK вернул некорректный экран входа. Начните вход заново.','error');return;}
  const box=node('div','mw-vk-browser'),img=node('img','mw-vk-browser-image');img.src=display.data;img.alt='Официальная страница входа VK';img.tabIndex=0;img.style.cssText='display:block;width:100%;height:auto;cursor:pointer;border:1px solid #666;border-radius:8px;';
  const send=values=>{if(!isCurrent(ctx)||box.dataset.busy)return;box.dataset.busy='1';img.style.pointerEvents='none';for(const b of box.querySelectorAll('button'))b.disabled=true;void advance(ctx,step,'user_input',values).finally(()=>{delete box.dataset.busy;img.style.pointerEvents='auto';for(const b of box.querySelectorAll('button'))b.disabled=false;});};
  img.addEventListener('click',e=>{const rect=img.getBoundingClientRect();if(rect.width&&rect.height)send({action:'click',x:Math.max(0,Math.min(display.width-1,(e.clientX-rect.left)*display.width/rect.width)),y:Math.max(0,Math.min(display.height-1,(e.clientY-rect.top)*display.height/rect.height))});});
  img.addEventListener('keydown',e=>{if(['Enter','Tab','Backspace','ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();send({action:'key',key:e.key});}});box.append(img);
  box.append(node('p','mw-help','Нажмите нужную кнопку или поле прямо на изображении. Если страница ещё загружается, нажмите «Обновить экран». Проверку безопасности выполняете вы самостоятельно.'));
  const controls=node('div','mw-actions');controls.style.cssText='display:flex;flex-wrap:wrap;gap:8px;margin:10px 0;';
  controls.append(button('Обновить экран','mc-secondary',()=>send({action:'refresh'})),button('Enter','mc-secondary',()=>send({action:'key',key:'Enter'})),button('Tab','mc-secondary',()=>send({action:'key',key:'Tab'})),button('Прокрутить вниз','mc-secondary',()=>send({action:'scroll',dy:500})),button('Прокрутить вверх','mc-secondary',()=>send({action:'scroll',dy:-500})));box.append(controls);
  const form=node('form','mw-form'),label=node('label','mw-field-label','Текст для выбранного поля VK'),input=node('input');input.type='password';input.autocomplete='off';input.maxLength=512;input.placeholder='Телефон, пароль или код — прямо в VK';label.append(input);form.append(label);const submit=node('button','mc-primary','Ввести в выбранное поле');submit.type='submit';form.append(submit);form.addEventListener('submit',e=>{e.preventDefault();if(!input.value)return;const text=input.value;input.value='';send({action:'text',text});});box.append(form);view.append(box);say('Ожидаем ваших действий на официальном экране VK. Автоматическое прохождение проверки отключено.');
 }

 function renderVKAccounts(ctx,info){
  ctx.complete=true;methodSelect.closest('label').hidden=true;caption.textContent='Каждый аккаунт VK подключается отдельно. Чтобы добавить другой аккаунт, нажмите «Добавить ещё VK» и отсканируйте новый QR-код.';
  const panel=node('section','mw-vk-accounts'),label=node('label','mw-field-label','Аккаунт VK'),select=node('select');select.setAttribute('aria-label','Выберите аккаунт VK');
  for(const row of info.logins||[]){const option=node('option','',row.name||row.id);option.value=row.id;select.append(option);}label.append(select);panel.append(label);
  const status=node('p','mw-help');const update=()=>{const row=info.logins.find(x=>x.id===select.value);status.textContent=row?.state?.state_event==='CONNECTED'?'Подключён':String(row?.state?.error||'Вход сохранён. Обмен сообщениями ещё проверяется.');};select.addEventListener('change',update);panel.append(status);update();
  panel.append(button('＋ Добавить ещё VK','mc-primary',()=>{ctx.requestedLoginId=null;ctx.reconnecting=false;void begin('vk','qr');}),button('Переподключить выбранный','mc-secondary',()=>{ctx.requestedLoginId=select.value;ctx.reconnecting=true;void begin('vk','qr',select.value);}));view.append(panel);say('Выберите аккаунт или добавьте ещё один.');
 }

 function renderStep(ctx,step){
  if(!isCurrent(ctx))return;
  if(!step?.type||step.type!=='complete'&&(!step.login_id||!step.step_id)){say('Шлюз вернул неполный шаг входа.','error');return;}
  if(step.login_id)ctx.process=step.login_id;
  if(['user_input','cookies'].includes(step.type))void api(ctx,'/v1/refresh',{accountService:true,body:{pending_login:{platform:ctx.platform,step}},timeout:15000}).catch(()=>{});
  clearSecrets();view.replaceChildren();
  caption.textContent=ctx.platform==='facebook'?facebookCaption(step):ctx.platform==='linkedin'?linkedInCaption(step):step.instructions||'';
  if(step.type==='display_and_wait'){
   const display=step.display_and_wait||{};
   if(display.type==='browser_image'&&ctx.platform==='vk'){renderVKBrowser(ctx,step,display);return;}
   if(display.type==='qr_image'&&['wechat','taobao','pinduoduo','vk'].includes(ctx.platform)){
    if(!/^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(display.data||'')||display.data.length>500000){say('Некорректный QR-код '+LABELS[ctx.platform]+'.','error');return;}
    const img=node('img','mw-qr');img.alt='QR-код '+LABELS[ctx.platform];img.src=display.data;view.append(img);say('Отсканируйте QR-код в '+LABELS[ctx.platform]+' и подтвердите вход.');
   }else if(display.type==='qr'){
    if(!display.data||typeof window.qrcode!=='function'){say('Не удалось нарисовать QR-код. Обновите страницу Matrix.','error');return;}
    try{const qr=window.qrcode(0,'M');qr.addData(display.data);qr.make();const img=node('img','mw-qr');img.alt='QR-код для подключения '+LABELS[ctx.platform];img.src=qr.createDataURL(5,20);view.append(img);}catch(_){say('Ошибка формирования QR-кода. Выберите вход по номеру.','error');return;}
    caption.textContent=ctx.platform==='imo'?'На телефоне: IMO → «+» → «Сканировать QR-код». Подтвердите вход.':ctx.platform==='telegram'?'На телефоне: Telegram → Настройки → Устройства → Подключить устройство. Отсканируйте QR-код.':'На телефоне: WhatsApp или WhatsApp Business → Связанные устройства → Привязать устройство. Отсканируйте QR-код.';
    say('Ожидаем сканирование. QR-код обновляется автоматически.');
   }else if(display.type==='code'||display.type==='emoji'){
    view.append(node('div','mw-pairing-code',String(display.data||'')));say('Подтвердите подключение на телефоне.');
   }else say('Ожидаем подтверждение…');
   void advance(ctx,step,'display_and_wait');return;
  }
  if(step.type==='cookies'&&ctx.platform==='linkedin'){renderLinkedInCookies(ctx,step);return;}
  if(step.type==='user_input'||step.type==='cookies'){
   const fields=step.type==='cookies'?(step.cookies?.fields||[]).map(f=>({id:f.id||f.name,name:f.id||f.name,type:'token',optional:!f.required})):step.user_input?.fields||[];
   if(!fields.length){say('Шлюз не передал поля формы.','error');return;}
   const form=node('form','mw-form');form.autocomplete='off';
   if(step.type==='cookies'){
    const text=node('p','mw-help','Войдите на сайте сервиса в браузере, затем вставьте запрошенные данные сессии. Они отправляются только вашему шлюзу подключения.');form.append(text);
    const raw=step.cookies?.url;
    try{const u=new URL(raw);const domains={instagram:['instagram.com'],facebook:['facebook.com','messenger.com'],twitter:['x.com','twitter.com'],tiktok:['tiktok.com']};if(u.protocol==='https:'&&(domains[ctx.platform]||[]).some(d=>u.hostname===d||u.hostname.endsWith('.'+d))){const a=node('a','mc-site','Открыть сайт для входа ↗');a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';form.append(a);}}catch(_){}
   }
   for(const f of fields){
    if(!f.id)continue;const label=node('label','mw-field');label.append(node('span','',fieldLabel(f)));
    const input=f.type==='select'?node('select'):node('input');input.name=f.id;if(['linkedin','facebook'].includes(ctx.platform))input.setAttribute('aria-label',fieldLabel(f));
    if(f.type==='select'){
     if(ctx.platform==='facebook'&&f.id==='mfatype'){const placeholder=node('option','','Выберите способ подтверждения');placeholder.value='';placeholder.disabled=true;placeholder.selected=true;input.append(placeholder);}
     for(const value of f.options||[]){const option=node('option','',ctx.platform==='facebook'?facebookOption(value):ctx.platform==='linkedin'?linkedInOption(value):String(value));option.value=value;input.append(option);}
    }
    else {input.type=['password','token'].includes(f.type)?'password':f.type==='phone_number'?'tel':f.type==='email'?'email':'text';input.autocomplete=['password','token'].includes(f.type)?'new-password':f.type==='2fa_code'?'one-time-code':'off';if(f.type==='2fa_code')input.inputMode='numeric';}
    input.required=!f.optional;if(f.pattern)input.pattern=f.pattern;if(f.min_length)input.minLength=Math.min(65536,f.min_length);if(f.id==='pin')input.inputMode='numeric';if(f.max_length)input.maxLength=Math.min(65536,f.max_length);
    if(f.default_value&&input.type!=='password')input.value=f.default_value;
    if(f.type==='phone_number')input.placeholder='+993…';label.append(input);
    if(f.description)label.append(node('small','',f.description));form.append(label);
   }
   const submit=node('button','mc-primary',ctx.platform==='twitter'&&step.step_id==='fi.mau.twitter.login.juicebox_pin'?'Создать PIN и подключить':'Продолжить');submit.type='submit';form.append(submit);
   if(ctx.platform==='email'){const help=node('a','mc-site','Создать пароль приложения в Google ↗');help.href='https://myaccount.google.com/apppasswords';help.target='_blank';help.rel='noopener noreferrer';form.append(help,node('p','mw-help','Для пароля приложения нужна двухэтапная проверка Google. Настройки ящика выбираются автоматически. Можно подключить несколько Gmail по очереди.'));submit.textContent='Подключить Gmail';}
   form.addEventListener('submit',event=>{event.preventDefault();if(!form.reportValidity()||submit.disabled)return;const values=Object.fromEntries(new FormData(form));submit.disabled=true;clearSecrets();say('Проверяем введённые данные…');void advance(ctx,step,step.type,values);});
   view.append(form);if(ctx.platform==='facebook'&&/invalid|incorrect|wrong|rejected|expired/i.test(String(step.instructions||'')))say('Facebook отклонил введённые данные. Проверьте сообщение выше.','error');else say('Заполните форму, чтобы продолжить.');form.querySelector('input,select')?.focus();return;
  }
  if(step.type==='complete'){
   ctx.complete=true;void syncAccountSidebar(ctx,step.complete);dispatchEvent(new CustomEvent('matrix-accounts-changed')); view.append(node('div','mw-success',step.complete?.transport_pending?'✓ Вход подтверждён':'✓ Аккаунт подключён'));caption.textContent=step.complete?.transport_pending?'Вход выполнен. Обмен сообщениями ещё настраивается и проверяется; аккаунт пока не считается подключённым.':ctx.platform==='email'?'Gmail подключён. Новые входящие письма и ответы появятся в разделе этого аккаунта. Старый архив автоматически не загружается.':'Вход выполнен. Чаты будут появляться в Matrix по мере синхронизации.';say('Можно закрыть окно и продолжить работу.','ok');
   if(ctx.platform==='wechat'){void api(ctx,'/v3/whoami',{signal:ctx.abort.signal,timeout:15000}).then(info=>{if(isCurrent(ctx))return renderWeChatAccounts(ctx,info,step.complete?.id);}).catch(error=>{if(isCurrent(ctx))say(error.message,'error');});}
   if(ctx.platform==='imo'){ctx.loginId=step.complete?.id||step.complete?.user_login_id;void api(ctx,'/v3/whoami',{signal:ctx.abort.signal,timeout:15000}).then(info=>{if(isCurrent(ctx))return renderIMOAccounts(ctx,info,ctx.loginId);}).catch(error=>{if(isCurrent(ctx))say(error.message,'error');});}
   void api(ctx,'/v3/whoami',{signal:ctx.abort.signal,timeout:15000}).then(data=>{if(isCurrent(ctx))renderAccounts(data);}).catch(()=>{});return;
  }
  if(step.type==='webauthn')say('Сервис запросил подтверждение ключом доступа. Этот шаг нужно выполнить через приложение подключения шлюза или выбрать другой способ входа.','error');
  else say('Этот способ требует встроенного браузера сервиса. Выберите другой способ входа или используйте личный диалог с ботом.','error');
 }
 async function syncAccountSidebar(ctx,complete={}){
  try{
   await api(ctx,'/v1/refresh',{accountService:true,body:{clear_pending_login:{platform:ctx.platform,login_id:ctx.process}},timeout:15000});
   const id=complete?.id||complete?.user_login_id;
   for(let attempt=0;attempt<12;attempt++){
    if(!isCurrent(ctx))return;
    const data=await api(ctx,'/v1/accounts',{accountService:true,signal:ctx.abort.signal,timeout:15000});
    const account=(data.accounts||[]).find(a=>a.platform===ctx.platform&&(!id||String(a.login_id)===String(id))&&a.space_room&&!a.space_error);
    if(account){await ctx.client.joinRoom(account.space_room);if(isCurrent(ctx))say('Аккаунт добавлен в панель слева.','ok');dispatchEvent(new CustomEvent('matrix-accounts-changed'));return;}
    await new Promise(resolve=>setTimeout(resolve,1500));
   }
   if(isCurrent(ctx))say('Вход выполнен. Панель аккаунтов обновляется автоматически.','ok');
  }catch(_){if(isCurrent(ctx))say('Вход выполнен. Обновите список аккаунтов кнопкой «Проверить» слева.','ok');}
 }
 function facebookOption(value){return ({Email:'Электронная почта','Text message':'SMS','Re-enter password':'Повторно ввести пароль'})[value]||String(value);}
 function facebookCaption(step){
  const id=String(step.step_id||''),text=String(step.instructions||'');
  if(id.endsWith('.email_password'))return /invalid|incorrect|wrong|rejected/i.test(text)?'Facebook не принял логин или пароль. Проверьте данные либо выберите способ «Сессия Facebook».':'Введите email или телефон Facebook и пароль. Данные отправятся вашему мосту на этом сервере.';
  if(id.endsWith('.mfa_type'))return 'Facebook запросил подтверждение входа. Выберите, куда получить код, затем нажмите «Продолжить».';
  if(id.endsWith('.otp_code')&&/invalid|incorrect|wrong|expired/i.test(text))return 'Facebook не принял код подтверждения. Введите последний код из письма или SMS. Если срок действия кода истёк, начните новый вход.';
  if(id.endsWith('.otp_code'))return /email/i.test(text)?'Facebook отправил код на вашу электронную почту. Введите последний код из письма.':/phone|text message|sms/i.test(text)?'Facebook отправил код на ваш телефон. Введите последний код.':'Введите код подтверждения Facebook, полученный выбранным способом.';
  return text;
 }
 function linkedInOption(value){return ({'Approve in LinkedIn app':'Подтвердить в приложении LinkedIn','Try another way':'Другой способ','Continue in browser':'Продолжить через браузер','Try email and password again':'Повторить вход по email'})[value]||String(value);}
 function linkedInCaption(step){
  const id=step.step_id||'',text=String(step.instructions||'');
  if(id.endsWith('.credentials'))return /rejected|rejected|did not accept/i.test(text)?'LinkedIn не подтвердил вход. Проверьте email и пароль или выберите вход через браузер.':'Введите email или телефон LinkedIn и пароль. Данные отправятся вашему мосту на этом сервере.';
  if(id.endsWith('.email_code'))return 'LinkedIn отправил код на вашу почту. Введите последний шестизначный код.';
  if(id.endsWith('.sms_code'))return 'Введите шестизначный код из SMS LinkedIn.';
  if(id.endsWith('.totp_code'))return 'Введите текущий шестизначный код из приложения аутентификации.';
  if(id.endsWith('.app_method'))return 'LinkedIn запросил подтверждение. Подтвердите вход в приложении на телефоне или выберите другой способ.';
  if(id.endsWith('.app_approval'))return 'Откройте LinkedIn на телефоне и подтвердите вход. Ожидаем ответ сервиса…';
  if(id.endsWith('.browser_fallback'))return 'LinkedIn запросил дополнительную проверку. Можно завершить вход через браузер или повторить ввод email и пароля.';
  if(step.type==='cookies')return 'Войдите в LinkedIn в приватном окне Chrome или Edge. В инструментах разработчика: «Сеть» → Fetch/XHR → запрос graphql → Copy as cURL (bash). Закройте приватное окно и вставьте запрос ниже.';
  return text;
 }
 function renderLinkedInCookies(ctx,step){
  const form=node('form','mw-form');form.autocomplete='off';
  const link=node('a','mc-site','Открыть LinkedIn ↗');link.href='https://www.linkedin.com/login';link.target='_blank';link.rel='noopener noreferrer';form.append(link);
  const label=node('label','mw-field');label.append(node('span','','Запрос cURL из LinkedIn'));
  const input=node('textarea');input.rows=7;input.required=true;input.maxLength=524288;input.spellcheck=false;input.autocomplete='off';input.setAttribute('aria-label','Запрос cURL из LinkedIn');label.append(input);form.append(label);
  form.append(node('p','mw-help','Запрос содержит данные входа. Вставляйте его только в эту форму Matrix; в переписку с помощником отправлять его не нужно.'));
  const submit=node('button','mc-primary','Подключить LinkedIn');submit.type='submit';form.append(submit);
  form.addEventListener('submit',event=>{event.preventDefault();if(!form.reportValidity()||submit.disabled)return;let values;try{values=window.MatrixLinkedInSession.parse(input.value,step.cookies?.fields||[]);}catch(error){say(error.message,'error');return;}submit.disabled=true;clearSecrets();say('Подключаем LinkedIn…');void advance(ctx,step,'cookies',values);});
  view.append(form);say('Подтверждения LinkedIn выполняйте самостоятельно.');input.focus();
 }
 async function renderWeChatAccounts(ctx,info,selected){
  if(!isCurrent(ctx))return;view.replaceChildren();accountList.hidden=true;methodSelect.closest('label').hidden=true;
  const rows=info.logins||[];ctx.loginId=rows.some(a=>a.id===selected)?selected:rows[0]?.id;
  const panel=node('section','mw-imo-accounts'),label=node('label','mw-field'),select=node('select');
  label.append(node('span','','Аккаунт WeChat'));select.setAttribute('aria-label','Аккаунт WeChat');
  for(const a of rows){const o=node('option','',String(a.name||a.id));o.value=a.id;select.append(o);}select.value=ctx.loginId||'';label.append(select);panel.append(label);
  const add=button('＋ Добавить ещё WeChat','mc-primary',()=>{ctx.requestedLoginId=null;ctx.loginId=null;void begin('wechat','qr');});add.disabled=rows.length>=(info.max_accounts||40);panel.append(add);
  panel.append(button('Переподключить выбранный','mc-secondary',()=>{ctx.requestedLoginId=select.value;void begin('wechat','qr');}));view.append(panel);
  caption.textContent='Доступные переписки, группы и контакты. Новые сообщения появятся автоматически. Архив ограничен историей, доступной клиенту WeChat.';
  retryButton.textContent='Обновить список';say('Выберите переписку или найдите её по имени.','ok');
  let sequence=0;
  async function choose(){
   const turn=++sequence;ctx.loginId=select.value;view.querySelector('.mw-imo-chats')?.remove();
   const area=node('section','mw-imo-chats'),search=node('input','mw-imo-search'),hint=node('p','mw-help'),list=node('div','mw-imo-list');
   search.type='search';search.placeholder='Найти чат, группу или контакт WeChat';search.setAttribute('aria-label',search.placeholder);area.append(search,hint,list);view.append(area);
   let loadSequence=0,timer;
   async function load(){
    const n=++loadSequence,account=ctx.loginId;hint.textContent='Загружаем переписки и контакты…';
    try{
     const data=await api(ctx,'/v3/chats?search='+encodeURIComponent(search.value.trim()),{signal:ctx.abort.signal,timeout:65000});
     if(!isCurrent(ctx)||turn!==sequence||n!==loadSequence||account!==ctx.loginId)return;
     list.replaceChildren();hint.textContent=data.total>200?'Всего '+data.total+'. Показаны первые 200; используйте поиск.':data.total?'Всего: '+data.total:'Переписки пока не найдены. Новые сообщения появятся автоматически.';
     for(const chat of data.chats||[]){
      const row=node('div','mw-imo-chat');row.append(node('span','',chat.name));
      const open=button(chat.room_id?'Открыть':'Добавить','mc-secondary',async()=>{
       if(!isCurrent(ctx)||account!==ctx.loginId)return;open.disabled=true;say('Открываем переписку…');
       try{const p=await api(ctx,'/v3/chats/open',{body:{id:chat.id},signal:ctx.abort.signal,timeout:70000});if(!isCurrent(ctx)||account!==ctx.loginId)return;await ctx.client.joinRoom(p.room_id);dialog.close();location.hash='#/room/'+encodeURIComponent(p.room_id);}
       catch(e){if(isCurrent(ctx)){say(e.message,'error');open.disabled=false;}}
      });row.append(open);list.append(row);
     }
    }catch(e){if(isCurrent(ctx)&&turn===sequence)hint.textContent=e.message;}
   }
   search.addEventListener('input',()=>{++loadSequence;clearTimeout(timer);timer=setTimeout(load,300);});await load();
  }
  select.addEventListener('change',()=>void choose());await choose();
 }
 async function renderIMOAccounts(ctx,info,selected){
  if(!isCurrent(ctx))return;view.replaceChildren();accountList.hidden=true;
  const rows=info.logins||[];ctx.loginId=rows.some(a=>a.id===selected)?selected:rows[0]?.id;
  const panel=node('section','mw-imo-accounts');
  const label=node('label','mw-field');label.append(node('span','','Аккаунт IMO'));
  const select=node('select');select.setAttribute('aria-label','Аккаунт IMO');
  for(const account of rows){const o=node('option','',String(account.name||account.id)+' · '+(account.phone_suffix?'…'+account.phone_suffix:account.id));o.value=account.id;select.append(o);}
  select.value=ctx.loginId||'';label.append(select);panel.append(label);
  panel.append(node('p','mw-help','Подключено '+rows.length+' из '+(info.max_accounts||40)+'. У каждого аккаунта свои контакты, группы и вкладка слева.'));
  const add=button('＋ Добавить ещё IMO','mc-primary',()=>{ctx.loginId=null;void begin('imo','new_qr');});add.disabled=rows.length>=(info.max_accounts||40);panel.append(add);panel.append(button('Переподключить выбранный','mc-secondary',()=>void window.MatrixAccountTools?.reconnect('imo:'+select.value)));view.append(panel);
  retryButton.textContent='Список аккаунтов';retryButton.onclick=null;
  caption.textContent='Выберите аккаунт или подключите новый по отдельному QR-коду.';say('','ok');
  let sequence=0;
  async function choose(){
   const turn=++sequence;ctx.loginId=select.value;view.querySelector('.mw-imo-chats')?.remove();
   const account=rows.find(a=>a.id===ctx.loginId);
   if(account?.state?.state_event!=='CONNECTED'){
    const area=node('section','mw-imo-chats');area.append(node('p','mw-help','Соединение этого аккаунта ещё не готово.'),button('Проверить соединение','mc-secondary',()=>void show('imo',lastOpener)),button('Повторный вход','mc-secondary',()=>void begin('imo','new_qr',ctx.loginId)));view.append(area);return;
   }
   await renderIMOChats(ctx,turn,()=>turn===sequence);
  }
  select.addEventListener('change',()=>void choose());await choose();
 }
 async function renderIMOChats(ctx,turn=0,stillSelected=()=>true){
  caption.textContent='Фото, видео и файлы можно отправлять и открывать в Matrix. Вложения хранятся два месяца; текстовая история сохраняется.';
  const loginId=ctx.loginId;const area=node('section','mw-imo-chats');
  const search=node('input','mw-imo-search');search.type='search';search.placeholder='Найти переписку или контакт';search.setAttribute('aria-label','Найти переписку IMO');
  const list=node('div','mw-imo-list');const hint=node('p','mw-help');area.append(search,hint,list);view.append(area);
  let sequence=0,timer;
  async function load(){
   const turn=++sequence;if(!isCurrent(ctx))return;
   hint.textContent='Загружаем переписки…';
   try{
    const data=await api(ctx,'/v3/chats?search='+encodeURIComponent(search.value.trim()),{signal:ctx.abort.signal,timeout:20000});
    if(!isCurrent(ctx)||turn!==sequence||!area.isConnected||!stillSelected()||ctx.loginId!==loginId)return;
    list.replaceChildren();hint.textContent=data.total>100?'Показаны первые 100. Уточните поиск.':data.chats?.length?'':'Переписки не найдены.';
    for(const chat of data.chats||[]){
     const row=node('div','mw-imo-chat');row.append(node('span','',chat.name));
     const open=button(chat.room_id?'Открыть':'Добавить','mc-secondary',async()=>{
      if(open.disabled||!isCurrent(ctx)||ctx.loginId!==loginId)return;open.disabled=true;say('Открываем переписку…');
      try{
       const portal=await api(ctx,'/v3/chats/open',{body:{id:chat.id},signal:ctx.abort.signal,timeout:30000});
       if(!isCurrent(ctx)||ctx.loginId!==loginId)return;
       await ctx.client.joinRoom(portal.room_id);
       if(isCurrent(ctx)){dialog.close();location.hash='#/room/'+encodeURIComponent(portal.room_id);}
      }catch(error){if(isCurrent(ctx)){say(error.message,'error');open.disabled=false;}}
     });row.append(open);list.append(row);
    }
   }catch(error){if(isCurrent(ctx)&&turn===sequence)hint.textContent=error.message;}
  }
  search.addEventListener('input',()=>{++sequence;clearTimeout(timer);timer=setTimeout(load,250);});
  await load();
 }
 async function openBot(ctx){
  if(!isCurrent(ctx))return;const bot=ctx.bot||'@'+(ctx.platform==='facebook'?'facebook':ctx.platform)+'bot:__SERVER_NAME__';
  try{
   const room=ctx.client.getRooms().find(r=>r.getMyMembership?.()==='join'&&(r.getJoinedMembers?.()||[]).some(m=>m.userId===bot)&&(r.getJoinedMembers?.()||[]).every(m=>m.userId===bot||m.userId===ctx.user));
   const id=room?.roomId||(await ctx.client.createRoom({preset:'private_chat',invite:[bot],is_direct:true})).room_id;
   if(isCurrent(ctx)){dialog.close();location.hash='#/room/'+encodeURIComponent(id);}
  }catch(_){if(isCurrent(ctx))say('Не удалось открыть бота. Попробуйте ещё раз.','error');}
 }
 document.addEventListener('click',event=>{
  const target=event.target.closest('button');if(!target)return;
  let platform='';if(target.classList.contains('mc-shortcut'))platform=Object.keys(LABELS).find(k=>target.classList.contains('mc-'+k));
  else if(target.parentElement?.classList.contains('mc-bot-actions'))platform=target.parentElement.dataset.platform;
  if(!platform)return;event.preventDefault();event.stopImmediatePropagation();void show(platform,target.closest('.mc-account-menu')?.parentElement.querySelector('.mc-account-trigger')||target);
 },true);
 setInterval(()=>{if(current&&(!sdk()?.getUserId?.()||sdk()!==current.client)){dialog?.open&&dialog.close();}},1500);
 if(twitterResumeProcess&&/^[a-zA-Z0-9_-]{12,100}$/.test(twitterResumeProcess)){
  const resumeTimer=setInterval(()=>{if(sdk()?.getUserId?.()){clearInterval(resumeTimer);void show('twitter');}},400);
 }
 addEventListener('matrix-resume-login',event=>{const platform=event.detail?.platform;if(LABELS[platform]){void show(platform,undefined,event.detail);}});
 if(new URL(location.href).searchParams.get('matrix_connect')==='vk'){
  let attempts=0;const directTimer=setInterval(()=>{if(sdk()?.getUserId?.()){clearInterval(directTimer);const clean=new URL(location.href);clean.searchParams.delete('matrix_connect');history.replaceState(null,'',clean);void show('vk');}else if(++attempts>150)clearInterval(directTimer);},400);
 }
 addEventListener('pagehide',close);
})();
