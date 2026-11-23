(()=>{
 'use strict';if(window.__matrixContactSearchLoaded)return;window.__matrixContactSearchLoaded=true;
 const networks={telegram:'Telegram',whatsapp:'WhatsApp',imo:'IMO'};let network,networkNote,help,queryLabel;let dialog,account,input,form,status,results,submit,closeButton,current=null,opener=null,lastLogin={},lastNetwork='telegram',scheduled=false;
 const limits=new Map();
 const sdk=()=>{try{return window.mxMatrixClientPeg?.get?.()||null}catch{return null}};
 const node=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text)e.textContent=text;return e;};
 function telegramIdentifier(raw){
  let value=String(raw||'').trim();
  if(!value||value.length>160)throw Error('Введите номер телефона с кодом страны или @username Telegram.');
  if(/^(?:https?:\/\/)?(?:t\.me|telegram\.me)\//i.test(value)){
   const url=new URL(/^https?:/i.test(value)?value:'https://'+value);
   if(url.username||url.password||url.search||url.hash||url.port||!['https:','http:'].includes(url.protocol)||!['t.me','telegram.me'].includes(url.hostname.toLowerCase())||!/^\/[A-Za-z][A-Za-z0-9_]{3,30}[A-Za-z0-9]\/?$/.test(url.pathname))throw Error('Нужна ссылка на личный профиль: t.me/username.');
   value='@'+url.pathname.replace(/^\/|\/$/g,'');
  }
  if(/^[+\d\s().-]+$/.test(value)){
   value=value.replace(/[\s().-]/g,'');if(value.startsWith('00'))value='+'+value.slice(2);
   if(!value.startsWith('+'))throw Error('Для поиска по телефону укажите + и код страны, например +993…');
   if(!/^\+[1-9]\d{6,14}$/.test(value))throw Error('Проверьте номер: +, код страны и от 7 до 15 цифр.');
   return value;
  }
  value=value.replace(/^@/,'');
  if(!/^[A-Za-z][A-Za-z0-9_]{3,30}[A-Za-z0-9]$/.test(value)||value.includes('__'))throw Error('Введите @username Telegram: от 5 до 32 латинских букв, цифр или подчёркиваний.');
  return '@'+value;
 }
 function normalize(raw,service){
  if(service==='telegram')return telegramIdentifier(raw);
  let value=String(raw||'').trim();if(value.length>160)throw Error('Проверьте номер или идентификатор.');
  if(service==='whatsapp'&&/^(?:https?:\/\/)?wa\.me\//i.test(value)){
   const url=new URL(/^https?:/i.test(value)?value:'https://'+value);
   if(url.hostname!=='wa.me'||url.username||url.password||url.port||url.search||url.hash||!/^\/[1-9]\d{6,14}\/?$/.test(url.pathname))throw Error('Нужна ссылка wa.me/номер без дополнительных параметров.');
   value='+'+url.pathname.replace(/^\/|\/$/g,'');
  }
  if(service==='imo'&&/^@[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(value))return value;
  if(!/^[+\d\s().-]+$/.test(value))throw Error(service==='imo'?'Введите международный номер с + или IMO ID с @.':'WhatsApp: введите номер телефона с + и кодом страны.');
  value=value.replace(/[\s().-]/g,'');if(value.startsWith('00'))value='+'+value.slice(2);
  if(!/^\+[1-9]\d{6,14}$/.test(value))throw Error('Введите +, код страны и номер: от 7 до 15 цифр.');return value;
 }
 function contextLabel(){const selected=document.querySelector('.mx_SpacePanel [aria-selected="true"]');return [selected?.getAttribute('aria-label'),selected?.getAttribute('title'),selected?.querySelector('button,[role="button"]')?.getAttribute('aria-label'),selected?.textContent,document.querySelector('[data-testid="room-list-header"] h1')?.textContent].filter(Boolean).join(' ');}
 function updateNetwork(){
  const service=network.value,isTG=service==='telegram',isIMO=service==='imo';
  networkNote.textContent=networks[service]+' · '+(isTG?'поиск по номеру или @username':isIMO?'поиск по номеру или IMO ID':'поиск по номеру телефона');
  queryLabel.firstChild.textContent=isTG?'Номер телефона или @username':isIMO?'Номер телефона или IMO ID':'Номер телефона';
  input.placeholder=isTG?'+993… или @username':isIMO?'+993… или @IMO_ID':'+993…';
  input.setAttribute('aria-label',isTG?'Номер телефона или username Telegram':isIMO?'Номер телефона или IMO ID':'Номер телефона WhatsApp');
  account.setAttribute('aria-label','Аккаунт '+networks[service]+' для поиска');
  help.textContent=isTG?'Можно вставить t.me/username. Если поиск по номеру закрыт в Telegram, используйте @username.':isIMO?'Для IMO ID добавьте @ перед идентификатором. Поиск по телефону использует добавление контакта IMO без автоматического запроса дружбы.':'Можно вставить ссылку wa.me/номер. Введите номер с международным кодом страны.';
  help.textContent+=' Сообщение отправляется только вами после открытия чата.';
 }
 const active=ctx=>current===ctx&&!ctx.closed&&dialog.open&&sdk()===ctx.client&&ctx.client.getUserId()===ctx.user;
 function say(text,tone=''){status.textContent=text;status.dataset.tone=tone;}
 function invalidate(){if(!current)return;current.turn++;current.abort?.abort();current.abort=null;results.replaceChildren();submit.disabled=!current.ready;}
 async function request(ctx,path,{login='',body,signal,timeout=45000}={}){
  if(!active(ctx))throw Error('Сессия Matrix изменилась. Откройте поиск заново.');
  const token=ctx.client.getAccessToken?.();if(!token)throw Error('Войдите в Matrix и повторите поиск.');
  const url=new URL('/matrix-onboarding/'+ctx.network+'/v3'+path,location.origin);url.searchParams.set('user_id',ctx.user);if(login)url.searchParams.set('login_id',login);
  const abort=new AbortController(),cancel=()=>abort.abort();if(signal?.aborted)abort.abort();else signal?.addEventListener('abort',cancel,{once:true});
  const timer=setTimeout(()=>abort.abort(),timeout);
  try{
   const response=await fetch(url,{method:body===undefined?'GET':'POST',headers:{Authorization:'Bearer '+token,Accept:'application/json',...(body===undefined?{}:{'Content-Type':'application/json'})},body:body===undefined?undefined:JSON.stringify(body),credentials:'omit',cache:'no-store',redirect:'error',signal:abort.signal});
   if(!response.headers.get('content-type')?.includes('application/json'))throw Error('Поиск '+networks[ctx.network]+' временно недоступен. Повторите позже.');
   const data=await response.json();
   if(!response.ok){
    const error=Error();error.code=data.errcode;error.status=response.status;error.retry=Number(data.retry_after_ms)||0;
    if(error.retry||data.errcode==='M_LIMIT_EXCEEDED'){const wait=Math.max(4,Math.ceil(error.retry/1000)||30);error.message=networks[ctx.network]+' ограничил поиск. Повторите через '+wait+' сек.';if(login)limits.set(ctx.network+':'+login,Date.now()+wait*1000);}
    else if(response.status===404)error.message=ctx.network==='whatsapp'?'Этот номер не найден в WhatsApp. Проверьте номер и код страны.':'Контакт не найден. Проверьте номер или идентификатор. Поиск может быть закрыт настройками приватности '+networks[ctx.network]+'.';
    else if([401,403].includes(response.status))error.message='Нет доступа к выбранному аккаунту. Проверьте вход в Matrix и подключение '+networks[ctx.network]+'.';
    else if(response.status===409)error.message='Выбранный аккаунт отключён. Переподключите его в настройках аккаунта.';
    else if(response.status===501)error.message='Этот аккаунт не поддерживает выбранный поиск.';
    else error.message=networks[ctx.network]+' не ответил на запрос. Проверьте подключение выбранного аккаунта и повторите позже.';
    throw error;
   }
   return data;
  }finally{clearTimeout(timer);signal?.removeEventListener('abort',cancel);}
 }
 function ensureDialog(){
  if(dialog)return;
  dialog=node('dialog','mts-dialog');dialog.setAttribute('aria-labelledby','mts-title');
  const header=node('div','mts-header');header.append(node('h2','','Добавить клиента'));header.querySelector('h2').id='mts-title';closeButton=node('button','mts-close','×');closeButton.type='button';closeButton.setAttribute('aria-label','Закрыть поиск клиента');closeButton.addEventListener('click',()=>dialog.close());header.append(closeButton);dialog.append(header);
  form=node('form','mts-form');form.autocomplete='off';
  const networkLabel=node('label','mts-field','Мессенджер');network=node('select');network.setAttribute('aria-label','Мессенджер для поиска');for(const [id,name] of Object.entries(networks)){const option=node('option','',name);option.value=id;network.append(option)}networkLabel.append(network);form.append(networkLabel);
  const label=node('label','mts-field','Искать с аккаунта');account=node('select');account.setAttribute('aria-label','Аккаунт Telegram для поиска');label.append(account);form.append(label);
  queryLabel=node('label','mts-field','Номер телефона или @username');input=node('input');input.type='text';input.name='telegram-contact';input.maxLength=160;input.placeholder='+993… или @username';input.autocomplete='off';input.spellcheck=false;input.setAttribute('aria-label','Номер телефона или username Telegram');queryLabel.append(input);form.append(queryLabel);
  submit=node('button','mts-primary','Найти');submit.type='submit';form.append(submit);networkNote=node('p','mts-network');dialog.append(networkNote);dialog.append(form);
  status=node('p','mts-status');status.setAttribute('role','status');status.setAttribute('aria-live','polite');dialog.append(status);results=node('div','mts-results');dialog.append(results);
  help=node('p','mts-help');dialog.append(help);
  network.addEventListener('change',()=>{input.value='';lastNetwork=network.value;void loadAccounts();});
  account.addEventListener('change',()=>{invalidate();lastLogin[network.value]=account.value;say('Введите номер или идентификатор и нажмите «Найти».');});
  input.addEventListener('input',()=>{invalidate();if(current?.ready)say('Нажмите «Найти», чтобы проверить контакт.');});
  form.addEventListener('submit',event=>{event.preventDefault();void search();});
  dialog.addEventListener('close',()=>{if(current){current.closed=true;current.abort?.abort();current=null;}input.value='';results.replaceChildren();if(opener?.isConnected)opener.focus();});
  document.body.append(dialog);
 }
 async function open(button,service){
  ensureDialog();if(dialog.open){input.focus();return;}
  opener=button||document.activeElement;const label=contextLabel();
  network.value=service&&networks[service]?service:/WhatsApp/i.test(label)?'whatsapp':/\bIMO\b/i.test(label)?'imo':/Telegram/i.test(label)?'telegram':lastNetwork;
  input.value='';results.replaceChildren();dialog.showModal();await loadAccounts();
 }
 async function loadAccounts(){
  if(current){current.closed=true;current.abort?.abort();}
  const client=sdk();account.disabled=true;input.disabled=false;account.replaceChildren();results.replaceChildren();submit.disabled=true;updateNetwork();say('Проверяем аккаунты '+networks[network.value]+'…');
  const ctx={client,user:client?.getUserId?.(),network:network.value,closed:false,ready:false,turn:0,logins:[],abort:new AbortController()};current=ctx;
  if(!ctx.user){say('Сначала войдите в Matrix.','error');return;}
  try{
   const info=await request(ctx,'/whoami',{signal:ctx.abort.signal,timeout:20000});if(!active(ctx))return;
   ctx.logins=(info.logins||[]).filter(x=>typeof x.id==='string'&&/^\d+$/.test(x.id));
   if(!ctx.logins.length){say('Подключите '+networks[ctx.network]+' через «Подключить новый аккаунт».','error');return;}
   let roomId='';try{roomId=decodeURIComponent(location.hash.match(/^#\/room\/([^/?]+)/)?.[1]||'')}catch{}
   const spaces=new Set(roomId?[roomId]:[]);try{for(const event of client.getRoom?.(roomId)?.currentState?.getStateEvents('m.space.parent')||[]){const key=event.getStateKey?.();if(key)spaces.add(key)}}catch{}
   const label=contextLabel();
   for(const login of ctx.logins){const name=login.profile?.name||login.name||networks[ctx.network]+' · …'+login.id.slice(-4),phone=login.profile?.phone||'';const option=node('option','',name+(phone&&phone!==name?' · '+phone:login.phone_suffix?' · …'+login.phone_suffix:''));option.value=login.id;if(login.state?.state_event&&login.state.state_event!=='CONNECTED')option.textContent+=' · проверьте связь';account.append(option);}
   const match=ctx.logins.find(x=>spaces.has(x.space_room||x.state?.info?.personal_filtering_space||x.state_info?.personal_filtering_space))||ctx.logins.find(x=>[x.name,x.profile?.phone].some(name=>name&&label.includes(name)))||ctx.logins.find(x=>x.id===lastLogin[ctx.network]);if(match)account.value=match.id;
   lastLogin[ctx.network]=account.value;lastNetwork=ctx.network;account.disabled=false;ctx.ready=true;submit.disabled=false;say('Введите номер или идентификатор и нажмите «Найти».');input.focus();
  }catch(error){if(active(ctx))say(error.name==='AbortError'?'Сервер не ответил вовремя. Закройте и откройте поиск ещё раз.':error.message,'error');}
 }
 async function search(){
  const ctx=current;if(!active(ctx)||!ctx.ready||submit.disabled)return;
  let identifier;try{identifier=normalize(input.value,ctx.network)}catch(error){say(error.message,'error');input.focus();return;}
  const login=account.value;if(!ctx.logins.some(x=>x.id===login)){say('Выберите подключённый аккаунт '+networks[ctx.network]+'.','error');return;}
  const wait=limits.get(ctx.network+':'+login)||0;if(wait>Date.now()){say('Подождите '+Math.ceil((wait-Date.now())/1000)+' сек. перед следующим поиском.','error');return;}
  invalidate();const turn=ctx.turn;ctx.abort=new AbortController();submit.disabled=true;limits.set(ctx.network+':'+login,Date.now()+3500);say('Ищем в '+networks[ctx.network]+'…');
  try{
   const result=await request(ctx,'/resolve_identifier/'+encodeURIComponent(identifier),{login,signal:ctx.abort.signal});
   if(!active(ctx)||turn!==ctx.turn||login!==account.value)return;
   if(typeof result.id!=='string'||!(ctx.network==='whatsapp'?/^(?:lid-)?\d+$/:/^\d+$/).test(result.id))throw Error(networks[ctx.network]+' вернул неполный результат. Повторите поиск.');
   const card=node('section','mts-result'),name=node('h3','',result.name||identifier);card.append(name);
   const details=node('p','mts-result-details',identifier);card.append(details);
   if(Array.isArray(result.identifiers)){const labels=result.identifiers.filter(x=>typeof x==='string'&&/^(tel:|telegram:|https:\/\/t\.me\/)/.test(x)).slice(0,4).map(x=>x.replace(/^tel:/,''));if(labels.length)card.append(node('p','mts-result-details',labels.join(' · ')));}
   const go=node('button','mts-primary','Открыть чат');go.type='button';go.addEventListener('click',()=>void openChat(ctx,turn,login,result,go));card.append(go);results.replaceChildren(card);say('Контакт найден. Проверьте имя и откройте чат.','success');
  }catch(error){if(active(ctx)&&turn===ctx.turn)say(error.name==='AbortError'?'Поиск прерван или сервер не ответил вовремя. Можно повторить вручную.':error.message,'error');}
  finally{if(active(ctx)&&turn===ctx.turn)submit.disabled=false;}
 }
 async function openChat(ctx,turn,login,result,button){
  if(!active(ctx)||turn!==ctx.turn||login!==account.value||button.disabled)return;button.disabled=true;submit.disabled=true;network.disabled=true;account.disabled=true;input.disabled=true;say('Открываем личный чат…');
  try{
   let room=result.dm_room_mxid;
   if(!room){const created=await request(ctx,'/create_dm/'+encodeURIComponent(result.id),{login,body:{},timeout:75000});if(created.id!==result.id)throw Error(networks[ctx.network]+' вернул другой контакт. Выполните поиск заново.');room=created.dm_room_mxid;}
   if(typeof room!=='string'||!/^!(?:[A-Za-z0-9_-]{43}|[^\s:]+:[^\s]+)$/.test(room))throw Error('Чат пока не готов. Подождите и повторите открытие.');
   if(!active(ctx)||turn!==ctx.turn)return;await ctx.client.joinRoom(room);
   if(!active(ctx)||turn!==ctx.turn)return;dialog.close();location.hash='#/room/'+encodeURIComponent(room);
  }catch(error){if(active(ctx)&&turn===ctx.turn)say(error.name==='AbortError'?'Не удалось дождаться открытия чата. Проверьте список переписок перед повтором.':error.message||'Не удалось открыть чат. Проверьте подключение.','error');}
  finally{network.disabled=false;account.disabled=false;input.disabled=false;if(active(ctx)&&turn===ctx.turn){button.disabled=false;submit.disabled=false;}}
 }
 function mount(){window.MatrixContactSearch={open:service=>void open(document.activeElement,service)};window.MatrixTelegramContactSearch={open:()=>void open(document.activeElement,'telegram')};}
 function schedule(){if(scheduled)return;scheduled=true;requestAnimationFrame(()=>{scheduled=false;mount()})}
 function start(){new MutationObserver(records=>{if(records.some(r=>[...r.addedNodes].some(n=>n.nodeType===1)))schedule()}).observe(document.getElementById('matrixchat')||document.body,{childList:true,subtree:true});mount();}
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
