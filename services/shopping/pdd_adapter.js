async ({op,args={}}) => {
 const realm=args.detail?window.__matrixPDDDetail:window;
 const unpack=r=>r&&typeof r.result==='object'?{...r,...r.result}:r;
 const timeout=p=>Promise.race([p,new Promise((_,reject)=>setTimeout(()=>reject(Error('IM_TIMEOUT')),25000))]);
 const media=m=>{const out=[],info=m.info||{};let payload=m.content;if(typeof payload==='string'){try{payload=JSON.parse(payload)}catch{payload=null}}const add=(url,kind,type,name)=>{if(typeof url==='string'&&/^(https?:)?\/\//.test(url))out.push({url:url.startsWith('//')?'https:'+url:url,kind,type,name:name||({image:'photo.jpg',video:'video.mp4',audio:'audio.mp3',file:'document'}[kind])});};
  if(Number(m.type)===1&&typeof m.content==='string')add(m.content,'image','image');
  for(const p of [info,payload])if(p&&typeof p==='object'){add(p.image_url||p.img_url,'image','image');add(p.video_url,'video','video');add(p.audio_url||p.voice_url,'audio','audio');add(p.file_url,'file','file',p.file_name);}
  const goods=info.data?.goods_info||m.biz_context;if(goods?.thumb_url)add(goods.thumb_url,'image','card','product.jpg');
  return out.filter((v,i,a)=>a.findIndex(x=>x.url===v.url)===i).map((x,index)=>({...x,index}));};
 function find(kind){
  const key=kind==='list'?'__matrixPDDStore':'__matrixPDDMsgStore';if(realm[key])return realm[key];
  const queue=[];for(const el of [...realm.document.querySelectorAll('body *')].slice(0,600))for(const k of Object.keys(el))if(/^__reactFiber|^__reactInternalInstance|^__reactContainer/.test(k))queue.push(el[k]);
  const seen=new Set();let n=0;
  while(queue.length&&n++<8000){const f=queue.pop();if(!f||seen.has(f))continue;seen.add(f);const p=f.memoizedProps;
   const candidates=kind==='list'?[p?.store,p?.value?.store,f.stateNode?.store]:[p?.msgStore,p?.value?.msgStore];
   const s=candidates.find(x=>kind==='list'?x?.$service?.getMallChatList:x?.$service?.getChatList&&x?.sendMessage);
   if(s){realm[key]=s;return s}for(const k of ['child','sibling','return'])if(f[k])queue.push(f[k]);
  }
  throw Error('IM_STORE_NOT_READY');
 }
 if(op==='identity'){
  const s=find('list');if(!s.uid)throw Error('IM_NOT_AUTHENTICATED');return {id:String(s.uid)};
 }
 if(op==='chats'){
  const s=find('list'),chats=new Map();let cursor={};
  for(let page=0;page<25;page++){
   const res=unpack(await timeout(s.$service.getMallChatList({version:3,mode:2,size:20,...cursor})));
   if(res.result!=='ok'||!Array.isArray(res.conversations))throw Error('CHAT_LIST_REJECTED');
   for(const m of res.conversations){
    const from=m.from||{},to=m.to||{};
    let id=from.mall_id||to.mall_id;
    if(!id){const peer=String(from.uid)===String(s.uid)?to:from;if(/mall/.test(peer.role||'')){id=String(peer.uid||'');if(id.startsWith('cs_'))id=id.split('_')[1]}}
    id=String(id||'');const info=res.mall_info_map?.[id];if(!/^\d+$/.test(id)||!info)continue;
    const c={id,name:info.mall_name||info.name||id,modified:Number(m.ts)*1000,last_id:String(m.msg_id||''),last_timestamp:Number(m.ts)*1000,visible:true};
    const old=chats.get(id);if(!old||old.modified<c.modified)chats.set(id,c);
   }
   if(!res.has_more||!res.conversations.length)return [...chats.values()].sort((a,b)=>b.modified-a.modified);
   const last=res.conversations[res.conversations.length-1];if(cursor.start_msg_id===String(last.msg_id))throw Error('CHAT_CURSOR_STALLED');
   cursor={start_msg_id:String(last.msg_id),start_ts:Number(last.ts),is_last_mall:last.chat_type_id===0||!!last.from?.mall_id||!!last.to?.mall_id};
  }
  throw Error('CHAT_LIST_BACKLOG_TOO_LARGE');
 }
 const s=find('detail');
 if(op==='ready')return {ready:true};
 if(op==='history'){
  if(!/^\d+$/.test(String(args.cid))||!args.own_id)throw Error('INVALID_CHAT_ID');
  const stop=Number(args.since||0);let cursor={},all=[];
  for(let i=0;i<(stop?30:1);i++){
   const res=unpack(await timeout(s.$service.getChatList({list:{with:{role:'mall',id:String(args.cid)},size:50,...cursor}})));
   if(res.result!=='ok'||!Array.isArray(res.messages))throw Error('HISTORY_REJECTED');
   const batch=res.messages.map(m=>{
    const id=String(m.msg_id||'');if(!id||id.endsWith('_fake')||!m.from||!Number.isFinite(Number(m.ts)))throw Error('INVALID_NATIVE_MESSAGE');
    const own=String(m.from.uid)===String(args.own_id),attachments=media(m).map(({index,type})=>({index,type}));let body=typeof m.content==='string'?m.content:'';
    if(attachments.length&&Number(m.type)===1)body='';
    if(!body&&Array.isArray(m.rich_text?.content))body=m.rich_text.content.filter(x=>x.type==='text').map(x=>x.text).join('');
    if(!body&&!attachments.length)body='Сообщение Pinduoduo: откройте оригинал в приложении.';
    return {id,body,attachments,own,timestamp:Number(m.ts)*1000,sender:String(m.from.uid||m.from.role||'Pinduoduo'),kind:body?(Number(m.type)===0?'text':'notice'):'media'};
   });
   all.push(...batch.filter(m=>!stop||m.timestamp>=stop-1000));
   const min=batch.length?Math.min(...batch.map(x=>x.timestamp)):0;
   if(!res.has_more||!batch.length||!stop||min<stop)return {messages:all.sort((a,b)=>a.timestamp-b.timestamp),cursor:all.length?Math.max(stop,...all.map(x=>x.timestamp)):stop};
   const oldest=res.messages.reduce((a,b)=>Number(a.ts)<Number(b.ts)?a:b);if(cursor.start_msg_id===String(oldest.msg_id))throw Error('HISTORY_CURSOR_STALLED');
   cursor={start_msg_id:String(oldest.msg_id)};
  }
  throw Error('HISTORY_BACKLOG_TOO_LARGE');
 }
 if(op==='media'){
  if(!/^\d+$/.test(String(args.cid))||typeof args.id!=='string'||!Number.isInteger(args.index)||args.index<0||args.index>99)throw Error('INVALID_MEDIA_LOCATOR');let cursor={},found;
  for(let page=0;page<30;page++){const res=unpack(await timeout(s.$service.getChatList({list:{with:{role:'mall',id:String(args.cid)},size:50,...cursor}})));if(res.result!=='ok'||!Array.isArray(res.messages))throw Error('HISTORY_REJECTED');found=res.messages.find(m=>String(m.msg_id)===args.id);if(found)break;if(!res.has_more||!res.messages.length)break;const oldest=res.messages.reduce((a,b)=>Number(a.ts)<Number(b.ts)?a:b);if(cursor.start_msg_id===String(oldest.msg_id))throw Error('HISTORY_CURSOR_STALLED');cursor={start_msg_id:String(oldest.msg_id)};}
  const a=found&&media(found)[args.index];if(!a||a.type!==args.type)throw Error('ATTACHMENT_UNAVAILABLE');return a;
 }
 if(op==='send'){
  if(String(s.mallId)!==String(args.cid))throw Error('WRONG_CHAT');
  if(s.chatDisabled||s.isRiskControlled)throw Error('NATIVE_CHAT_RESTRICTED');
  if(typeof args.body!=='string'||!args.body.trim()||args.body.length>500)throw Error('TEXT_LIMIT_500');
  const response=await timeout(new Promise((resolve,reject)=>{
   const result=s.sendMessage({content:args.body,type:0},(status,data)=>resolve({status,data}));
   if(result?.catch)result.catch(reject);
  }));
  const data=response.data;if(response.status!==0||data?.result!=='ok'||!data.msg_id)throw Error('NATIVE_ACK_MISSING');
  return {id:String(data.msg_id)};
 }
 throw Error('UNSUPPORTED_OPERATION');
}
