(()=>{'use strict';if(window.__matrixAccountReconnectR4)return;window.__matrixAccountReconnectR4=true;
 const sdk=()=>window.mxMatrixClientPeg?.get?.();let owner=null,snapshot=null,loaded=0,loading=null,timer,pickerDialog=null;
 const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text)e.textContent=text;if(cls)e.className=cls;return e;};
 const PLAT_HINTS=[
  ['pinduoduo',['pinduoduo']],['taobao',['taobao']],['instagram',['instagram']],['threads',['threads']],
  ['telegram',['telegram']],['whatsapp',['whatsapp']],['wechat',['wechat']],['imo',['imo']],
  ['facebook',['facebook','messenger']],['twitter',['twitter','x / twitter']],['tiktok',['tiktok']],
  ['linkedin',['linkedin']],['email',['gmail','email']],['ok',['одноклассники','ok']],['vk',['вконтакте','vk']]
 ];
 function reset(){owner=sdk();snapshot=null;loaded=0;document.querySelectorAll('.mar-connection,.mar-ctx-reconnect').forEach(e=>e.remove());if(pickerDialog?.open)pickerDialog.close();}
 async function accounts(force=false){const client=sdk();if(!client?.getUserId?.())return null;if(owner!==client)reset();
  if(!force&&snapshot&&Date.now()-loaded<15000)return snapshot;if(loading)return loading;
  const job=(async()=>{const u=new URL('/matrix-accounts/v1/accounts',location.origin);u.searchParams.set('user_id',client.getUserId());
   const response=await fetch(u,{headers:{Authorization:'Bearer '+client.getAccessToken()},credentials:'omit',cache:'no-store',redirect:'error',signal:AbortSignal.timeout(15000)});
   if(!response.ok)throw Error('Не удалось проверить подключение.');const data=await response.json();if(sdk()!==client)return null;snapshot=data;loaded=Date.now();return data;
  })();loading=job;try{return await job;}finally{if(loading===job)loading=null;}
 }
 function matches(title,name){if(!name||!title)return false;return title===name||(title.endsWith(name)&&/[—–:\-\(]\s*$/.test(title.slice(0,-name.length)))||title.includes('('+name+')');}
 function detectPlatform(title){const low=(title||'').trim().toLowerCase();for(const [plat,keys] of PLAT_HINTS){if(keys.some(k=>low===k||low.startsWith(k+' ')||low.startsWith(k+'(')||low.includes('('+k)))return plat;}return null;}
 function findAccounts(data,client,title,displayedRoomId){
  if(!data?.accounts?.length)return [];
  if(displayedRoomId){const byId=data.accounts.filter(a=>a.space_room===displayedRoomId);if(byId.length)return byId;}
  const plat=detectPlatform(title);
  const pool=plat?data.accounts.filter(a=>a.platform===plat):data.accounts;
  const exact=pool.filter(a=>{const rn=a.space_room?client.getRoom?.(a.space_room)?.name:'';return (rn&&(title===rn||matches(title,rn)))||matches(title,a.name)||matches(title,a.profile_name);});
  if(exact.length)return exact;
  if(plat)return pool;
  return [];
 }
 function fallbackAvatar(dialog,rows){if(rows.length!==1)return;const platform=rows[0].platform;if(!['threads','ok','vk','whatsapp','telegram','imo','email','wechat','facebook','instagram','twitter','tiktok','linkedin','taobao','pinduoduo'].includes(platform))return;
  const icon='/matrix-service-icons-20261117/'+platform+(platform==='imo'?'.ico':platform==='pinduoduo'?'.png':'.svg');
  for(const img of dialog.querySelectorAll('.mx_AvatarSetting_avatar img'))if(img.complete&&!img.naturalWidth&&img.getAttribute('src')!==icon){img.removeAttribute('srcset');img.src=icon;img.alt=rows[0].name;img.classList.add('mar-service-avatar');}
 }
 function ensurePickerStyles(){
  if(document.getElementById('mar-picker-style'))return;
  const st=document.createElement('style');st.id='mar-picker-style';
  st.textContent=`
   .mar-ctx-reconnect .mar-ctx-icon{width:16px;height:16px;margin-right:12px;display:inline-flex;align-items:center;justify-content:center;flex:none;opacity:.9}
   .mar-picker-dialog{background:#181b21;color:#e8ecf2;border:1px solid rgba(255,255,255,.14);border-radius:14px;padding:18px 20px;width:min(440px,92vw);box-shadow:0 18px 48px rgba(0,0,0,.55);font-family:inherit}
   .mar-picker-dialog::backdrop{background:rgba(0,0,0,.62)}
   .mar-picker-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}
   .mar-picker-head h3{margin:0;font-size:16px;font-weight:600}
   .mar-picker-close{background:transparent;border:0;color:#9aa4b2;font-size:20px;cursor:pointer;padding:2px 6px;border-radius:6px;line-height:1}
   .mar-picker-close:hover{background:rgba(255,255,255,.08);color:#fff}
   .mar-picker-list{display:flex;flex-direction:column;gap:10px;max-height:55vh;overflow:auto}
   .mar-picker-row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:11px 12px;border-radius:10px;background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08)}
   .mar-picker-info{min-width:0;flex:1}
   .mar-picker-name{font-size:14px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:flex;align-items:center;gap:8px}
   .mar-picker-dot{width:9px;height:9px;border-radius:50%;flex:none;background:#f59e0b}
   .mar-picker-dot[data-state="green"]{background:#22c55e}
   .mar-picker-dot[data-state="red"]{background:#ef4444}
   .mar-picker-sub{font-size:12px;color:#9aa4b2;margin-top:3px}
   .mar-picker-btn{background:#0dbd8b;color:#0b1411;border:0;border-radius:8px;padding:7px 12px;font-size:13px;font-weight:600;cursor:pointer;white-space:nowrap}
   .mar-picker-btn:hover{filter:brightness(1.08)}
   .mar-picker-btn:disabled{opacity:.6;cursor:wait}
   .mar-picker-err{color:#f87171;font-size:12px;margin-top:8px}
   .mar-picker-foot{margin-top:12px;font-size:12px;color:#8a94a6}
  `;
  document.head.append(st);
 }
 async function openPicker(title,initialRows){
  ensurePickerStyles();
  if(!pickerDialog){pickerDialog=document.createElement('dialog');pickerDialog.className='mar-picker-dialog';pickerDialog.addEventListener('click',e=>{if(e.target===pickerDialog)pickerDialog.close();});document.body.append(pickerDialog);}
  pickerDialog.replaceChildren();
  const head=node('div','','mar-picker-head');
  head.append(node('h3','Переподключить: '+title));
  const closeBtn=node('button','×','mar-picker-close');closeBtn.type='button';closeBtn.setAttribute('aria-label','Закрыть');closeBtn.addEventListener('click',()=>pickerDialog.close());
  head.append(closeBtn);
  const list=node('div','','mar-picker-list');
  const errBox=node('div','','mar-picker-err');
  const foot=node('div','Выберите аккаунт — откроется QR-код или форма повторного входа.','mar-picker-foot');
  pickerDialog.append(head,list,errBox,foot);
  const renderRows=(rows)=>{
   list.replaceChildren();
   if(!rows.length){list.append(node('div','Подключённые аккаунты не найдены.','mar-picker-sub'));return;}
   for(const a of rows){
    const row=node('div','','mar-picker-row');
    const info=node('div','','mar-picker-info');
    const nameEl=node('div','','mar-picker-name');
    const dot=node('span','','mar-picker-dot');
    dot.dataset.state=a.color||(a.state==='CONNECTED'?'green':(a.state==='BAD_CREDENTIALS'||a.state==='LOGGED_OUT'||a.state==='WRONG_ACCOUNT'?'red':'yellow'));
    nameEl.append(dot,document.createTextNode(a.profile_name||a.name||a.key));
    info.append(nameEl,node('div',a.status_text||a.state||'','mar-picker-sub'));
    const btn=node('button','Переподключить','mar-picker-btn');btn.type='button';
    btn.addEventListener('click',async()=>{
     if(btn.disabled)return;btn.disabled=true;errBox.textContent='';
     try{
      if(!window.MatrixAccountTools?.reconnect)throw Error('Обновите страницу Matrix через Ctrl+F5.');
      pickerDialog.close();
      await window.MatrixAccountTools.reconnect(a.key);
     }catch(err){errBox.textContent=err?.message||'Не удалось запустить переподключение.';if(!pickerDialog.open)pickerDialog.showModal();}
     finally{btn.disabled=false;}
    });
    row.append(info,btn);list.append(row);
   }
  };
  renderRows(initialRows);
  if(!pickerDialog.open)pickerDialog.showModal();
  // Refresh fresh state in background while modal is open
  try{
   const fresh=await accounts(true);
   const client=sdk();
   if(fresh&&client&&pickerDialog.open){
    const updated=findAccounts(fresh,client,title,null);
    if(updated.length)renderRows(updated);
   }
  }catch(_){}
 }
 async function mountContextMenus(data,client){
  ensurePickerStyles();
  for(const menu of document.querySelectorAll('.mx_ContextualMenu')){
   if(menu.querySelector('.mar-ctx-reconnect'))continue;
   // Identify space context menu: has a header (h2) and a Settings/Leave button
   const heading=menu.querySelector('h2,.mx_SpacePanel_contextMenu_header');
   const headerText=heading?.textContent?.trim();
   if(!headerText)continue;
   const optionList=menu.querySelector('.mx_IconizedContextMenu_optionList')||menu.querySelector('[role="menu"]');
   if(!optionList)continue;
   const items=Array.from(optionList.querySelectorAll('button,[role="menuitem"]'));
   const settingsBtn=items.find(b=>/Настройки|Settings/i.test(b.textContent||''));
   const leaveBtn=items.find(b=>/Покинуть пространство|Leave space/i.test(b.textContent||''));
   if(!settingsBtn&&!leaveBtn)continue;
   const rows=findAccounts(data,client,headerText,null);
   if(!rows.length)continue;
   const btn=document.createElement('button');
   btn.type='button';
   btn.setAttribute('role','menuitem');
   btn.className='mx_AccessibleButton mx_IconizedContextMenu_item mar-ctx-reconnect';
   const ic=document.createElement('span');ic.className='mar-ctx-icon';
   ic.innerHTML='<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"></polyline><polyline points="1 20 1 14 7 14"></polyline><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path></svg>';
   const lbl=document.createElement('span');lbl.className='mx_IconizedContextMenu_label';lbl.textContent='Переподключить';
   btn.append(ic,lbl);
   btn.addEventListener('click',e=>{
    e.preventDefault();e.stopPropagation();
    // Dismiss Element contextual menu
    const bg=document.querySelector('.mx_ContextualMenu_background');if(bg)bg.click();
    void openPicker(headerText,rows);
   });
   if(leaveBtn&&leaveBtn.parentElement===optionList)optionList.insertBefore(btn,leaveBtn);
   else if(settingsBtn&&settingsBtn.parentElement===optionList)settingsBtn.after(btn);
   else optionList.append(btn);
  }
 }
 async function mount(){if(owner!==sdk())reset();const client=sdk();if(!client?.getUserId?.())return;
  const hasDialog=!!document.querySelector('.mx_SpaceSettingsDialog');
  const hasMenu=!!document.querySelector('.mx_ContextualMenu');
  if(!hasDialog&&!hasMenu)return;
  let data;try{data=await accounts();}catch(_){return;}if(sdk()!==client||!data)return;
  if(hasMenu)await mountContextMenus(data,client);
  for(const dialog of document.querySelectorAll('.mx_SpaceSettingsDialog')){
   const title=dialog.querySelector('.mx_Dialog_title')?.textContent?.trim();const host=dialog.querySelector('.mx_TabbedView_tabPanel .mx_SettingsTab');if(!title||!host)continue;
   const displayedRoomId=Array.from(dialog.querySelectorAll('.mx_CopyableText')).map(e=>e.textContent.trim()).find(text=>/^![^\s]+:[^\s]+$/.test(text));
   const rows=findAccounts(data,client,title,displayedRoomId);
   if(!rows.length){dialog.querySelector('.mar-connection')?.remove();continue;}
   fallbackAvatar(dialog,rows);
   const signature=JSON.stringify(rows.map(a=>[a.key,a.state,a.status_text]));let panel=dialog.querySelector('.mar-connection');if(panel&&panel.parentElement!==host){panel.remove();panel=null;}if(panel?.dataset.signature===signature)continue;
   if(!panel){panel=node('section');panel.className='mar-connection';panel.setAttribute('aria-label','Подключение аккаунта');host.prepend(panel);}panel.dataset.signature=signature;panel.replaceChildren();panel.append(node('h3','Подключение аккаунта'));
   for(const a of rows){const line=node('div');line.className='mar-account';line.dataset.state=a.color||(a.state==='CONNECTED'?'green':'yellow');line.append(node('p',a.profile_name||a.name),node('p',a.status_text||a.state));
    const button=node('button','Переподключить');button.type='button';button.className='mc-secondary';button.dataset.account=a.key;
    button.addEventListener('click',async()=>{if(button.disabled)return;button.disabled=true;
     try{if(!window.MatrixAccountTools)throw Error('Обновите страницу Matrix через Ctrl+F5.');(dialog.closest('.mx_Dialog')||dialog).querySelector('.mx_Dialog_cancelButton')?.click();await window.MatrixAccountTools.reconnect(a.key);}
     catch(error){line.append(node('p',error.message));}finally{button.disabled=false;}
    });line.append(button);panel.append(line);
   }
   panel.append(node('small','При необходимости откроется QR-код или форма повторного входа для выбранного аккаунта.'));
  }
 }
 function schedule(){clearTimeout(timer);timer=setTimeout(()=>void mount(),60);}
 function start(){new MutationObserver(schedule).observe(document.body,{childList:true,subtree:true});setInterval(()=>{if(owner!==sdk()||document.querySelector('.mx_SpaceSettingsDialog,.mx_ContextualMenu'))void mount();},5000);schedule();}
 addEventListener('matrix-accounts-changed',()=>{loaded=0;schedule();});
 document.addEventListener('error',event=>{if(event.target instanceof HTMLImageElement&&event.target.closest('.mx_SpaceSettingsDialog .mx_AvatarSetting_avatar'))schedule();},true);
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
