async ({op,args={}}) => {
 const sdk=window._imsdk;if(!sdk)throw Error('IM_NOT_READY');
 const origin=sdk.getOriginSdk(),auth=origin.getAuthService(),ms=origin.getMsgService();
 const ownId=String(auth.getMyUid())+'@'+String(auth.getDomain());
 if(!sdk.getAuthService().isLogin()||!auth.getMyUid())throw Error('IM_NOT_AUTHENTICATED');
 const timeout=p=>Promise.race([p,new Promise((_,reject)=>setTimeout(()=>reject(Error('IM_TIMEOUT')),25000))]);
 const custom=ct=>{try{return JSON.parse(ct.custom?.data||'{}')}catch{return {}}};
 const media=ct=>{const out=[];for(const kind of ['image','video','audio','file']){const a=ct[kind];if(a?.url)out.push({url:a.url,kind,name:a.fileName||a.name||({image:'photo.jpg',video:'video.mp4',audio:'audio.mp3',file:'document'}[kind]),type:kind});}if(ct.contentType===101){const d=custom(ct);if(d.url)out.push({url:d.url,kind:/mp4|webm|mov/i.test(d.suffix||'')?'video':/mp3|ogg|wav|m4a/i.test(d.suffix||'')?'audio':/pdf|doc|zip|xls|txt/i.test(d.suffix||'')?'file':'image',name:d.fileName||d.name||'photo.'+(d.suffix||'jpg'),type:'custom'});else if(d.picUrl)out.push({url:d.picUrl,kind:'image',name:'product.jpg',type:'card'});}for(const d of [custom(ct)])for(const item of [...(d.shopList||[]),...(d.E2_E1_items||[]),...(d.itemList||[])]){const url=item.imgUrl||item.pic||item.itemImgUrl;if(url)out.push({url,kind:'image',name:'product.jpg',type:'card'});}return out.slice(0,30).map((x,index)=>({...x,index}));};
 const normalize=m=>{
  if(!m||!m.messageId||!m.sender?.uid||!Number.isFinite(Number(m.createAt)))throw Error('INVALID_NATIVE_MESSAGE');
  const ct=m.content||{},attachments=media(ct).map(({index,type})=>({index,type}));let body=ct.text?.content||(!attachments.length?(ct.custom?.summary||m.searchableContent?.summary||''):'');
  if(ct.contentType===101){const d=custom(ct);body=[d.title,d.content,d.itemUrl].filter(x=>typeof x==='string'&&x).join('\n')||body;}
  if(!body&&!attachments.length)body='Сообщение Taobao: откройте оригинал в приложении.';
  return {id:String(m.messageId),body:String(body),attachments,own:m.sender.uid===ownId,timestamp:Number(m.createAt),sender:m.extension?.sender_nick?.replace(/^cntaobao/,'')||m.sender.uid,kind:body?(ct.contentType===1?'text':'notice'):'media'};
 };
 if(op==='identity')return {id:ownId,nick:sdk.getAuthService().getLoginNick(),connection:sdk.getAuthService().connectionStatus};
 if(op==='chats'){
  const raw=await timeout(origin.getConvService().listConversations(0,1000));
  if(!Array.isArray(raw))throw Error('INVALID_CONVERSATION_LIST');
  const names=new Map([...document.querySelectorAll('.conversation-item')].map(x=>[x.id,x.querySelector('.name')?.innerText]));
  return raw.map(c=>{
   const x=c.singleChatUserConversation;if(!x)return null;let target={};try{target=JSON.parse(x.user_extension?.target||'{}')}catch(_){}
   const cid=x.singleChatConversation?.cid;if(!cid)return null;
   return {id:cid,name:names.get(cid)||target.dnick||target.snick||cid,modified:Number(x.modifyTime||0),last_id:String(x.lastMessage?.message?.messageId||''),last_timestamp:Number(x.lastMessage?.message?.createAt||x.last_message_time||0),visible:x.visible===1};
  }).filter(x=>x&&x.visible).sort((a,b)=>b.modified-a.modified);
 }
 if(op==='history'){
  const stop=Number(args.since||0);let cursor=Date.now()+86400000,all=[],done=false;
  for(let i=0;i<(stop?30:1);i++){
   const res=await timeout(ms.listPrevMsgs(args.cid,String(cursor),50)),raw=res?.userMessageModels||[];
   const batch=raw.map(x=>normalize(x.message));all.push(...batch.filter(m=>!stop||m.timestamp>=stop-1));
   const min=batch.length?Math.min(...batch.map(x=>x.timestamp)):0;
   if(!raw.length||!res.hasMore||!stop||min<stop){done=true;break}
   if(min>=cursor)throw Error('HISTORY_CURSOR_STALLED');cursor=min;
  }
  if(!done)throw Error('HISTORY_BACKLOG_TOO_LARGE');
  return {messages:all.sort((a,b)=>a.timestamp-b.timestamp),cursor:all.length?Math.max(stop,...all.map(x=>x.timestamp)):stop};
 }
 if(op==='media'){
  if(typeof args.id!=='string'||!Number.isSafeInteger(args.timestamp)||!Number.isInteger(args.index)||args.index<0||args.index>99)throw Error('INVALID_MEDIA_LOCATOR');let cursor=args.timestamp+1,found;
  for(let i=0;i<5;i++){const res=await timeout(ms.listPrevMsgs(args.cid,String(cursor),50)),raw=res?.userMessageModels||[];found=raw.map(x=>x.message).find(m=>String(m.messageId)===args.id);if(found)break;const min=Math.min(...raw.map(x=>Number(x.message.createAt)));if(!res.hasMore||!raw.length||min<args.timestamp||min>=cursor)break;cursor=min;}
  const a=found&&media(found.content||{})[args.index];if(!a||a.type!==args.type)throw Error('ATTACHMENT_UNAVAILABLE');let url=a.url;if(url.startsWith('//'))url='https:'+url;return {...a,url};
 }
 if(op==='send'){
  if(typeof args.body!=='string'||!args.body.trim()||args.body.length>500)throw Error('TEXT_LIMIT_500');
  let receiver=sdk.getMessageService().getReceiverUid(args.cid);
  if(!receiver){const c=await timeout(origin.getConvService().getConversation(args.cid)),pair=c?.singleChatUserConversation?.singleChatConversation;receiver=pair?.pairFirst===ownId?pair?.pairSecond:pair?.pairFirst}
  if(typeof receiver!=='string'||!receiver||receiver===ownId||!/^\d+@cntaobao$/.test(receiver))throw Error('RECEIVER_NOT_VERIFIED');
  // Capture the actual server acknowledgement emitted by the official SDK.
  // A local bubble or a fulfilled send() with no native ID is insufficient.
  const events=origin.getEventService();let ack=null,uuid=null;
  const oldEmit=events.emit,oldSend=ms.sendMessage;
  events.emit=function(name,...values){for(const p of values)if(p&&p.uuid===uuid&&p.messageId)ack=String(p.messageId);return oldEmit.call(this,name,...values)};
  ms.sendMessage=function(payload,...rest){uuid=payload.uuid;return oldSend.call(this,payload,...rest)};
  try{await timeout(ms.sendTextMessage(args.cid,args.body,{actualReceivers:[ownId,receiver]}));if(!ack)throw Error('NATIVE_ACK_MISSING');window.__matrixShoppingLastSend={id:ack,uuid,accepted:true};return {id:ack,uuid}}
  catch(e){window.__matrixShoppingLastSend={uuid,accepted:false,code:String(e?.code||e?.body?.code||'SEND_NOT_CONFIRMED').replace(/[^A-Za-z0-9_.:-]/g,'').slice(0,100)};throw e}
  finally{events.emit=oldEmit;ms.sendMessage=oldSend}
 }
 throw Error('UNSUPPORTED_OPERATION');
}
