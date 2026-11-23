(()=>{
 'use strict';if(window.__matrixClientMenuLoaded)return;window.__matrixClientMenuLoaded=true;
 let trigger=null,queued=false,lastMenu=null;const ownClass='mcm-custom-menu-item';
 function isPlus(button){
  if(!button?.matches('button,[role="button"]')||!button.closest('.mx_RoomListPanel,.mx_LeftPanel_roomListContainer'))return false;
  return [...button.querySelectorAll('svg path[d]')].some(path=>path.getAttribute('d')?.startsWith('M11 13H6'));
 }
 function isSpaceAdd(button){
  return Boolean(button?.matches('button,[role="button"]')&&button.closest('.mx_SpaceHierarchy_listHeader_buttons')&&button.hasAttribute('aria-haspopup')&&(button.getAttribute('aria-label')||button.textContent).trim()==='Добавить');
 }
 function findTrigger(){return [...document.querySelectorAll('.mx_RoomListPanel button,.mx_RoomListPanel [role="button"],.mx_LeftPanel_roomListContainer button')].find(isPlus)||null;}
 function menuFor(button){
  if(button.getAttribute('aria-expanded')!=='true'&&button.getAttribute('data-state')!=='open')return null;
  const controls=button.getAttribute('aria-controls');let menu=controls?document.getElementById(controls):null;
  if(!menu&&button.id)menu=[...document.querySelectorAll('[role="menu"]')].find(x=>x.getAttribute('aria-labelledby')===button.id);
  if(!menu)menu=[...document.querySelectorAll('[role="menu"][data-state="open"],.mx_ContextualMenu[role="menu"]')].filter(x=>x.getBoundingClientRect().width>0).pop();
  return menu?.getAttribute('role')==='menu'&&menu.getBoundingClientRect().width>0?menu:null;
 }
 function closeMenu(){
  const menu=trigger?menuFor(trigger):null;
  const backdrop=menu?.closest('.mx_ContextualMenu_wrapper')?.querySelector('.mx_ContextualMenu_background');
  if(backdrop){backdrop.click();return;}
  if(menu)menu.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',code:'Escape',bubbles:true,cancelable:true}));
 }
 function action(menu,label,fn,cls=''){
  const button=document.createElement('div');button.className=ownClass+' '+cls;button.setAttribute('role','menuitem');button.setAttribute('aria-label',label);button.tabIndex=-1;button.textContent=label;
  const run=event=>{event.preventDefault();event.stopPropagation();closeMenu();requestAnimationFrame(fn);};button.addEventListener('click',run);
  button.addEventListener('keydown',event=>{
   if(event.key==='Enter'||event.key===' '){run(event);return;}
   if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){
    event.preventDefault();event.stopPropagation();const all=[...menu.querySelectorAll('[role="menuitem"],[role="menuitemcheckbox"]')].filter(x=>x.getAttribute('aria-disabled')!=='true'&&x.getBoundingClientRect().width>0),index=all.indexOf(button);let next=event.key==='Home'?0:event.key==='End'?all.length-1:(index+(event.key==='ArrowDown'?1:-1)+all.length)%all.length;all[next]?.focus();
   }
   if(event.key==='Escape'){event.preventDefault();event.stopPropagation();closeMenu();trigger?.focus();}
  });
  button.addEventListener('pointermove',()=>button.focus({preventScroll:true}));return button;
 }
 function mountMenu(menu){
  if(menu.querySelector('.mcm-add-client'))return;menu.classList.add('mcm-menu');
  const fragment=document.createDocumentFragment();
  const create=action(menu,'Добавить клиента',()=>window.MatrixContactSearch?.open(),'mcm-add-client');create.title='Telegram, WhatsApp, IMO: найти клиента и открыть чат';fragment.append(create);
  const sep=document.createElement('div');sep.className='mcm-separator';sep.setAttribute('role','separator');fragment.append(sep);menu.prepend(fragment);
  const section=document.createElement('div');section.className='mcm-settings-section';const separator=document.createElement('div');separator.className='mcm-separator';separator.setAttribute('role','separator');section.append(separator);
  const label=document.createElement('div');label.className='mcm-menu-caption';label.textContent='Подключения и настройки';section.append(label);
  const accountButton=document.querySelector('.mc-chat-shortcuts .mat-open');
  if(accountButton)section.append(action(menu,'Подключённые аккаунты',()=>{if(window.MatrixAccountTools?.show)void window.MatrixAccountTools.show();else accountButton.click();}));
  const msb=document.querySelector('.mc-chat-shortcuts .msb-open');if(msb)section.append(action(menu,'Уведомления MSB-CBMRS',()=>msb.click()));
  const service=document.querySelector('.mc-chat-shortcuts .mc-conversation-events');if(service)section.append(action(menu,service.getAttribute('aria-pressed')==='true'?'Скрыть служебные события':'Показать служебные события',()=>service.click()));
  const retention=document.querySelector('.mc-chat-shortcuts .mc-media-policy');if(retention){const note=document.createElement('div');note.className='mcm-menu-caption';note.textContent='Вложения хранятся 2 месяца';note.title=retention.title;section.append(note);}
  if(section.children.length>2)menu.append(section);lastMenu=menu;requestAnimationFrame(()=>{if(menu.isConnected&&menuFor(trigger)===menu)create.focus({preventScroll:true})});
 }
 function refresh(){
  const plus=findTrigger();document.documentElement.toggleAttribute('data-matrix-compact-header',Boolean(plus));
  if(trigger&&!trigger.isConnected)trigger=null;
  const expanded=[...document.querySelectorAll('button[aria-expanded="true"],[role="button"][aria-expanded="true"]')].find(x=>isPlus(x)||isSpaceAdd(x));if(expanded)trigger=expanded;
  if(plus){plus.title='Добавить клиента или чат';plus.setAttribute('aria-label','Добавить клиента или чат');}
  if(trigger){const menu=menuFor(trigger);if(menu)mountMenu(menu);else lastMenu=null;}
 }
 function schedule(){if(queued)return;queued=true;requestAnimationFrame(()=>{queued=false;refresh()})}
 document.addEventListener('pointerdown',event=>{const button=event.target.closest?.('button,[role="button"]');if(isPlus(button)||isSpaceAdd(button)){trigger=button;schedule();}},true);
 document.addEventListener('click',event=>{const button=event.target.closest?.('button,[role="button"]');if(isPlus(button)||isSpaceAdd(button)){trigger=button;schedule();}},true);
 function start(){new MutationObserver(records=>{if(records.some(r=>r.type==='attributes'||[...r.addedNodes,...r.removedNodes].some(n=>n.nodeType===1)))schedule()}).observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['aria-expanded','data-state']});refresh();}
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
