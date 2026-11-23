// Injected only in the bridge's isolated browser. These names are checked against
// the exact client version before this hook is appended to the main module.
;(()=>{
 const api=window.__matrixIMO={version:1,events:[],sequence:0,qr:null,
  status:()=>({uid:Z().uid||'',name:Z().nickname||'',phone_suffix:String(Z().verifiedPhone||'').slice(-4),ready:!!Z().inited,connected:!!Z().uid&&!!je().connected}),
  network:()=>({connected:je().connected,connecting:je().connecting,reconnectCount:je().reconnectCount,endpoint:je().wsUrl}),
  timestamp:()=>as(),
  recent:async(id)=>await Vt.getChatsRecentMessages({from_end:true,limit:100,buid_range_map:{[id]:{start_ts:'0',end_ts:as()}}}),
  sessions:()=>ce().sessionList.map(s=>({id:s.buid,name:s.note||s.name||s.buid,type:s.type,is_group:s.buid.endsWith(';'),is_private:!!s.isPrivacyChat,is_left:!!s.isLeftGroup,last_ts:s.lastMessage?.timestampNanoString||'',last:s.lastMessage?.msg||''})),
  contacts:()=>wn().contactList.map(c=>({id:c.buid,name:c.note||c.name||c.buid,type:c.buid.endsWith(';')?'group':'normal',is_group:c.buid.endsWith(';'),is_private:!!c.isPrivacyChat,is_left:!!c.isLeftGroup})),
  cached:(id)=>[
   ...(Me().unreadMessageMap[id]||[]).map(m=>({...m,buid:id})),
   ...(Me().chatMessageMap[id]||[]).map(m=>({buid:id,msg:m.msg,msg_id:m.msgId,author:m.authorId,author_alias:m.authorAlias,is_sent:m.from===1,timestamp_nano_str:m.timestampNanoString,imdata:m.imData||{type:m.type}}))
  ],
  history:async(id,start,end)=>await Vt.getHistoryMessages({buid:id,from_end:true,proto:'imo',limit:100,start_time:start||'0',end_time:end||String(BigInt(Date.now())*1000000n)}),
  send:async(id,text,msgid)=>await Vt.sendImMessages({buid:id,msg:text,imdata:{type:'im',msg_id:msgid,trace_id:msgid+'matrix'}}),
  cancelQR:async()=>{if(api.qr)await Po.invalidateQrCode({qr_link:api.qr.data});api.qr=null;},
 };
 const qr=Po.requestQrCode;
 Po.requestQrCode=async(...args)=>{try{const res=await qr(...args);api.qrStatus={status:res?.status,keys:Object.keys(res?.result||{}),error:res?.error};if(res?.result?.qr_link)api.qr={data:res.result.qr_link,expires:Date.now()+Number(res.result.code_ttl||60)*1000};return res;}catch(e){api.qrStatus={error:String(e).slice(0,200)};throw e;}};
 const capture=(kind,payload)=>{api.events.push({seq:++api.sequence,kind,data:payload.edata||{}});};
 api.startCapture=()=>{
  if(api.capturing)return;api.capturing=true;
  Vt.onPushReceive(p=>capture('receive',p));
  Vt.onPushReceiveUnreadMessages(p=>{for(const [buid,batch] of Object.entries(p.edata||{}))for(const m of batch.msgs||[])capture('receive',{edata:{...m,buid}});});
  Vt.onPushReflect(p=>capture('reflect',p));
  Vt.onPushMessageAcked(p=>capture('delivered',p));
  Vt.onPushMessageSeen(p=>capture('seen',p));
 };
})();

