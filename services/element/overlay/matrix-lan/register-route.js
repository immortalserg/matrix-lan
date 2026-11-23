/* Matrix-LAN registration embedded in the Element registration route. */
(() => {
  "use strict";
  let panel, previousFocus;
  function close() { if(panel){panel.remove();panel=null;previousFocus?.focus();} }
  async function open() {
    if(panel)return;
    previousFocus=document.activeElement;
    panel=document.createElement("div");
    panel.setAttribute("role","dialog");
    panel.setAttribute("aria-modal","true");
    panel.setAttribute("aria-label","Matrix-LAN registration");
    Object.assign(panel.style,{position:"fixed",inset:"0",zIndex:"10000",background:"#10151c",color:"#fff",display:"grid",placeItems:"center",overflow:"auto"});
    panel.innerHTML='<form style="width:min(420px,90vw);display:grid;gap:14px"><h2>Matrix-LAN — Регистрация</h2><p role="status">Проверка сервера…</p><label>Имя пользователя<input name="username" required autocomplete="username" style="display:block;width:100%;padding:10px;box-sizing:border-box"></label><label>Пароль<input name="password" type="password" minlength="12" required autocomplete="new-password" style="display:block;width:100%;padding:10px;box-sizing:border-box"></label><label hidden>Ключ приглашения<input name="invite" autocomplete="off" style="display:block;width:100%;padding:10px;box-sizing:border-box"></label><button type="submit" disabled>Создать учётную запись</button><button type="button">Вернуться ко входу</button></form>';
    document.body.append(panel);
    const form=panel.querySelector("form"), msg=form.querySelector("[role=status]"), submit=form.querySelector("[type=submit]");
    form.querySelector("[type=button]").onclick=()=>{location.hash="/login";close();};
    try {
      const response=await fetch("/api/setup/status",{cache:"no-store"});
      if(!response.ok)throw Error("HTTP "+response.status);
      const state=await response.json();
      if(typeof state.initialized!=="boolean")throw Error("Invalid setup status");
      const invite=form.elements.invite;
      invite.parentElement.hidden=!state.initialized;invite.required=state.initialized;
      msg.textContent=state.initialized?"Введите одноразовое приглашение.":"Первый пользователь станет суперпользователем.";
      submit.disabled=false;
      form.onsubmit=async event=>{
        event.preventDefault();submit.disabled=true;
        try {
          const body=Object.fromEntries(new FormData(form));
          const result=await fetch(state.initialized?"/api/auth/register":"/api/setup/register",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
          const data=await result.json();
          if(!result.ok)throw Error(data.error||"HTTP "+result.status);
          form.elements.password.value="";
          location.hash="/login";close();
        }catch(error){msg.textContent=error.message;submit.disabled=false;}
      };
      form.elements.username.focus();
    }catch(error){msg.textContent="Регистрация недоступна: "+error.message;}
  }
  function route(){if(/^#\/?register(?:[/?]|$)/.test(location.hash))open();else close();}
  window.addEventListener("hashchange",route);
  route();
})();
