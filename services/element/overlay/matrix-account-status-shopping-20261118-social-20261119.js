(()=>{'use strict';if(window.__matrixAccountStatus)return;window.__matrixAccountStatus=true;
let current=null,snapshot=null,busy=false,blocked=false,failed=false,generation=0,info=null,renderQueued=false;
const sdk=()=>{try{return window.mxMatrixClientPeg?.get?.()||null}catch(_){return null}};
const originals=new Map();
function clear(){for(const [button,data] of originals){button.title=data.title;if(data.described===null)button.removeAttribute('aria-describedby');else button.setAttribute('aria-describedby',data.described);button.querySelectorAll('.mc-account-dot,.mc-account-network,.mc-account-service-logo').forEach(x=>x.remove());}originals.clear();info?.remove();info=null;}
function active(){const c=sdk();return c?.getUserId?.()&&c.getAccessToken?.()?c:null;}
async function api(path,post=false){const c=current;if(!c||active()!==c)throw Error('Session changed');const u=new URL('/matrix-accounts/v1/'+path,location.origin);u.searchParams.set('user_id',c.getUserId());const response=await fetch(u,{method:post?'POST':'GET',headers:{Authorization:'Bearer '+c.getAccessToken(),...(post?{'Content-Type':'application/json'}:{})},...(post?{body:'{}'}:{}),cache:'no-store',credentials:'omit',redirect:'error',signal:AbortSignal.timeout(20000)});if(response.status===401||response.status===403)blocked=true;if(!response.ok)throw Error('Accounts unavailable');return response.json();}
function render(){
 const c=active();if(c!==current){clear();current=c;snapshot=null;failed=false;blocked=false;generation++;if(c)void poll();return;}
 if(!c||!snapshot)return;
 const accounts=new Map(snapshot.accounts.filter(a=>a.space_room).map(a=>[a.space_room,a]));
 for(const [button,data] of originals){if(!button.isConnected){originals.delete(button);continue;}const item=button.closest('.mx_SpaceItem');if(!accounts.has(item?.getAttribute('data-rbd-draggable-id'))){button.title=data.title;button.querySelectorAll('.mc-account-dot,.mc-account-network,.mc-account-service-logo').forEach(x=>x.remove());if(data.described===null)button.removeAttribute('aria-describedby');else button.setAttribute('aria-describedby',data.described);originals.delete(button);}}
 for(const item of document.querySelectorAll('.mx_SpaceItem[data-rbd-draggable-id]')){
  const a=accounts.get(item.getAttribute('data-rbd-draggable-id'));if(!a)continue;
  const button=item.querySelector('.mx_SpaceButton'),avatar=button?.querySelector('.mx_SpaceButton_avatarWrapper');if(!button||!avatar)continue;
  if(!originals.has(button))originals.set(button,{title:button.title,described:button.getAttribute('aria-describedby')});
  const stale=Date.now()/1000-a.checked_at>720,color=failed||stale?'yellow':a.color;
  const label=failed?'Сервис проверки недоступен':stale?'Проверка устарела':a.status_text;
  let dot=avatar.querySelector('.mc-account-dot');if(!dot){dot=document.createElement('span');dot.className='mc-account-dot';dot.id='account-status-'+a.platform+'-'+a.login_id.replace(/[^\w-]/g,'');dot.role='img';avatar.append(dot);}
  let network=avatar.querySelector('.mc-account-network');if(!network){network=document.createElement('span');network.className='mc-account-network';network.setAttribute('aria-hidden','true');avatar.append(network);}
  if(network.textContent)network.textContent='';network.hidden=true;network.dataset.platform=a.platform;
  let logo=avatar.querySelector('.mc-account-service-logo');
  if(!logo){logo=document.createElement('img');logo.className='mc-account-service-logo';logo.alt='';logo.setAttribute('aria-hidden','true');logo.width=32;logo.height=32;logo.addEventListener('error',()=>{logo.hidden=true;});avatar.append(logo);}
  const service=['threads','ok','wechat','whatsapp','telegram','instagram','facebook','twitter','tiktok','email','imo','vk','linkedin','taobao','pinduoduo'].includes(a.platform)?a.platform:null;
  if(service){const src='/matrix-service-icons-20261117/'+service+(service==='imo'?'.ico':service==='pinduoduo'?'.png':'.svg');if(logo.getAttribute('src')!==src){logo.hidden=false;logo.src=src;}}else logo.hidden=true;
  dot.dataset.state=color;dot.textContent=color==='red'?'!':color==='yellow'?'·':'';dot.setAttribute('aria-label',label);
  const when=new Date(a.checked_at*1000).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'});
  button.title=a.name+'\n'+label+'\nПроверено: '+when+' · новые аккаунты появляются автоматически'+(a.space_warning?'\n'+a.space_warning:'');
  const described=(originals.get(button).described||'').split(' ').filter(Boolean);if(!described.includes(dot.id))described.push(dot.id);button.setAttribute('aria-describedby',described.join(' '));
 }
 const tree=document.querySelector('ul[data-rbd-droppable-id="top-level-spaces"]');
 if(tree){if(!info){info=document.createElement('div');info.className='mc-account-check-info';info.append(document.createElement('span'));const b=document.createElement('button');b.type='button';b.textContent='Проверить';b.addEventListener('click',()=>void refresh());info.append(b);}if(tree.nextElementSibling!==info)tree.after(info);const text=info.firstChild,when=new Date(snapshot.checked_at*1000).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'});const value=(failed?'Нет связи с проверкой':snapshot.checking?'Проверяем аккаунты…':'Проверено '+when)+' · авто '; if(text.textContent!==value)text.textContent=value;
 const pending=snapshot.pending_logins||{};for(const button of info.querySelectorAll('.mc-pending-login'))if(!pending[button.dataset.platform])button.remove();
 for(const platform of Object.keys(pending)){if(info.querySelector('.mc-pending-login[data-platform="'+platform.replace(/[^a-z0-9_-]/gi,'')+'"]'))continue;const resume=document.createElement('button');resume.className='mc-pending-login';resume.dataset.platform=platform;resume.textContent=({twitter:'X / Twitter',instagram:'Instagram',facebook:'Facebook',imo:'IMO',telegram:'Telegram',whatsapp:'WhatsApp',tiktok:'TikTok'})[platform]||platform.toUpperCase();resume.textContent+=': завершить вход';resume.addEventListener('click',()=>dispatchEvent(new CustomEvent('matrix-resume-login',{detail:{platform}})));info.append(resume);}}
}
async function poll(){if(busy||!current||blocked)return;busy=true;const c=current,turn=generation;try{const data=await api('accounts');if(current===c&&turn===generation){snapshot=data;failed=false;render();}}catch(_){if(current===c){failed=true;render();}}finally{busy=false;}}
async function refresh(){if(!current||blocked)return;try{await api('refresh',true);for(const ms of [1000,4000,10000])setTimeout(()=>void poll(),ms);}catch(_){failed=true;render();}}
function schedule(){if(renderQueued)return;renderQueued=true;setTimeout(()=>{renderQueued=false;render();},200);}
function start(){new MutationObserver(changes=>{if(changes.some(x=>!x.target.closest?.('.mc-account-dot,.mc-account-network,.mc-account-check-info')))schedule();}).observe(document.getElementById('matrixchat')||document.body,{childList:true,subtree:true});render();setInterval(()=>{render();void poll()},5000);addEventListener('matrix-accounts-changed',()=>void refresh());addEventListener('visibilitychange',()=>{if(!document.hidden)void poll()});}
if(document.readyState==='loading')addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
