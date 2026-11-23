(()=>{'use strict';
 if(window.__matrixMSBCBMRS)return;window.__matrixMSBCBMRS=true;
 const names={threads:'Threads',ok:'Одноклассники',vk:'VK',whatsapp:'WhatsApp',telegram:'Telegram',instagram:'Instagram',facebook:'Facebook',twitter:'X / Twitter',tiktok:'TikTok',imo:'IMO',email:'Gmail',wechat:'WeChat',linkedin:'LinkedIn',taobao:'Taobao',pinduoduo:'Pinduoduo'};
 let dialog,form,status,history,current,poll,accounts=[],data;
 const sdk=()=>window.mxMatrixClientPeg?.get?.();
 function el(tag,text,cls){const n=document.createElement(tag);if(text)n.textContent=text;if(cls)n.className=cls;return n;}
 function button(text,fn){const b=el('button',text);b.type='button';b.addEventListener('click',fn);return b;}
 async function api(path,body){
  const c=current;if(!c||sdk()!==c||!c.getUserId?.())throw Error('Сессия Matrix изменилась. Откройте настройки заново.');
  const u=new URL('/matrix-accounts/v1/msb-cbmrs/'+path,location.origin);u.searchParams.set('user_id',c.getUserId());
  const r=await fetch(u,{method:body===undefined?'GET':'POST',headers:{Authorization:'Bearer '+c.getAccessToken(),'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)}),credentials:'omit',cache:'no-store',redirect:'error',signal:AbortSignal.timeout(25000)});
  const result=await r.json();if(sdk()!==c||current!==c)throw Error('Сессия Matrix изменилась.');
  if(!r.ok)throw Error(r.status===401||r.status===403?'Настройки доступны владельцу Matrix.':result.error||'Сервис уведомлений недоступен.');return result;
 }
 function field(name,title,type='text',value=''){
  const label=el('label',null,'msb-field');label.append(el('span',title));const input=el('input');input.name=name;input.type=type;
  if(type==='checkbox'){input.checked=!!value;label.classList.add('msb-toggle');}else input.value=value;
  if(type==='password'){input.autocomplete='new-password';input.value='';}label.append(input);return label;
 }
 function feedback(text,error=false){status.textContent=text;status.dataset.error=String(error);}
 function renderStatus(d){
  const last=d.last_delivery;let text=(d.enabled?'Уведомления включены. ':'Уведомления выключены. ')+`Сбоев: ${d.active_incidents||0}. В очереди: ${d.queue_size||0}. `;
  text+=last?last.message+(last.http_status?' (HTTP '+last.http_status+')':''):'API ещё не проверен.';
  feedback(text,!!last&&!last.ok);history.replaceChildren();
  for(const item of [...(d.history||[])].reverse().slice(0,8)){
   const at=new Date(item.at*1000).toLocaleString('ru-RU',{timeZone:'Asia/Ashgabat'});
   history.append(el('li',at+' · '+(item.kind==='test'?'Проверка API':item.kind==='recovery'?'Восстановление':'Сбой аккаунта · '+(accounts.find(a=>a.key===item.account_key)?.name||item.account_key||''))+' · '+item.message));
  }
 }
 function render(d){
  data=d;accounts=d.accounts||[];form.replaceChildren();
  form.append(field('enabled','Отправлять уведомления в MSB-CBMRS','checkbox',d.enabled));
  const endpoint=field('endpoint','Адрес API MSB-CBMRS','url',d.endpoint);endpoint.querySelector('input').required=true;form.append(endpoint);
  const site=field('site_url','Адрес вашего Matrix','url',d.site_url);site.querySelector('input').required=true;form.append(site);
  const key=field('api_key','API-ключ','password');key.querySelector('input').placeholder=d.api_key_set?'Ключ сохранён; оставьте пустым, чтобы не менять':'Введите API-ключ';form.append(key);
  const times=el('div',null,'msb-grid');
  for(const [name,title,seconds] of [['delay_minutes','Задержка перед уведомлением, мин.',d.delay_seconds],['repeat_minutes','Повторное напоминание, мин. (0 — без повторов)',d.repeat_seconds],['stale_minutes','Проверка считается устаревшей через, мин.',d.stale_seconds]]){
   const label=field(name,title,'number',seconds/60);const input=label.querySelector('input');input.required=true;input.min=name==='stale_minutes'?1:0;input.max=name==='repeat_minutes'?10080:1440;input.step='any';times.append(label);
  }form.append(times);
  form.append(field('notify_yellow','Уведомлять при жёлтом статусе','checkbox',d.notify_yellow),field('notify_red','Уведомлять при красном статусе','checkbox',d.notify_red),field('notify_recovery','Сообщать о восстановлении связи','checkbox',d.notify_recovery));
  const all=field('all_platforms','Все сервисы, включая добавленные позже','checkbox',!d.platforms.length);form.append(all);
  const choices=el('div',null,'msb-platforms');for(const [key,title] of Object.entries(names))choices.append(field('platform_'+key,title,'checkbox',!d.platforms.length||d.platforms.includes(key)));
  const setAll=()=>{choices.hidden=all.querySelector('input').checked;};all.querySelector('input').addEventListener('change',setAll);setAll();form.append(choices);
  const details=el('details');details.append(el('summary','Подключения: уведомления, восстановление и удаление'));
  details.append(el('p','Флажок «Уведомлять» управляет напоминаниями. «Переподключить» восстанавливает вход, «Удалить подключение» удаляет сессию и значок аккаунта. Если сервис не сообщает телефон, укажите его здесь.','msb-help'));
  for(const a of accounts){
   const box=el('section',null,'msb-account');box.dataset.account=a.key;
   const active=field('track','Уведомлять: '+a.name,'checkbox',!d.excluded_accounts.includes(a.key));box.append(active,el('small',(a.status_text||a.state)+' · '+a.login_id));
   const labels=el('div',null,'msb-grid'),override=d.account_overrides[a.key]||{};
   const name=field('account_name','Имя для уведомления','text',override.name||'');name.querySelector('input').maxLength=180;
   const phone=field('account_phone','Телефон для уведомления','tel',override.phone||'');phone.querySelector('input').pattern='\\+?[0-9]{5,15}';phone.querySelector('input').placeholder=a.phone||'+993…';
   labels.append(name,phone);box.append(labels);if(window.MatrixAccountTools){box.append(button('Переподключить',()=>void window.MatrixAccountTools.reconnect(a.key)),button('Удалить подключение',()=>void window.MatrixAccountTools.remove(a.key)));}details.append(box);
  }form.append(details);
  const actions=el('div',null,'msb-actions');const save=el('button','Сохранить','mc-primary');save.type='submit';actions.append(save,button('Сохранить и проверить API',()=>void submit(true)));form.append(actions);
  form.append(el('p','Проверка создаёт отдельное тестовое уведомление в MSB-CBMRS. Отсутствие новых сообщений от собеседника само по себе не считается сбоем.','msb-help'));
  renderStatus(d);
 }
 function values(){
  const get=n=>form.elements.namedItem(n),result={};
  for(const name of ['endpoint','site_url','api_key'])result[name]=get(name).value.trim();
  for(const name of ['enabled','notify_yellow','notify_red','notify_recovery'])result[name]=get(name).checked;
  for(const [name,key] of [['delay_minutes','delay_seconds'],['repeat_minutes','repeat_seconds'],['stale_minutes','stale_seconds']])result[key]=Math.round(Number(get(name).value)*60);
  result.platforms=get('all_platforms').checked?[]:Object.keys(names).filter(k=>get('platform_'+k).checked);
  if(!get('all_platforms').checked&&!result.platforms.length)throw Error('Выберите хотя бы один сервис или выключите уведомления.');
  result.excluded_accounts=[...data.excluded_accounts.filter(k=>!accounts.some(a=>a.key===k))];result.account_overrides={...data.account_overrides};
  for(const section of form.querySelectorAll('[data-account]')){
   const k=section.dataset.account;if(!section.querySelector('[name=track]').checked)result.excluded_accounts.push(k);
   const name=section.querySelector('[name=account_name]').value.trim(),phone=section.querySelector('[name=account_phone]').value.trim();
   if(name||phone)result.account_overrides[k]={name,phone};else delete result.account_overrides[k];
  }return result;
 }
 async function submit(test=false){
  if(!form.reportValidity())return;let body;
  try{body=values();}catch(e){feedback(e.message,true);return;}
  for(const b of form.querySelectorAll('button'))b.disabled=true;
  try{
   await api('settings',body);form.elements.namedItem('api_key').value='';
   if(test){await api('test',{});feedback('Тест поставлен в очередь. Ожидаем подтверждение API…');}
   else {feedback('Настройки сохранены.');await updateStatus();}
  }catch(e){feedback(e.message,true);}finally{if(form)for(const b of form.querySelectorAll('button'))b.disabled=false;}
 }
 async function updateStatus(){try{const d=await api('settings');if(dialog.open)renderStatus(d);}catch(e){if(dialog.open)feedback(e.message,true);}}
 async function open(){
  if(!dialog){
   dialog=el('dialog',null,'mc-dialog msb-dialog');dialog.id='matrixMSBCBMRS';dialog.setAttribute('aria-labelledby','msbTitle');
   const header=el('header'),title=el('h2','MSB-CBMRS');title.id='msbTitle';header.append(title,button('Закрыть',()=>dialog.close()));
   const body=el('div',null,'mc-dialog-body');body.append(el('p','Уведомления о потере связи с подключёнными аккаунтами Matrix.'));
   status=el('p',null,'msb-status');status.setAttribute('role','status');form=el('form');form.addEventListener('submit',e=>{e.preventDefault();void submit(false);});history=el('ul',null,'msb-history');body.append(status,form,history);dialog.append(header,body);document.body.append(dialog);
   dialog.addEventListener('close',()=>{clearInterval(poll);for(const e of form.querySelectorAll('input'))e.value='';current=null;});
  }
  current=sdk();if(!dialog.open)dialog.showModal();form.replaceChildren();history.replaceChildren();feedback('Загружаем настройки…');
  try{const d=await api('settings');if(dialog.open){render(d);clearInterval(poll);poll=setInterval(()=>void updateStatus(),5000);}}catch(e){feedback(e.message,true);}
 }
 function mount(){
  const c=sdk();if(dialog?.open&&c!==current)dialog.close();
  const bar=document.querySelector('.mc-account-toolbar');if(!bar||!c?.getUserId?.()||bar.querySelector('.msb-open'))return;
  const b=button('MSB-CBMRS',()=>void open());b.className='mc-secondary msb-open';b.setAttribute('aria-haspopup','dialog');bar.append(b);
 }
 addEventListener('matrix-account-removed',event=>{const key=event.detail?.key;accounts=accounts.filter(a=>a.key!==key);if(data){data.excluded_accounts=data.excluded_accounts.filter(k=>k!==key);delete data.account_overrides[key];}for(const section of form?.querySelectorAll('[data-account]')||[])if(section.dataset.account===key)section.remove();});
 setInterval(mount,1000);addEventListener('pagehide',()=>{dialog?.open&&dialog.close();});mount();
})();
