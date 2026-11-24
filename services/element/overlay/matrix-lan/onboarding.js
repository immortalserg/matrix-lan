"use strict";
const $ = (id) => document.getElementById(id);
let initialized = null;
let token = null;
function notice(message, error = false) {
  $("notice").textContent = message;
  $("notice").className = error ? "error" : "success";
}
async function api(path, options = {}) {
  const headers = {"Content-Type": "application/json", ...(token ? {"Authorization": "Bearer " + token} : {})};
  const response = await fetch(path, {cache:"no-store", ...options, headers:{...headers, ...(options.headers || {})}});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "HTTP " + response.status);
  return data;
}
async function status() {
  try {
    initialized = (await api("/api/setup/status")).initialized;
    $("register").hidden = false;
    $("registration-title").textContent = initialized ? "Регистрация по приглашению" : "Создать первого суперпользователя";
    $("invite-field").hidden = !initialized;
    $("invite").required = initialized;
    notice(initialized ? "Для регистрации требуется одноразовое приглашение." : "Пользователей пока нет. Первый зарегистрированный станет суперпользователем.");
  } catch (error) { notice("Сервер регистрации недоступен: " + error.message, true); }
}
$("registration-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  try {
    const username = $("username").value;
    const password = $("password").value;
    const path = initialized ? "/api/auth/register" : "/api/setup/register";
    const data = await api(path, {method:"POST", body:JSON.stringify({username,password,invite:$("invite").value})});
    $("password").value = "";
    $("invite").value = "";
    notice("Создан аккаунт " + data.user_id + ". Теперь можно войти в Element.");
    await status();
  } catch (error) { notice(error.message, true); }
  finally { button.disabled = false; }
});
$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const data = await api("/_matrix/client/v3/login", {method:"POST", body:JSON.stringify({
      type:"m.login.password", identifier:{type:"m.id.user",user:$("admin-user").value},
      password:$("admin-pass").value, initial_device_display_name:"Matrix-LAN admin"
    })});
    $("admin-pass").value = "";
    token = data.access_token;
    await refresh();
    $("admin-content").hidden = false;
    notice("Права суперпользователя подтверждены.");
  } catch (error) { token = null; $("admin-content").hidden = true; notice("Ошибка входа: " + error.message, true); }
});
$("logout").addEventListener("click", async () => {
  const previous = token;
  token = null;
  $("bridge-inventory")?.replaceChildren();
  $("admin-content").hidden = true;
  if (previous) {
    try { await fetch("/_matrix/client/v3/logout", {method:"POST", headers:{Authorization:"Bearer " + previous}}); } catch (_) {}
  }
  notice("Вы вышли из панели управления.");
});
function logViewer(bridge) {
  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = "Журнал — " + bridge.label;
  const refreshButton = document.createElement("button");
  refreshButton.type = "button";
  refreshButton.textContent = "Обновить журнал";
  const output = document.createElement("pre");
  output.style.cssText = "max-height:360px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;";
  output.setAttribute("aria-live", "polite");
  const auto = document.createElement("input");
  auto.type = "checkbox";
  const autoLabel = document.createElement("label");
  autoLabel.append(auto, document.createTextNode(" Автообновление (10 секунд)"));
  let timer = null;
  let generation = 0;
  function schedule() {
    clearTimeout(timer);
    if (auto.checked && details.open && details.isConnected && token) {
      timer = setTimeout(() => {
        if (details.open && details.isConnected && token && auto.checked) {
          if (document.hidden || refreshButton.disabled) schedule();
          else load();
        }
      }, 10000);
    }
  }
  async function load() {
    clearTimeout(timer);
    if (!token || !details.open || !details.isConnected || refreshButton.disabled) return;
    const session = token;
    const requestId = ++generation;
    refreshButton.disabled = true;
    if (!output.textContent) output.textContent = "Загрузка…";
    try {
      const data = await api("/api/admin/bridges/" + encodeURIComponent(bridge.id) + "/logs");
      if (session !== token || requestId !== generation || !details.open || !details.isConnected) return;
      output.textContent = (data.truncated ? "[Последние 32 КиБ]\n" : "") + (data.log || "Журнал пуст.");
    } catch (error) {
      if (session === token && requestId === generation && details.open && details.isConnected)
        output.textContent = "Журнал недоступен: " + error.message;
    } finally {
      if (requestId === generation) {
        refreshButton.disabled = false;
        schedule();
      }
    }
  }
  details.addEventListener("toggle", () => {
    if (details.open) load();
    else { clearTimeout(timer); generation++; output.textContent = ""; refreshButton.disabled = false; }
  });
  refreshButton.addEventListener("click", load);
  auto.addEventListener("change", schedule);
  details.append(summary, refreshButton, autoLabel, output);
  return details;
}
async function refresh() {
  const [users, invites] = await Promise.all([api("/api/admin/users"),api("/api/admin/invites")]);
  let panel = $("bridge-inventory");
  if (!panel) {
    panel = document.createElement("section");
    panel.id = "bridge-inventory";
    $("admin-content").append(panel);
  }
  panel.replaceChildren();
  const heading = document.createElement("h2");
  heading.textContent = "Сервисы подключения";
  panel.append(heading);
  try {
    const catalog = await api("/api/admin/bridges");
    for (const bridge of catalog.bridges) {
      const row = document.createElement("p");
      row.textContent = bridge.label + " — " + (!bridge.available ? "Не реализован" : bridge.prepared ? "Подготовлен (работоспособность не проверена)" : "Не подготовлен");
      if (bridge.runtime) {
        const state = document.createElement("span");
        state.textContent = " — Docker: " + bridge.runtime.state + " / " + bridge.runtime.health + " (проверено " + new Date(bridge.runtime.checked_at).toLocaleString() + ")";
        row.append(state);
      }
      if (bridge.available) {
        const toggle = document.createElement("input");
        toggle.type = "checkbox";
        toggle.checked = bridge.desired_enabled === true;
        const label = document.createElement("label");
        label.append(toggle, document.createTextNode(" Включить (желаемое состояние)"));
        toggle.addEventListener("change", async () => {
          const desired = toggle.checked;
          toggle.disabled = true;
          try {
            await api("/api/admin/bridges/" + encodeURIComponent(bridge.id), {
              method: "PATCH", body: JSON.stringify({enabled: desired})
            });
            notice("Настройка сохранена. Контроллер применит её при следующей проверке, если он установлен.");
          } catch (error) {
            toggle.checked = !desired;
            notice(error.message, true);
          } finally { toggle.disabled = false; }
        });
        row.append(document.createTextNode(" — "), label);
      }
      panel.append(row, logViewer(bridge));
      if (bridge.id === "telegram") {
        const form = document.createElement("form");
        const id = document.createElement("input");
        id.type = "number"; id.min = "1"; id.max = "2147483647"; id.required = true;
        id.placeholder = "API ID"; id.value = bridge.credentials?.api_id || "";
        const hash = document.createElement("input");
        hash.type = "password"; hash.required = true; hash.pattern = "[a-fA-F0-9]{32}";
        hash.placeholder = bridge.credentials?.configured ? "Новый API Hash" : "API Hash";
        hash.autocomplete = "new-password";
        const save = document.createElement("button");
        save.textContent = "Сохранить Telegram API";
        const link = document.createElement("a");
        link.href = "https://my.telegram.org"; link.target = "_blank"; link.rel = "noopener noreferrer";
        link.textContent = "Получить API ID и API Hash";
        form.append(id, hash, save, link);
        form.addEventListener("submit", async event => {
          event.preventDefault(); save.disabled = true;
          try {
            await api("/api/admin/bridges/telegram/credentials", {
              method: "PATCH", body: JSON.stringify({api_id: Number(id.value), api_hash: hash.value})
            });
            hash.value = "";
            notice("Telegram API сохранён. Применение конфигурации пока не реализовано.");
          } catch (error) { notice(error.message, true); }
          finally { save.disabled = false; }
        });
        panel.append(form);
      }
    }
  } catch (error) {
    panel.append(document.createTextNode("Каталог недоступен: " + error.message));
  }
  $("users").replaceChildren();
  for (const user of users.users) {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = user.mxid + " — ";
    const select = document.createElement("select");
    for (const role of ["user","admin","superuser"]) {
      const option = document.createElement("option");
      option.value = role; option.textContent = role; option.selected = user.role === role;
      select.append(option);
    }
    select.addEventListener("change", async () => {
      const previous = user.role;
      if (!confirm("Изменить роль " + user.mxid + " на " + select.value + "?")) {select.value=previous;return;}
      try {
        await api("/api/admin/users/" + user.mxid + "/role",
          {method:"PATCH",body:JSON.stringify({role:select.value})});
        user.role = select.value;
        notice("Роль изменена.");
      } catch (error) { select.value=previous; notice(error.message,true); }
    });
    li.append(name,select); $("users").append(li);
  }
  $("invites").replaceChildren();
  for (const invite of invites.invites) {
    const li = document.createElement("li");
    li.textContent = "#" + invite.id + " — до " + invite.expires_at + (invite.consumed?" (использовано)":invite.revoked?" (отозвано)":"");
    if (!invite.consumed && !invite.revoked) {
      const button = document.createElement("button");
      button.textContent = "Отозвать";
      button.addEventListener("click",async()=>{try{
        await api("/api/admin/invites/"+invite.id,{method:"DELETE"});await refresh();
      }catch(error){notice(error.message,true);}});
      li.append(button);
    }
    $("invites").append(li);
  }
}
$("refresh").addEventListener("click",()=>refresh().catch(e=>notice(e.message,true)));
$("invite-form").addEventListener("submit",async(event)=>{
  event.preventDefault();
  try {
    const data=await api("/api/admin/invites",{method:"POST",body:JSON.stringify({hours:Number($("hours").value)})});
    $("new-invite").textContent="Одноразовый ключ (сохраните сейчас): "+data.token;
    await refresh();
  } catch(error){notice(error.message,true);}
});
status();
