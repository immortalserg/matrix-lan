(()=>{'use strict';if(window.MatrixAccountTools)return;
 let dialog,list,status,current,busy=false,lastData;const reconnecting=new Set();
 const sdk=()=>window.mxMatrixClientPeg?.get?.();
 const names={threads:'Threads',ok:'Одноклассники',vk:'VK',whatsapp:'WhatsApp',telegram:'Telegram',imo:'IMO',twitter:'X / Twitter',instagram:'Instagram',facebook:'Facebook',tiktok:'TikTok',email:'Gmail',wechat:'WeChat',linkedin:'LinkedIn',taobao:'Taobao',pinduoduo:'Pinduoduo'};
 const el=(tag,text,cls)=>{const e=document.createElement(tag);if(text)e.textContent=text;if(cls)e.className=cls;return e;};
 function button(text,fn,cls=''){const e=el('button',text,cls);e.type='button';e.addEventListener('click',fn);return e;}
 async function api(path,body){const c=sdk();if(!c?.getUserId?.())throw Error('Сначала войдите в Matrix.');
  const u=new URL('/matrix-accounts/v1/'+path,location.origin);u.searchParams.set('user_id',c.getUserId());
  const r=await fetch(u,{method:body===undefined?'GET':'POST',headers:{Authorization:'Bearer '+c.getAccessToken(),'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)}),credentials:'omit',cache:'no-store',redirect:'error',signal:AbortSignal.timeout(90000)});
  const d=await r.json();if(sdk()!==c)throw Error('Сессия Matrix изменилась.');if(!r.ok)throw Error(d.error||'Операция не выполнена.');return d;
 }
 function ensure(){if(dialog)return;dialog=el('dialog',null,'mc-dialog mat-dialog');dialog.id='matrixAccountTools';dialog.setAttribute('aria-labelledby','matTitle');
  const head=el('header'),h=el('h2','Подключённые аккаунты');h.id='matTitle';head.append(h,button('Закрыть',()=>dialog.close()));
  const body=el('div',null,'mc-dialog-body');status=el('p',null,'mat-status');status.setAttribute('role','status');list=el('div');
  body.append(button('Проверить состояние',()=>void refresh(true),'mc-secondary'),status,list);dialog.append(head,body);document.body.append(dialog);
  dialog.addEventListener('close',()=>{current=null;});
 }
 async function show(key){ensure();current=sdk();if(!dialog.open)dialog.showModal();status.textContent='Проверяем аккаунты…';
  try{lastData=await api('accounts');render(lastData,key);}catch(e){status.textContent=e.message;}
 }
 function render(data,key){list.replaceChildren();status.textContent='Подключений: '+data.accounts.length;
  if(key&&!data.accounts.some(a=>a.key===key))status.textContent='Это подключение удалено или больше не найдено. Новое можно добавить через «Подключить новый аккаунт».';
  for(const a of data.accounts){const box=el('section',null,'mat-account');box.dataset.account=a.key;
   box.append(el('h3',(names[a.platform]||a.platform)+' · '+(a.profile_name||a.name)),el('p',[a.phone,a.username||a.login_id].filter(Boolean).join(' · ')),el('strong',a.status_text||a.state));
   const info=a.diagnostics||{};box.append(el('p',info.reason||''));if(a.provider_error)box.append(el('small','Код сервиса: '+a.provider_error));
   const steps=el('ol');for(const step of info.steps||[])steps.append(el('li',step));box.append(steps);
   if(a.deletion_error)box.append(el('p',a.deletion_error,'mat-error'));
   const actions=el('div',null,'mat-actions');actions.append(button('Переподключить',()=>void reconnect(a.key),'mc-secondary'),button('Удалить подключение',()=>void remove(a.key),'mat-delete'));box.append(actions);list.append(box);
   if(a.key===key){box.dataset.selected='true';setTimeout(()=>box.scrollIntoView({block:'nearest'}),0);}
  }
 }
 async function refresh(force=false){if(force){await api('refresh',{});status.textContent='Запрошена новая проверка…';await new Promise(r=>setTimeout(r,1000));}await show();dispatchEvent(new CustomEvent('matrix-accounts-changed'));}
 async function reconnect(key){if(reconnecting.has(key))return;reconnecting.add(key);try{const d=await api('accounts'),a=d.accounts.find(a=>a.key===key);if(!a){await show(key);return;}
  dialog?.open&&dialog.close();dispatchEvent(new CustomEvent('matrix-resume-login',{detail:{platform:a.platform,login_id:a.login_id,reconnect:true}}));
 }catch(e){await show();status.textContent=e.message;}finally{reconnecting.delete(key);}}
 async function remove(key){if(busy)return false;let account;
  try{const d=await api('accounts');account=d.accounts.find(a=>a.key===key);if(!account){await show(key);return false;}}
  catch(e){await show();status.textContent=e.message;return false;}
  const label=(names[account.platform]||account.platform)+' · '+(account.profile_name||account.name)+' · '+(account.phone||account.username||account.login_id);
  if(!confirm('Удалить подключение «'+label+'»?\nСессия моста, значок аккаунта и напоминания будут удалены. История сообщений сохранится.'))return false;
  busy=true;document.querySelectorAll('.mw-dialog[open]').forEach(d=>d.close());await show(key);status.textContent='Удаляем только выбранное подключение…';
  for(const b of list.querySelectorAll('button'))b.disabled=true;
  try{const result=await api('accounts/delete',{key,confirm_key:key});dispatchEvent(new CustomEvent('matrix-account-removed',{detail:{key}}));dispatchEvent(new CustomEvent('matrix-accounts-changed'));
   await show();status.textContent=result.space_cleanup_pending?'Сессия и напоминания удалены. Обновите Matrix, чтобы проверить значок пространства.':'Подключение удалено. История сообщений сохранена.';return true;
  }catch(e){await show();status.textContent=e.message;return false;}finally{busy=false;}
 }
 window.MatrixAccountTools={show,remove,reconnect};
 function mount(){if(dialog?.open&&sdk()!==current)dialog.close();const bar=document.querySelector('.mc-account-toolbar');if(!bar||!sdk()?.getUserId?.()||bar.querySelector('.mat-open'))return;bar.append(button('Аккаунты',()=>void show(),'mc-secondary mat-open'));}
 const params=new URL(location.href).searchParams,help=params.get('matrix_account_help'),platform=params.get('matrix_reconnect'),login=params.get('matrix_account');let resumed=false;
 function resume(){if(resumed||!sdk()?.getUserId?.())return;if(!help&&!platform)return;resumed=true;const u=new URL(location.href);for(const k of ['matrix_account_help','matrix_reconnect','matrix_account'])u.searchParams.delete(k);history.replaceState(history.state,'',u.href);
  if(help)void show(help);else if(names[platform]&&login)void reconnect(platform+':'+login);
 }
 setInterval(()=>{mount();resume();},700);mount();resume();
})();
