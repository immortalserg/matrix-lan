async ({op,args={}}) => {
 const own=String(window.vk?.id||'');if(!/^\d+$/.test(own)||own==='0')throw Error('IM_NOT_AUTHENTICATED');
 if(args.expected_id&&args.expected_id!==own)throw Error('WRONG_ACCOUNT');
 if(typeof window.vkApi?.api!=='function')throw Error('VK_API_NOT_READY');
 const state=window.__matrixVkBridgeNative||(window.__matrixVkBridgeNative={next:0,names:{},writable:{},profile:null,profileAt:0});
 const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
 const call=async(method,params)=>{
  await sleep(Math.max(0,state.next-Date.now()));state.next=Date.now()+500;
  try{const value=await Promise.race([window.vkApi.api(method,params),new Promise((_,reject)=>setTimeout(()=>reject(Error('VK_API_TIMEOUT')),25000))]);if(value?.error)throw value.error;return value?.response??value;}
  catch(error){const code=error.code??error.error_code??error.error?.error_code;if(code)throw Error('VK_API_'+code);if(/missing access_token|session expired/i.test(error.message||''))throw Error('VK_AUTH_REQUIRED');throw Error('VK_API_UNAVAILABLE');}
 };
 const peer=value=>{if(typeof value!=='string'||!/^\-?\d+$/.test(value)||!Number.isSafeInteger(Number(value))||Number(value)===0)throw Error('INVALID_PEER_ID');return Number(value);};
 const names=data=>{for(const u of data?.profiles||[])state.names[String(u.id)]=[u.first_name,u.last_name].filter(Boolean).join(' ');for(const g of data?.groups||[])state.names[String(-g.id)]=g.name;};
 const nativeId=m=>{if(Number.isSafeInteger(m?.conversation_message_id)&&m.conversation_message_id>0)return 'cmid:'+m.conversation_message_id;if(Number.isSafeInteger(m?.id)&&m.id>0)return 'mid:'+m.id;throw Error('INVALID_NATIVE_MESSAGE_ID');};
 const normalize=(m,cid)=>{
  if(!m||String(m.peer_id)!==String(cid)||!Number.isSafeInteger(m.date)||!Number.isSafeInteger(m.from_id))throw Error('INVALID_NATIVE_MESSAGE');
  const sender=String(m.from_id),attachments=(m.attachments||[]).flatMap((a,index)=>['photo','audio_message','audio','doc','video','sticker','graffiti'].includes(a.type)?[{index,type:a.type}]:[]);let body=typeof m.text==='string'?m.text:'',kind='text';
  if(!body&&!attachments.length){kind='notice';body=m.action?'Событие в беседе VK':(m.attachments||[]).map(a=>a.type==='call'?'Звонок VK':a.type==='link'?(a.link?.title||'Ссылка')+'\n'+(a.link?.url||''):a.type==='wall'?'Запись VK':'Сообщение VK: откройте оригинал в приложении.').join('\n')||'Сообщение VK';}
  if(!body&&attachments.length)kind='media';
  return {id:nativeId(m),body,kind,attachments,timestamp:m.date*1000,sender,own:m.out===1||sender===own,name:state.names[sender]||sender};
 };
 if(op==='identity'){
  if(!state.profile||Date.now()-state.profileAt>900000){const users=await call('users.get',{user_ids:own});if(!Array.isArray(users)||String(users[0]?.id)!==own)throw Error('WRONG_ACCOUNT');state.profile=users[0];state.profileAt=Date.now();state.names[own]=[users[0].first_name,users[0].last_name].filter(Boolean).join(' ');}
  return {id:own,nick:state.names[own]||'VK · '+own,connection:'connected'};
 }
 if(op==='chats'){
  const all=new Map();let done=false;
  for(let offset=0;offset<5000;offset+=200){const data=await call('messages.getConversations',{offset,count:200,extended:1});if(!Array.isArray(data?.items)||!Number.isSafeInteger(data.count))throw Error('INVALID_CHAT_LIST');names(data);
   for(const item of data.items){const c=item.conversation,last=item.last_message,cid=String(c?.peer?.id||'');peer(cid);state.writable[cid]=c.can_write?.allowed!==false;const title=cid===own?'Избранное':c.chat_settings?.title||state.names[cid]||'VK '+cid;all.set(cid,{id:cid,name:title,is_group:c.peer.type==='chat',last_id:last?nativeId(last):'',modified:Number(c.timestamp||last?.date||0)*1000,last_timestamp:Number(last?.date||0)*1000});}
   if(offset+data.items.length>=data.count||data.items.length<200){done=true;break;}
  }
  if(!done)throw Error('CHAT_BACKLOG_TOO_LARGE');
  if(!all.has(own))all.set(own,{id:own,name:'Избранное',is_group:false,last_id:'',modified:0,last_timestamp:0});state.writable[own]=true;return [...all.values()].sort((a,b)=>b.modified-a.modified);
 }
 if(op==='history'){
  const cid=peer(args.cid),since=Number(args.since||0),all=[];let done=false;
  for(let offset=0;offset<10000;offset+=200){const data=await call('messages.getHistory',{peer_id:cid,offset,count:200,extended:1,rev:0});if(!Array.isArray(data?.items))throw Error('INVALID_NATIVE_HISTORY');names(data);const batch=data.items.map(m=>normalize(m,cid));all.push(...batch.filter(m=>m.timestamp>=since));if(data.items.length<200||batch.some(m=>m.timestamp<since)){done=true;break;}}
  if(!done)throw Error('HISTORY_BACKLOG_TOO_LARGE');const messages=[...new Map(all.map(m=>[m.id,m])).values()].sort((a,b)=>a.timestamp-b.timestamp);return {messages,cursor:Math.max(since,...messages.map(m=>m.timestamp))};
 }
 if(op==='media'){
  const cid=peer(args.cid);if(!/^cmid:\d+$/.test(args.id)||!Number.isInteger(args.index)||args.index<0||args.index>=100)throw Error('INVALID_MEDIA_LOCATOR');
  const data=await call('messages.getByConversationMessageId',{peer_id:cid,conversation_message_ids:args.id.slice(5),extended:0});
  const m=data.items?.find(m=>nativeId(m)===args.id&&String(m.peer_id)===String(cid)),a=m?.attachments?.[args.index];if(!a||a.type!==args.type)throw Error('ATTACHMENT_UNAVAILABLE');
  const largest=items=>[...(items||[])].filter(x=>x?.url).sort((x,y)=>(y.width||0)*(y.height||0)-(x.width||0)*(x.height||0))[0];
  const result=(url,kind,name,extra={})=>{if(typeof url!=='string'||!/^https:\/\//.test(url))throw Error('ATTACHMENT_UNAVAILABLE');return {url,kind,name,...extra};};
  if(a.type==='photo'){const p=largest([...(a.photo.sizes||[]),a.photo.orig_photo]);return result(p?.url,'image','photo.jpg',{width:p?.width,height:p?.height});}
  if(a.type==='audio_message'){const p=a.audio_message;return result(p.link_mp3||p.link_ogg,'audio',p.link_mp3?'voice.mp3':'voice.ogg',{voice:true,duration:p.duration*1000,waveform:p.waveform});}
  if(a.type==='sticker'){const p=largest(a.sticker.images_with_background?.length?a.sticker.images_with_background:a.sticker.images);return result(p?.url,'image','sticker.png');}
  if(a.type==='graffiti')return result(a.graffiti.url,'image','drawing.png');
  if(a.type==='doc')return result(a.doc.url,'file',a.doc.title||'document');
  if(a.type==='audio')return result(a.audio.url,'audio',(a.audio.title||'audio')+'.mp3',{duration:a.audio.duration*1000});
  if(a.type==='video'){const v=a.video;let files=v.files;if(!files){const details=await call('video.get',{videos:v.owner_id+'_'+v.id+(v.access_key?'_'+v.access_key:'')});files=details.items?.[0]?.files;}const key=Object.keys(files||{}).filter(k=>/^mp4_\d+$/.test(k)).sort((a,b)=>Number(b.slice(4))-Number(a.slice(4)))[0];return result(files?.[key],'video',(v.title||'video')+'.mp4',{duration:v.duration*1000});}
  throw Error('ATTACHMENT_UNAVAILABLE');
 }
 if(op==='send'){
  const cid=peer(args.cid),body=args.body,random=args.random_id;if(typeof body!=='string'||!body.trim()||body.length>500)throw Error('TEXT_LIMIT_500');if(!Number.isSafeInteger(random)||random<=0||random>2147483647)throw Error('DURABLE_SEND_ID_REQUIRED');if(state.writable[String(cid)]===false)throw Error('VK_SEND_FORBIDDEN');
  const sent=await call('messages.send',{peer_id:cid,message:body,random_id:random});let id=Number.isSafeInteger(sent)?sent:sent?.message_id,found;
  if(Array.isArray(sent)){if(sent.length!==1||String(sent[0].peer_id)!==String(cid)||sent[0].error)throw Error('NATIVE_ACK_MISSING');id=sent[0].message_id;}
  if(Number.isSafeInteger(id)&&id>0){const data=await call('messages.getById',{message_ids:String(id),extended:1});names(data);found=data.items?.find(m=>m.id===id&&String(m.peer_id)===String(cid));}
  else {const data=await call('messages.getHistory',{peer_id:cid,count:30,extended:1});names(data);const matching=(data.items||[]).filter(m=>m.random_id===random&&String(m.peer_id)===String(cid));if(matching.length===1)found=matching[0];}
  const message=found&&normalize(found,cid);if(!message?.own||message.body!==body||(found.random_id&&found.random_id!==random))throw Error('NATIVE_ACK_MISSING');return {id:message.id,timestamp:message.timestamp,native_ack:true};
 }
 throw Error('UNSUPPORTED_NATIVE_OPERATION');
}
