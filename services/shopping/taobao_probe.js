async () => {
 const s=window._imsdk;if(!s)return null;
 const origin=s.getOriginSdk(),cs=s.getConversationService(),ms=origin.getMsgService(),auth=s.getAuthService();
 const keys=o=>o?[...new Set([...Object.keys(o),...Object.getOwnPropertyNames(Object.getPrototypeOf(o)||{})])]:[];
 const identity=o=>Object.fromEntries(keys(o).filter(k=>/^(userId|uid|nick|nickname|currentUserId|currentUser|userInfo|loginUser|identifier|selfId|user)$/i.test(k)).map(k=>[k,typeof o[k]==='object'?Object.fromEntries(Object.entries(o[k]||{}).filter(([x,y])=>/^(uid|id|nick|nickname|userId|name|domain)$/i.test(x)&&['string','number'].includes(typeof y))):['string','number'].includes(typeof o[k])?o[k]:typeof o[k]]));
 const convs=await new Promise((resolve,reject)=>{const t=setTimeout(()=>reject(new Error('conversation timeout')),10000);cs.listAllConversation({dataCallback:r=>{clearTimeout(t);resolve(r)},errorCallBack:()=>{clearTimeout(t);reject(new Error('conversation failed'))}})});
 if(!Array.isArray(convs))return {shape:typeof convs,keys:Object.keys(convs||{})};
 const out=[];
 for(const c of convs.slice(0,4)){
  const cid=c.conversationCode||c.cid,res=await ms.listPrevMsgs(cid,String(Date.now()+1000000000),8);
  out.push({cid,keys:Object.keys(c),name:c.ext?.target?.dnick||c.conversationContent?.conversationName,identity:identity(c),target:c.ext?.target,last:c.conversationContent?.lastMessageSummary,historyKeys:Object.keys(res||{}),messages:(res?.userMessageModels||[]).slice(-4).map(m=>({modelKeys:Object.keys(m),message:m.message}))});
 }
 return {authKeys:keys(auth),authIdentity:identity(auth),originKeys:keys(origin),originIdentity:identity(origin),sdkIdentity:identity(s),contextIdentity:identity(s.context),baseIdentity:identity(s.context?.base),sendTextSource:ms.sendTextMessage?.toString().slice(0,4000),sendSource:ms.sendMessage?.toString().slice(0,2000),wrapperSend:s.getMessageService()?.sendTextMessage?.toString().slice(0,3000),wrapperMethods:keys(s.getMessageService()).filter(k=>typeof s.getMessageService()[k]==='function'),count:convs.length,chats:out};
}
