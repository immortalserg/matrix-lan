"use strict";
const $ = (id) => document.getElementById(id);
let initialized = null;
let token = null;
let bridgePoll = null;
let bridgePolling = false;
function scheduleBridgePoll() {
  clearTimeout(bridgePoll);
  if (!token) return;
  bridgePoll = setTimeout(async () => {
    const session = token;
    const panel = $("bridge-inventory");
    if (!document.hidden && panel && !bridgePolling && session) {
      bridgePolling = true;
      try {
        const catalog = await api("/api/admin/bridges");
        if (session === token && panel.isConnected) {
          for (const bridge of catalog.bridges) {
            const row = document.getElementById("bridge-status-" + bridge.id);
            if (row && panel.contains(row)) row.updateBridgeStatus?.(bridge);
          }
        }
      } catch (_) {
        if (session === token) {
          for (const row of panel.querySelectorAll("[data-bridge-status]"))
            row.markBridgeStale?.();
        }
      } finally { bridgePolling = false; }
    }
    if (session === token) scheduleBridgePoll();
  }, 5000);
}
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
    scheduleBridgePoll();
    notice("Права суперпользователя подтверждены.");
  } catch (error) { token = null; $("admin-content").hidden = true; notice("Ошибка входа: " + error.message, true); }
});
$("logout").addEventListener("click", async () => {
  const previous = token;
  token = null;
  clearTimeout(bridgePoll);
  $("bridge-inventory")?.replaceChildren();
  $("account-inventory")?.remove();
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
async function refreshAccounts() {
  const session = token;
  if (!session) return;
  let panel = $("account-inventory");
  if (!panel) {
    panel = document.createElement("section");
    panel.id = "account-inventory";
    $("admin-content").append(panel);
  }
  panel.replaceChildren();
  const heading = document.createElement("h2");
  heading.textContent = "Подключённые аккаунты";
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "Обновить список";
  button.addEventListener("click", refreshAccounts);
  button.disabled = true;
  const output = document.createElement("div");
  output.style.overflowX = "auto";
  output.textContent = "Загрузка…";
  panel.append(heading, button, output);
  try {
    const data = await api("/api/admin/bridge-accounts");
    if (session !== token || !output.isConnected) return;
    output.replaceChildren();
    if (!data.accounts.length) {
      output.textContent = "В реестре пока нет аккаунтов.";
      return;
    }
    const table = document.createElement("table");
    table.style.cssText = "width:100%;border-collapse:collapse;text-align:left;overflow-wrap:anywhere";
    const head = table.createTHead().insertRow();
    for (const label of ["Мессенджер", "Аккаунт", "Владелец", "Последний статус", "Проверено", "Мост"]) {
      const cell = document.createElement("th");
      cell.scope = "col"; cell.textContent = label; cell.style.padding = "6px";
      head.append(cell);
    }
    const body = table.createTBody();
    for (const account of data.accounts) {
      const row = body.insertRow();
      const runtime = account.bridge_runtime;
      for (const value of [account.bridge, account.login_id, account.owner_mxid,
        account.account_status || "unknown",
        account.status_checked_at ? new Date(account.status_checked_at).toLocaleString() : "Нет данных",
        runtime?.checked_at ? runtime.state + " / " + runtime.health + " (" + new Date(runtime.checked_at).toLocaleString() + ")" : "Нет данных"]) {
        const cell = row.insertCell();
        cell.textContent = value;
        cell.style.cssText = "padding:6px;border-top:1px solid #526277";
      }
    }
    output.append(table);
  } catch (error) {
    if (session === token && output.isConnected) output.textContent = "Реестр недоступен: " + error.message;
  } finally { button.disabled = false; }
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
      row.id = "bridge-status-" + bridge.id;
      row.dataset.bridgeStatus = "";
      const state = document.createElement("span");
      function showState(current) {
        const runtime = current.runtime;
        const fresh = runtime && Date.now() - Date.parse(runtime.checked_at) < 30000;
        state.textContent = runtime ? " — " + runtime.state + " / " + runtime.health +
          (fresh ? "" : " (устаревшие данные)") + " (" + new Date(runtime.checked_at).toLocaleString() + ")" :
          " — Нет данных менеджера";
        if (current.restart) state.textContent += current.restart.pending ?
          " — Перезапуск ожидается #" + current.restart.generation :
          " — Запуск подтверждён #" + current.restart.applied_generation;
      }
      row.updateBridgeStatus = showState;
      row.markBridgeStale = () => { state.textContent = " — Не удалось обновить состояние"; };
      showState(bridge);
      row.append(state);
      if (bridge.available && bridge.prepared) {
        const start = document.createElement("button");
        const stop = document.createElement("button");
        const restart = document.createElement("button");
        const result = document.createElement("span");
        result.setAttribute("aria-live", "polite");
        start.textContent = "Запустить";
        stop.textContent = "Остановить";
        restart.textContent = "Перезапустить";
        let enabled = bridge.desired_enabled === true;
        let busy = false;
        let changedAt = 0;
        row.updateBridgeStatus = current => {
          showState(current);
          if (!busy && Date.now() - changedAt > 5000) {
            enabled = current.desired_enabled === true;
            controls();
          }
        };
        function controls(busy = false) {
          start.disabled = busy || enabled;
          stop.disabled = busy || !enabled;
          restart.disabled = busy || !enabled;
        }
        async function command(action) {
          if (!token || !row.isConnected) return;
          const session = token;
          busy = true;
          controls(true);
          try {
            const base = "/api/admin/bridges/" + encodeURIComponent(bridge.id);
            if (action === "restart") {
              const data = await api(base + "/restart", {method:"POST"});
              if (session !== token || !row.isConnected) return;
              result.textContent = "Перезапуск запрошен (#" + data.generation + ").";
            } else {
              const desired = action === "start";
              await api(base, {method:"PATCH", body:JSON.stringify({enabled:desired})});
              if (session !== token || !row.isConnected) return;
              enabled = desired;
              result.textContent = "Команда сохранена; ожидается применение.";
            }
          } catch (error) {
            if (session === token && row.isConnected) result.textContent = "Ошибка: " + error.message;
          } finally { busy = false; changedAt = Date.now(); controls(); }
        }
        start.addEventListener("click", () => command("start"));
        stop.addEventListener("click", () => command("stop"));
        restart.addEventListener("click", () => command("restart"));
        controls();
        row.append(document.createTextNode(" — "), start, stop, restart, result);
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
        const applied = document.createElement("span");
        applied.setAttribute("aria-live", "polite");
        function showCredentials(current) {
          const credentials = current.credentials;
          applied.textContent = !credentials?.configured ? "API не сохранён в базе" :
            credentials.applied ? "Конфигурация применена: " + new Date(credentials.applied_at).toLocaleString() :
            "Ожидается применение конфигурации";
        }
        const previousUpdate = row.updateBridgeStatus;
        row.updateBridgeStatus = current => {
          previousUpdate(current);
          showCredentials(current);
        };
        showCredentials(bridge);
        form.append(id, hash, save, link, applied);
        form.addEventListener("submit", async event => {
          event.preventDefault(); save.disabled = true;
          try {
            await api("/api/admin/bridges/telegram/credentials", {
              method: "PATCH", body: JSON.stringify({api_id: Number(id.value), api_hash: hash.value})
            });
            hash.value = "";
            applied.textContent = "Ожидается применение конфигурации";
            notice("Telegram API сохранён. Менеджер применит параметры при включении моста.");
          } catch (error) { notice(error.message, true); }
          finally { save.disabled = false; }
        });
        panel.append(form);
      }
    }
  } catch (error) {
    panel.append(document.createTextNode("Каталог недоступен: " + error.message));
  }
  await refreshAccounts();
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