// Uses the same native media download/upload stack as this pinned IMO client.
;(()=>{
 const api=window.__matrixIMO,jobs=new Map();let serial=0;
 const kind=t=>t?.startsWith('photo_')?'image':t?.startsWith('video_')?'video':t?.startsWith('audio_')?'audio':'file';
 const decode=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));
 const encode=bytes=>{let text='';for(let i=0;i<bytes.length;i+=32768)text+=String.fromCharCode(...bytes.subarray(i,i+32768));return btoa(text)};
 async function locate(buid,ts){
  let message=api.cached(buid).find(m=>m.timestamp_nano_str===ts);
  const refs=message?.imdata?Yi().extractMediaUrl(message.imdata):null;
  if(!refs||!Object.values(refs).some(Boolean)){
   const rows=await api.history(buid,String(BigInt(ts)-1n),String(BigInt(ts)+1n));
   message=rows.find(m=>m.timestamp_nano_str===ts);
  }
  if(!message?.imdata)throw new Error('Media message is unavailable');
  return message.imdata;
 }
 api.mediaBeginDownload=({buid,ts,limit})=>{
  if(!/^\d+$/.test(ts)||limit<1)throw new Error('Invalid media request');
  const handle='download-'+(++serial),job={state:'loading',imdata:null,blob:null};jobs.set(handle,job);
  void(async()=>{
   try{
    const data=await locate(buid,ts);job.imdata=data;
    if(Number(data.file_size||data.objects?.[0]?.filesize||0)>limit)throw new Error('File exceeds configured size limit');
    const transfer=el(data);await transfer.onDownload({userInitiated:true});
    const url=Yi().getCachedUrl(data);
    if(!url)throw new Error(transfer.info.status===Te.EXPIRED?'Media expired on IMO':'IMO could not download this attachment');
    const response=await fetch(url);const blob=await response.blob();
    if(!blob.size||blob.size>limit)throw new Error('Invalid attachment size');
    job.blob=blob;job.info={size:blob.size,mime:blob.type||Yi().getMediaMineType(data)||'application/octet-stream',name:data.file_name||data.objects?.[0]?.filename||'',kind:kind(data.type),width:data.width||data.objects?.[0]?.type_specific_params?.width||0,height:data.height||data.objects?.[0]?.type_specific_params?.height||0};job.state='ready';
   }catch(e){job.state='error';job.error=String(e.message||'Media download failed').replace(/https?:\/\/\S+/g,'[media]').slice(0,160);}
  })();return handle;
 };
 api.mediaPoll=handle=>{const j=jobs.get(handle);return j?{state:j.state,info:j.info||null,error:j.error||null,result:j.result||null}:null};
 api.mediaRead=async({handle,offset,length})=>{
  const j=jobs.get(handle);if(!j?.blob||offset<0||length<1||length>1048576)throw new Error('Invalid chunk request');
  return encode(new Uint8Array(await j.blob.slice(offset,offset+length).arrayBuffer()));
 };
 api.mediaRelease=handle=>{
  const j=jobs.get(handle);
  if(j?.imdata){const key=jr(j.imdata);if(key.transferKey)uf(key.transferKey);Yi().destroyPreviewUrl(j.imdata);}
  jobs.delete(handle);
 };
 api.mediaStage=({name,mime,size,limit,meta=null,cover=null})=>{
  if(size<1||size>limit)throw new Error('Invalid upload size');
  const handle='upload-'+(++serial);jobs.set(handle,{state:'staging',parts:[],size,loaded:0,name,mime,meta,cover});return handle;
 };
 api.mediaAppend=({handle,data})=>{
  const j=jobs.get(handle);if(!j||j.state!=='staging')throw new Error('Unknown upload');
  const bytes=decode(data);if(j.loaded+bytes.length>j.size)throw new Error('Upload exceeds announced size');j.parts.push(bytes);j.loaded+=bytes.length;
 };
 api.mediaSend=({handle,buid,msgid,msgtype})=>{
  const j=jobs.get(handle);if(!j||j.state!=='staging'||j.loaded!==j.size)throw new Error('Incomplete upload');
  j.state='preparing';
  void(async()=>{
   try{
    let file=new File(j.parts,j.name,{type:j.mime});j.parts=[];let cover=null,meta=null;
    let type=msgtype==='m.image'?'photo_uploaded':msgtype==='m.video'?'video_uploaded':msgtype==='m.audio'?'audio_uploaded':'bigo_uploaded';
    if(file.size>32*1024*1024)type='bigo_uploaded';
    if(type==='photo_uploaded'){meta=await pa().imageProcess(file,false);file=meta?.file||file;cover=meta?.cover||null;}
    if(type==='video_uploaded'){if(j.meta){meta=j.meta;cover=j.cover?new File([decode(j.cover)],'cover.jpg',{type:'image/jpeg'}):null;}else{meta=await pa().videoProcess(file);file=meta?.file||file;cover=meta?.cover||null;}}
    const width=meta?.videoWidth||meta?.imageWidth||0,height=meta?.videoHeight||meta?.imageHeight||0,duration=meta?.duration||0;
    const data={type,file_name:file.name,ext:file.name.split('.').pop(),file_size:file.size,width,height,msg_id:msgid,trace_id:msgid+'matrix',objects:[{type_specific_params:{width,height,original_width:width,original_height:height,duration,mime:file.type}}],quality:{duration:duration*1000}};
    const files=[{file,serviceType:Hi.UPLOAD_IM,mediaType:p6(type)}];if(cover)files.push({file:cover,serviceType:Hi.UPLOAD_IM,mediaType:Xt.THUMB});
    const ext={[Dd.REQ_KEY_IMO_EXT_DATA]:JSON.stringify({uid:Z().uid,client_info:{udid:je().getUdid(),'full-user-agent':od(),'user-agent':am(),signup_cc:Z().cc,file_trace_id:data.trace_id},object_type:Xu(type),stream_id:hc(buid),imdata:data,task_id:msgid+'_matrix',ssid:je().getSsid()})};
    j.state='sending';const task=si.uploadBatch({serviceType:Id.IMO_NOTIFY_SIGNALING_BACKEND,fileInfoList:files,extInfos:ext});j.task=task;
    task.on('success',result=>{
     try{
      const response=JSON.parse(result.extInfos[Bd.RESULT_KEY_IMO_EXT_DATA]);
      const value={...(response?.result?.files||{}),...(response?.result?.object_data||{})};
      if(response?.status==='failed'||!value.timestamp_nano_str)throw new Error('IMO did not confirm attachment');
      j.result={timestamp_nano_str:value.timestamp_nano_str};j.state='complete';j.task=null;
     }catch(e){j.state='error';j.error='IMO did not confirm attachment';}
    });
    task.on('error',()=>{j.state='error';j.error='IMO attachment transfer failed';});
   }catch(e){j.state='error';j.error=String(e.message||'Media upload failed').replace(/https?:\/\/\S+/g,'[media]').slice(0,160);}
  })();return {started:true};
 };
})();
