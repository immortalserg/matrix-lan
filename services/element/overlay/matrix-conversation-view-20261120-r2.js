(()=>{
 'use strict';if(window.__matrixConversationViewLoaded)return;window.__matrixConversationViewLoaded=true;
 const keys=['hideJoinLeaves','hideAvatarChanges','hideDisplaynameChanges','hideRedactions'];let client=null,pending=false,busy=false;
 const sdk=()=>{try{return window.mxMatrixClientPeg?.get?.()||null}catch{return null}};
 const settings=()=>client?.getAccountData?.('im.vector.web.settings')?.getContent?.()||{};
 const shown=()=>keys.some(k=>settings()[k]===false);
 function update(){for(const button of document.querySelectorAll('.mc-conversation-events')){button.textContent='Служебные: '+(shown()?'видны':'скрыты');button.setAttribute('aria-pressed',String(shown()));button.disabled=busy;}}
 async function toggle(event){
  const current=client;if(!current?.getUserId?.()||busy)return;const next=!shown();busy=true;update();
  try{await current.setAccountData('im.vector.web.settings',{...settings(),...Object.fromEntries(keys.map(k=>[k,!next]))});}
  catch{event.currentTarget.textContent='Не удалось сохранить. Повторить';}
  finally{busy=false;update();}
 }
 function render(){
  const current=sdk();if(current!==client){client?.removeListener?.('accountData',update);client=current;client?.on?.('accountData',update);}
  if(!client?.getUserId?.())return;
  for(const toolbar of document.querySelectorAll('.mc-chat-shortcuts')){
   if(toolbar.querySelector('.mc-conversation-events'))continue;
   const button=document.createElement('button');button.type='button';button.className='mc-secondary mc-conversation-events';button.title='Показать или скрыть вступления, выходы, смену имени и аватара, а также строки «Сообщение удалено». Сообщения и ошибки доставки остаются видны.';button.addEventListener('click',toggle);toolbar.append(button);
  }
  update();
 }
 function schedule(){if(pending)return;pending=true;requestAnimationFrame(()=>{pending=false;render()})}
 function start(){new MutationObserver(records=>{if(records.some(r=>[...r.addedNodes].some(n=>n.nodeType===1)))schedule()}).observe(document.getElementById('matrixchat')||document.body,{subtree:true,childList:true});render();}
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
