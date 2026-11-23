async ({op,args={}}) => {
 const page=document.querySelector('msg-page'),store=page?.store,api=store?.app?.chat,profile=store?.data?.auth?.profile;
 if(!profile?.id||store.data.auth.state!=='authorized')throw Error('IM_NOT_AUTHENTICATED');
 if(!api?._api?.connected)throw Error('IM_NOT_READY');
 const own=String(profile.id),timeout=p=>Promise.race([p,new Promise((_,reject)=>setTimeout(()=>reject(Error('IM_TIMEOUT')),25000))]);
 const chatID=x=>{const id=Number(x);if(!Number.isSafeInteger(id)||!id)throw Error('INVALID_CHAT_ID');return id;};
 const normalize=m=>{
  if(!m?.id||!Number.isSafeInteger(Number(m.time)))throw Error('INVALID_NATIVE_MESSAGE');
  const sender=String(m.sender?.id??m.sender??''),attaches=m.attaches||[];if(!sender)throw Error('INVALID_NATIVE_SENDER');
  const attachments=attaches.flatMap((a,index)=>['PHOTO','IMAGE','STICKER','AUDIO','VOICE','VIDEO','FILE'].includes(a._type)?[{index,type:a._type}]:[]);
  let body=m.text||'',kind='text';if(!body){kind=attachments.length?'media':'notice';body=attachments.length?'':attaches.find(a=>a._type==='CONTROL')?.shortMessage||'Сообщение Одноклассников: откройте оригинал в приложении.';}
  return {id:String(m.id),body:String(body),attachments,timestamp:Number(m.time),sender,own:sender===own,kind,name:m.sender?.name||sender};
 };
 if(op==='identity')return {id:own,nick:profile.name,connection:'connected'};
 if(op==='chats')return [...store.data.chats.values()].filter(c=>c.status==='ACTIVE'&&c.type!=='CHANNEL').map(c=>({id:String(c.id),name:c.type==='SELF_DIALOG'?'Сообщения себе':c.title||c.owner?.name||'Чат '+c.id,modified:Number(c.lastEventTime||c.lastMessage?.time||0),last_id:String(c.lastMessage?.id||''),last_timestamp:Number(c.lastMessage?.time||0),is_group:!['DIALOG','SELF_DIALOG'].includes(c.type)})).sort((a,b)=>b.modified-a.modified);
 if(op==='history'){
  const id=chatID(args.cid),since=Number(args.since||0);let from=Date.now()+1000,all=[],done=false;
  for(let i=0;i<12;i++){
   const raw=await timeout(api.fetchMessages(id,{from,backward:100}));if(!Array.isArray(raw))throw Error('INVALID_NATIVE_HISTORY');
   const batch=raw.map(normalize);all.push(...batch.filter(m=>m.timestamp>=since));const min=batch.length?Math.min(...batch.map(m=>m.timestamp)):0;
   if(batch.length<100||min<since){done=true;break;}if(min>=from)throw Error('HISTORY_CURSOR_STALLED');from=min-1;
  }
  if(!done)throw Error('HISTORY_BACKLOG_TOO_LARGE');const unique=[...new Map(all.map(m=>[m.id,m])).values()].sort((a,b)=>a.timestamp-b.timestamp);
  return {messages:unique,cursor:Math.max(since,...unique.map(m=>m.timestamp))};
 }
 if(op==='media'){
  const id=chatID(args.cid);if(typeof args.id!=='string'||!Number.isSafeInteger(args.timestamp)||!Number.isInteger(args.index)||args.index<0||args.index>99)throw Error('INVALID_MEDIA_LOCATOR');
  const raw=await timeout(api.fetchMessages(id,{from:args.timestamp+1,backward:100})),m=raw.find(m=>String(m.id)===args.id),a=m?.attaches?.[args.index];if(!a||a._type!==args.type)throw Error('ATTACHMENT_UNAVAILABLE');
  const kind=['PHOTO','IMAGE','STICKER'].includes(a._type)?'image':['VOICE','AUDIO'].includes(a._type)?'audio':a._type==='VIDEO'?'video':'file';
  const url=a._type==='STICKER'?(a.firstUrl||a.previewUrl||a.url):a.url||a.downloadUrl||a.fullUrl;
  if(typeof url!=='string'||!/^https:\/\//.test(url))throw Error('ATTACHMENT_UNAVAILABLE');
  return {url,kind,name:a.name||a.fileName||({image:'photo.png',audio:'audio.mp3',video:'video.mp4',file:'document'}[kind]),voice:a._type==='VOICE'};
 }
 if(op==='select'){api.select(chatID(args.cid));return {selected:true};}
 if(op==='send'){
  const id=chatID(args.cid);if(typeof args.body!=='string'||!args.body.trim()||args.body.length>500)throw Error('TEXT_LIMIT_500');
  if(String(page.state.selectedChat?.id)!==String(id))throw Error('WRONG_SELECTED_CHAT');
  const posting=api.getPostingService(),form=posting.getPostingForm(id);if(form?.text!==args.body)throw Error('COMPOSER_TEXT_MISMATCH');
  const raw=await timeout(posting.commit(id)),res=Array.isArray(raw)&&raw.length===1?raw[0]:null;
  if(!res?.message?.id||res.message.text!==args.body||String(res.chatId||id)!==String(id))throw Error('NATIVE_ACK_MISSING');
  posting.reset(id);return {id:String(res.message.id),timestamp:Number(res.message.time),native_ack:true};
 }
 throw Error('UNSUPPORTED_NATIVE_OPERATION');
}
