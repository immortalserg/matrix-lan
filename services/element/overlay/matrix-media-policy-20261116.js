(()=>{
 'use strict';if(window.__matrixMediaPolicyLoaded)return;window.__matrixMediaPolicyLoaded=true;
 function render(){
  const toolbar=document.querySelector('.mx_RoomView .mc-chat-shortcuts');if(!toolbar||toolbar.querySelector('.mc-media-policy'))return;
  const note=document.createElement('small');note.className='mc-media-policy';note.textContent='Вложения: 2 месяца';
  note.title='Фото, видео и файлы во всех переписках Matrix удаляются с сервера через два календарных месяца. Текстовая история сохраняется. Для сохранения файла надолго скачайте его на своё устройство.';
  note.style.cssText='font:inherit;font-size:11px;line-height:1.4;margin-left:auto;opacity:.72;white-space:nowrap;';toolbar.append(note);
 }
 function start(){let pending=false;new MutationObserver(()=>{if(pending)return;pending=true;requestAnimationFrame(()=>{pending=false;render()})}).observe(document.getElementById('matrixchat')||document.body,{subtree:true,childList:true});render();}
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
