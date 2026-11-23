(() => {
  'use strict';
  if (window.__matrixConnectionsLoaded) return;
  window.__matrixConnectionsLoaded = true;
  const SERVICES = {
    threads:{label:'Threads',short:'@',bot:'@threadsbot:__SERVER_NAME__',note:'Личные сообщения Threads. Статус обмена проверяется отдельно от входа.',actions:[]},
    ok:{label:'Одноклассники',short:'ОК',bot:'@okbot:__SERVER_NAME__',note:'Личные сообщения и группы Одноклассников. Пока поддерживается текст.',actions:[]},
    vk:{label:'VK',short:'VK',bot:'@vkbot:__SERVER_NAME__',note:'Подключение VK ID. Дополнительные подтверждения выполняются в форме входа.',actions:[]},
    taobao:{label:'Taobao',short:'淘',bot:'@taobaobot:__SERVER_NAME__',note:'Переписка покупателя Taobao с магазинами. Проверка интеграции.',actions:[]},
    pinduoduo:{label:'Pinduoduo',short:'拼',bot:'@pinduoduobot:__SERVER_NAME__',note:'Переписка покупателя Pinduoduo с магазинами. Проверка интеграции.',actions:[]},
    linkedin: {label:'LinkedIn',short:'in',bot:'@linkedinbot:__SERVER_NAME__',note:'Личные и групповые сообщения LinkedIn в Matrix.',actions:[]},
    wechat: {label:'WeChat',short:'WC',bot:'@wechatbot:__SERVER_NAME__',note:'Личные чаты и группы WeChat. Подключение по QR-коду.',actions:[]},
    whatsapp: {label: 'WhatsApp', short: 'WA', bot: '@whatsappbot:__SERVER_NAME__', note: 'Подходит для обычного WhatsApp и WhatsApp Business. На телефоне откройте «Связанные устройства» и отсканируйте QR-код, который пришлёт бот.', actions: [['login qr', 'Подключить по QR-коду'], ['login phone', 'Подключить по номеру']]},
    telegram: {label: 'Telegram', short: 'TG', bot: '@telegrambot:__SERVER_NAME__', note: 'Бот попросит номер телефона, затем код входа и, если включена двухэтапная проверка, дополнительный пароль.', actions: [['login', 'Подключить Telegram']]},
    instagram: {label: 'Instagram', short: 'IG', bot: '@instagrambot:__SERVER_NAME__', note: 'Если вход с паролем отклоняется, подключите уже открытую сессию Instagram. Можно сначала войти на сайте Instagram через Facebook, если этот способ доступен вашему аккаунту. Боту нужна именно сессия Instagram.', url:'https://www.instagram.com/accounts/login/', site:'Открыть Instagram', guide:['Откройте Instagram в браузере и завершите вход, включая проверку безопасности. При желании используйте кнопку входа через Facebook на сайте Instagram.','Нажмите «Подключить сессию Instagram». Бот попросит данные сессии браузера.','В инструментах браузера откройте «Сеть», обновите Instagram, выберите запрос instagram.com и скопируйте его как cURL (POSIX / bash). Вставьте только в личный диалог с Instagram-ботом.','Если появляется checkpoint или ограничение запросов, сначала завершите проверку на сайте Instagram. Повторные нажатия входа ограничение не снимают.'], actions: [['login instagram', 'Подключить сессию Instagram'], ['login android', 'Войти по логину'], ['login instagram-password', 'Другой вход с паролем']]},
    facebook: {label: 'Facebook', short:'FB', bot:'@facebookbot:__SERVER_NAME__', note:'Подключение личных сообщений Facebook Messenger. Вход Facebook не подключает Instagram автоматически.', url:'https://www.facebook.com/', site:'Открыть Facebook', guide:['Войдите на facebook.com в браузере.','Нажмите «Подключить Facebook». Бот запросит данные сессии.','В инструментах браузера: «Сеть» → запрос facebook.com → «Копировать как cURL (POSIX / bash)». Вставьте в личный диалог с Facebook-ботом.','Можно выбрать вход с паролем; дополнительные подтверждения выполняйте по подсказкам бота.'], actions:[['login facebook','Подключить Facebook'],['login messenger','Подключить через Messenger'],['login messenger-lite','Войти с паролем']]},
    twitter: {label:'X / Twitter',short:'X',bot:'@twitterbot:__SERVER_NAME__',note:'Подключение личных сообщений X. Для входа через этого бота используется сессия браузера; пароль сам по себе для такого способа не нужен.',url:'https://x.com/',site:'Открыть X',guide:['Войдите на x.com в браузере.','Нажмите «Подключить X». Следуйте подсказкам бота для передачи сессии браузера.','Данные ct0 и auth_token находятся в инструментах браузера в разделе cookies для x.com. Передавайте их только в личный диалог с X-ботом.','Если X запросит PIN зашифрованных сообщений, следуйте инструкции бота.'],actions:[['login cookies','Подключить X']]},
    tiktok: {
      label:'TikTok',short:'TT',bot:'@tiktokbot:__SERVER_NAME__',
      note:'Экспериментальный шлюз личных сообщений TikTok от стороннего разработчика. Для входа используется уже открытая сессия TikTok в браузере.',
      url:'https://www.tiktok.com/',site:'Открыть TikTok',
      guide:['Войдите в TikTok в браузере.','Нажмите «Подключить TikTok». Бот попросит Cookie header.','В инструментах браузера откройте «Сеть», обновите TikTok, выберите запрос www.tiktok.com и скопируйте полное значение заголовка Cookie.','Вставьте значение только в личный диалог с TikTok-ботом. Работа переписки зависит от доступности TikTok и ограничений аккаунта.'],
      actions:[['login session-cookie','Подключить TikTok']]
    },
    email: {label:'Gmail',short:'G',bot:'@gmailbot:__SERVER_NAME__',note:'Новые письма, ответы и вложения Gmail в Matrix.',actions:[]},
    imo: {label:'IMO',short:'IMO',bot:'@imobot:__SERVER_NAME__',note:'Подключение IMO через QR-код.',actions:[]}
  };
  const localRooms = new Map();
  let toolbar, dialog, contextBar, activePlatform = '', opener, operation = false, mountedClient = null;
  const client = () => { try { return window.mxMatrixClientPeg?.get?.() || null; } catch (_) { return null; } };
  const element = (tag, cls, text) => {
    const node = document.createElement(tag); if (cls) node.className = cls;
    if (text) node.textContent = text; return node;
  };
  function button(text, cls, fn) {
    const b = element('button', cls, text); b.type = 'button'; b.addEventListener('click', fn); return b;
  }
  function privateBotRoom(room, c, bot) {
    if (!room || room.getMyMembership?.() !== 'join' || !room.getMember?.(bot)) return false;
    const members = room.getJoinedMembers?.() || [];
    return members.some(m => m.userId === bot) && members.some(m => m.userId === c.getUserId())
      && members.every(m => m.userId === bot || m.userId === c.getUserId())
      && (room.getMembers?.() || members).every(m => !['join','invite'].includes(m.membership) || m.userId === bot || m.userId === c.getUserId());
  }
  async function ensureBotRoom(c, key) {
    const bot = SERVICES[key].bot;
    const existing = c.getRooms().find(room => privateBotRoom(room, c, bot));
    if (existing) return existing.roomId;
    const saved = localRooms.get(key);
    if (saved && privateBotRoom(c.getRoom(saved), c, bot)) return saved;
    const result = await c.createRoom({preset:'private_chat', invite:[bot], is_direct:true});
    if (!result?.room_id) throw new Error('Не удалось открыть диалог с ботом.');
    localRooms.set(key, result.room_id);
    return result.room_id;
  }
  function setBusy(value) {
    operation = value;
    if (dialog) for (const b of dialog.querySelectorAll('[data-mc-action]')) b.disabled = value;
    if (contextBar) for (const b of contextBar.querySelectorAll('button')) b.disabled = value;
  }
  async function run(key, command = '') {
    if (operation) return;
    const service = SERVICES[key];
    if (!service || command && ![...service.actions.map(a => a[0]), 'help', 'cancel'].includes(command)) return;
    const c = client();
    if (!c?.getUserId?.()) { show(key); status('Сначала войдите в Matrix.'); return; }
    if (!dialog?.open) show(key);
    setBusy(true); status(command ? 'Открываем бота и отправляем команду…' : 'Открываем диалог с ботом…');
    try {
      const roomId = await ensureBotRoom(c, key);
      if (client() !== c || !c.getUserId()) throw new Error('Сессия Matrix изменилась. Повторите после входа.');
      if (command) await c.sendMessage(roomId, {msgtype:'m.text', body:command});
      if (client() !== c) return;
      location.hash = '#/room/' + encodeURIComponent(roomId);
      dialog.close();
    } catch (_) {
      status('Не удалось завершить действие. Проверьте соединение и историю бота перед повторной отправкой.');
    } finally { setBusy(false); }
  }
  function status(text) { if (dialog) dialog.querySelector('[data-mc-status]').textContent = text; }
  function show(key, from) {
    const service = SERVICES[key]; if (!service) return;
    activePlatform = key; opener = from || document.activeElement;
    if (!dialog) {
      dialog = element('dialog','mc-dialog'); dialog.id = 'matrixConnectionDialog';
      dialog.setAttribute('aria-labelledby','matrixConnectionTitle');
      const header = element('header'); const heading = element('div');
      heading.append(element('small','', 'ПОДКЛЮЧЕНИЕ В MATRIX'), element('h2',''));
      heading.lastChild.id = 'matrixConnectionTitle';
      const close = button('×','mc-close',()=>dialog.close()); close.setAttribute('aria-label','Закрыть');
      header.append(heading, close);
      const body = element('div','mc-dialog-body');
      const text = element('p','mc-instruction'); text.dataset.mcInstruction = '';
      const actions = element('div','mc-actions'); actions.dataset.mcActions = '';
      const guide = element('ol','mc-guide'); guide.dataset.mcGuide = ''; guide.hidden = true;
      const site = element('a','mc-site'); site.dataset.mcSite = ''; site.target='_blank'; site.rel='noopener noreferrer'; site.hidden=true;
      const message = element('p','mc-status'); message.dataset.mcStatus = ''; message.setAttribute('role','status'); message.setAttribute('aria-live','polite');
      body.append(text, site, actions, guide, message);
      const footer = element('footer');
      for (const [cmd,label] of [['','Открыть бота'],['help','Помощь'],['cancel','Отменить вход']]) {
        const b = button(label,'mc-secondary',()=>run(activePlatform,cmd)); b.dataset.mcAction = ''; footer.append(b);
      }
      dialog.append(header,body,footer);document.body.append(dialog);
      dialog.addEventListener('close',()=>{ if(!operation && opener?.isConnected) opener.focus(); });
      dialog.addEventListener('click',e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close();}});
    }
    dialog.querySelector('h2').textContent = service.label;
    dialog.querySelector('[data-mc-instruction]').textContent = service.note;
    const guide = dialog.querySelector('[data-mc-guide]'); guide.replaceChildren();guide.hidden=!service.guide;
    for(const text of service.guide || []) guide.append(element('li','',text));
    const site=dialog.querySelector('[data-mc-site]');site.hidden=!service.url;
    if(service.url){site.href=service.url;site.textContent=service.site+' ↗';}else site.removeAttribute('href');
    const actions = dialog.querySelector('[data-mc-actions]'); actions.replaceChildren();
    service.actions.forEach(([command,label],i)=>{
      const b=button(label,i===0?'mc-primary':'mc-secondary',()=>run(key,command));b.dataset.mcAction='';
      b.title=command;actions.append(b);
    });
    status(key==='instagram' ? 'Если бот ещё ждёт старый пароль или код, нажмите «Отменить вход», затем выберите новый способ. Данные сессии передавайте только в личный диалог с ботом.' : 'Команды отправляются только в личный диалог с выбранным ботом.');
    if (!dialog.open) dialog.showModal();
  }
  function activeBot(c) {
    const match = /^#\/room\/([^/?]+)/.exec(location.hash); if(!match) return '';
    let id;try{id=decodeURIComponent(match[1]);}catch(_){return '';}
    const room=c.getRoom(id);
    return Object.keys(SERVICES).find(key=>privateBotRoom(room,c,SERVICES[key].bot)) || '';
  }
  function mount() {
    const c=client();
    if (!c?.getUserId?.()) {
      toolbar?.remove();contextBar?.remove();if(dialog?.open)dialog.close();localRooms.clear();mountedClient=null;return;
    }
    if (mountedClient && mountedClient!==c) localRooms.clear();mountedClient=c;
    const key=activeBot(c);const view=document.querySelector('.mx_RoomView, .mx_HomePage, .mx_SpaceHomePage');
    if(!view){contextBar?.remove();toolbar?.remove();return;}
      if(!toolbar){
        toolbar=element('nav','mc-shortcuts mc-chat-shortcuts mc-account-toolbar');toolbar.setAttribute('aria-label','Подключение аккаунтов');
        const trigger=button('＋ Подключить новый аккаунт','mc-account-trigger',()=>{menu.hidden=!menu.hidden;trigger.setAttribute('aria-expanded',String(!menu.hidden));if(!menu.hidden)menu.querySelector('button')?.focus();});
        trigger.setAttribute('aria-haspopup','menu');trigger.setAttribute('aria-expanded','false');trigger.setAttribute('aria-controls','matrixAccountMenu');
        const menu=element('div','mc-account-menu');menu.id='matrixAccountMenu';menu.role='menu';menu.hidden=true;
        for(const [key,s] of Object.entries(SERVICES)){
          const b=button(s.label,'mc-shortcut mc-'+key,e=>show(key,e.currentTarget));b.role='menuitem';b.title='Подключить '+s.label;b.setAttribute('aria-haspopup','dialog');menu.append(b);
        }
        toolbar.append(trigger,menu);
        window.addEventListener('click',event=>{if(!menu.hidden&&(event.target.closest?.('.mc-shortcut')||!toolbar.contains(event.target))){menu.hidden=true;trigger.setAttribute('aria-expanded','false');}},true);
        toolbar.addEventListener('keydown',event=>{
          if(menu.hidden)return;
          const items=[...menu.querySelectorAll('button')],i=items.indexOf(document.activeElement);
          if(event.key==='Escape'){event.preventDefault();event.stopPropagation();menu.hidden=true;trigger.setAttribute('aria-expanded','false');trigger.focus();}
          else if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();const n=event.key==='Home'?0:event.key==='End'?items.length-1:(i+(event.key==='ArrowDown'?1:-1)+items.length)%items.length;items[n]?.focus();}
        });
      }
    if(view.firstElementChild!==toolbar)view.prepend(toolbar);
    if(!key){contextBar?.remove();return;}
    if(!contextBar||contextBar.dataset.platform!==key){
      contextBar?.remove();contextBar=element('div','mc-bot-actions');contextBar.dataset.platform=key;
      contextBar.append(element('strong','',SERVICES[key].label));
      const first=SERVICES[key].actions[0];if(first){const [cmd,text]=first;contextBar.append(button(text,'mc-primary',()=>run(key,cmd)));}
      contextBar.append(button('Ещё способы','mc-secondary',e=>show(key,e.currentTarget)));
    }
    if(toolbar.nextElementSibling!==contextBar)toolbar.after(contextBar);
  }
  let scheduled=false;
  function schedule(){if(scheduled)return;scheduled=true;setTimeout(()=>{scheduled=false;mount();},180);}
  const start=()=>{
    const observer=new MutationObserver(changes=>{
      if(changes.some(change=>!change.target.closest?.('.mc-dialog,.mc-shortcuts,.mc-bot-actions')))schedule();
    });observer.observe(document.getElementById('matrixchat')||document.body,{childList:true,subtree:true});
    addEventListener('hashchange',schedule);setInterval(mount,2500);mount();
  };
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
})();
