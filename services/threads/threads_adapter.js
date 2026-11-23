async ({op,args={}}) => {
 const r=window.require,viewer=r?.('BarcelonaCurrentUser'),env=r?.('CometRelayEnvironmentFactory')?.defaultEnvironment,rt=r?.('relay-runtime');
 if(!viewer?.isLoggedIn()||!viewer.getViewerID())throw Error('IM_NOT_AUTHENTICATED');
 if(!env||!rt)throw Error('IM_NOT_READY');
 const own=String(viewer.getViewerInteropMessagingUserFBID()||'');if(!own)throw Error('MESSAGING_NOT_ENABLED');
 const id=value=>{if(typeof value!=='string'||!/^\d+$/.test(value))throw Error('INVALID_NATIVE_ID');return value;};
 const timeout=p=>Promise.race([p,new Promise((_,reject)=>setTimeout(()=>reject(Error('IM_TIMEOUT')),25000))]);
 const mediaCache=window.__matrixThreadsMedia||(window.__matrixThreadsMedia=new Map());
 const media=c=>{if(!c||typeof c!=='object')return [];const items=[];if(c.ordered_images_videos?.length)for(const a of c.ordered_images_videos)items.push({url:a.attachment_cdn_url||a.attachment_cdn_fallback_url,kind:/Video/.test(a.__typename||'')?'video':'image'});else{for(const a of c.images||[])items.push({url:a.attachment_cdn_url||a.attachment_cdn_fallback_url,kind:'image'});for(const a of c.videos||[])items.push({url:a.attachment_cdn_url||a.attachment_cdn_fallback_url,kind:'video'});}if(c.animated_media)items.push({url:c.animated_media.attachment_mp4_url||c.animated_media.attachment_webp_url||c.animated_media.preview_cdn_url,kind:c.animated_media.attachment_mp4_url?'video':'image'});return items.filter(x=>x.url).map((x,index)=>({...x,index,type:x.kind}));};
 const query=async(name,vars)=>{
  const doc=r(name);if(!doc)throw Error('NATIVE_QUERY_UNAVAILABLE');
  const operation=rt.createOperationDescriptor(rt.getRequest(doc),vars,{force:true});
  const raw=await timeout(env.execute({operation}).toPromise()),packets=Array.isArray(raw)?raw:[raw];
  const packet=packets.find(p=>p?.data);if(!packet||packets.some(p=>p?.errors?.length)||packets.filter(p=>p?.data).length!==1)throw Error('INVALID_NATIVE_RESPONSE');return packet.data;
 };
 const normalize=m=>{
  const native=m?.as_micg_interface||m,sender=String(native?.sender_fbid||native?.sender?.id||''),timestamp=Number(native?.timestamp_ms||m?.timestamp_ms);
  const msgid=native?.message_id||m?.message_id||m?.id;
  if(!msgid||!sender||!Number.isSafeInteger(timestamp)||timestamp<=0)throw Error('INVALID_NATIVE_MESSAGE');
  const content=native.content||{},body=content.text_body??content.xma_text_body??content.threads_post_share_text_body??content.text_fragments?.map(f=>f.plaintext||f.link_fragment?.uri||'').join(''),attachments=media(content).map(({index,type})=>({index,type}));
  mediaCache.set(String(msgid),{content,at:Date.now(),cid:String(native.thread_fbid||'')});while(mediaCache.size>500)mediaCache.delete(mediaCache.keys().next().value);
  return {id:String(msgid),sender,timestamp,own:sender===own,body:body||(!attachments.length?'Сообщение Threads: откройте оригинал в приложении.':''),attachments,kind:body?'text':attachments.length?'media':'notice',name:native.sender?.name||native.sender?.username||sender};
 };
 const thread=async cid=>{
  const data=await query('BarcelonaMessageListContainerQuery.graphql',{thread_fbid:id(cid),min_uq_seq_id:null});if(data.get_slide_thread?.id!==cid)throw Error('WRONG_NATIVE_THREAD');return data.get_slide_thread;
 };
 const chat=t=>{
  const native=t.as_micg_interface||t,peers=(t.members?.nodes||[]).map(x=>x.as_micg_interface||x).filter(x=>String(x.id)!==own),last=t.messages?.edges?.[0]?.node;
  return {id:id(String(t.id)),name:native.thread_name||peers.map(x=>x.name||x.username||x.id).join(', ')||'Threads '+t.id,is_group:native.thread_type!=='ONE_TO_ONE',last_id:String(last?.as_micg_interface?.message_id||last?.message_id||''),modified:Number(native.timestamp_ms||last?.as_micg_interface?.timestamp_ms||last?.timestamp_ms||0),last_timestamp:Number(last?.as_micg_interface?.timestamp_ms||last?.timestamp_ms||native.timestamp_ms||0)};
 };
 if(op==='identity')return {id:String(viewer.getViewerID()),nick:viewer.getUsername(),connection:'connected'};
 if(op==='chats'){
  const all=new Map(),device=viewer.getDeviceId?.();if(typeof device!=='string'||!device)throw Error('IM_NOT_READY');
  let mailboxId=own;
  try{
   const setup=await query('BarcelonaMessagesSetup_MailboxQuery.graphql',{device_id_for_iris_subscription:device});
   if(setup?.get_slide_mailbox_for_iris_subscription?.id)mailboxId=String(setup.get_slide_mailbox_for_iris_subscription.id);
  }catch(_){}
  for(const folder of ['INBOX','PENDING']){
   let connection,after=null,done=false;
   try{
    for(let page=0;page<30;page++){
     const data=await query('BarcelonaMessagesThreadListPaginationQuery.graphql',{id:mailboxId,folder,first:20,after});
     connection=(data.node||data.fetch__SlideMailbox)?.threads_by_folder;
     if(!Array.isArray(connection?.edges))throw Error('INVALID_CHAT_LIST');
     for(const e of connection.edges){const c=chat(e.node);all.set(c.id,c);}
     if(!connection.page_info?.has_next_page){done=true;break;}
     const next=connection.page_info.end_cursor;if(!next||next===after)throw Error('CHAT_CURSOR_STALLED');after=next;
    }
    if(!done)throw Error('CHAT_BACKLOG_TOO_LARGE');
   }catch(err){
    if(folder==='INBOX')throw err;
   }
  }
  return [...all.values()].sort((a,b)=>b.modified-a.modified);
 }
 if(op==='history'){
  const cid=id(args.cid),since=Number(args.since||0),initial=await thread(cid);let connection=initial.messages,before,done=false;const all=[];
  for(let page=0;page<40;page++){
   if(!Array.isArray(connection?.edges))throw Error('INVALID_NATIVE_HISTORY');
   const batch=connection.edges.map(e=>normalize(e.node));all.push(...batch.filter(m=>m.timestamp>=since));
   if(!connection.page_info?.has_next_page||batch.some(m=>m.timestamp<since)){done=true;break;}
   const after=connection.page_info.end_cursor;if(!after||after===before)throw Error('HISTORY_CURSOR_STALLED');before=after;
   const data=await query('BarcelonaMessageListPaginationQuery.graphql',{id:cid,after,first:20,has_live_chats:true});connection=(data.node||data.fetch__SlideThread)?.messages;
  }
  if(!done)throw Error('HISTORY_BACKLOG_TOO_LARGE');
  const messages=[...new Map(all.map(m=>[m.id,m])).values()].sort((a,b)=>a.timestamp-b.timestamp);return {messages,cursor:Math.max(since,...messages.map(m=>m.timestamp))};
 }
 if(op==='media'){
  const cid=id(args.cid);if(typeof args.id!=='string'||!Number.isInteger(args.index)||args.index<0||args.index>99)throw Error('INVALID_MEDIA_LOCATOR');let cached=mediaCache.get(args.id);
  if(!cached||cached.cid!==cid||Date.now()-cached.at>300000){let connection=(await thread(cid)).messages,before;
   for(let page=0;page<40;page++){for(const e of connection?.edges||[])normalize(e.node);cached=mediaCache.get(args.id);if(cached&&cached.cid===cid)break;if(!connection?.page_info?.has_next_page)break;const after=connection.page_info.end_cursor;if(!after||after===before)throw Error('HISTORY_CURSOR_STALLED');before=after;const data=await query('BarcelonaMessageListPaginationQuery.graphql',{id:cid,after,first:20,has_live_chats:true});connection=(data.node||data.fetch__SlideThread)?.messages;}}
  const a=cached&&cached.cid===cid&&media(cached.content)[args.index];if(!a||a.type!==args.type)throw Error('ATTACHMENT_UNAVAILABLE');return {url:a.url,kind:a.kind,name:a.kind==='video'?'video.mp4':'photo.jpg'};
 }
 if(op==='send'){
  const cid=id(args.cid),body=args.body;if(typeof body!=='string'||!body.trim()||body.length>500)throw Error('TEXT_LIMIT_500');
  const t=await thread(cid);if(!t.members?.nodes?.some(n=>String(n.id)===own))throw Error('NOT_A_THREAD_MEMBER');
  const doc=r('WmiSlideTextMessage')?.WmiSlideTextMessageMutation;if(!doc||typeof env.executeMutation!=='function')throw Error('NATIVE_SEND_UNAVAILABLE');
  if(typeof args.offline_id!=='string'||!/^\d{15,20}$/.test(args.offline_id))throw Error('DURABLE_SEND_ID_REQUIRED');
  const variables={offline_threading_id:args.offline_id,thread_fbid:cid,text_body:{sensitive_string_value:body},send_silently:false,logging_data:r('WmiSlideSendLoggingData').resolveWmiSlideSendLoggingData()};
  const operation=rt.createOperationDescriptor(rt.getRequest(doc),variables,{force:true}),raw=await timeout(env.executeMutation({operation}).toPromise()),packets=Array.isArray(raw)?raw:[raw];
  if(packets.some(p=>p?.errors?.length)||packets.filter(p=>p?.data).length!==1)throw Error('NATIVE_ACK_MISSING');
  const m=packets.find(p=>p?.data)?.data.send_slide_text_message,normalized=m&&normalize(m);if(!normalized?.id||normalized.body!==body||!normalized.own||String(m.as_micg_interface?.thread_fbid)!==cid)throw Error('NATIVE_ACK_MISSING');
  return {id:normalized.id,timestamp:normalized.timestamp,native_ack:true};
 }
 throw Error('UNSUPPORTED_NATIVE_OPERATION');
}
