const themeButton = document.querySelector("#theme");
const savedTheme = localStorage.getItem("sentinel-theme");
const initialTheme = savedTheme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("sentinel-theme", theme);
  themeButton.textContent = theme === "dark" ? "Светлая" : "Тёмная";
}

applyTheme(initialTheme);
themeButton.addEventListener("click", () => {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
});

const tasksEl = document.querySelector("#tasks");
const detailEl = document.querySelector("#detail");
const checksEl = document.querySelector("#checks");
const countEl = document.querySelector("#run-count");
const form = document.querySelector("#create-form");
const formError = document.querySelector("#form-error");
const submitButton = form.querySelector("button[type=submit]");

const STATUS_LABEL = {
  pending: "в очереди",
  processing: "в работе",
  done: "готово",
  failed: "ошибка",
};

const RISK_LABEL = {
  low: "низкий",
  medium: "средний",
  high: "высокий",
};

const ACTION_LABEL = {
  record_only: "только запись",
  block_host: "блокировка хоста",
  isolate_host: "изоляция хоста",
};

const AUDIT_LABEL = {
  "task.created": "Событие принято",
  "task.completed": "Проверка завершена",
  "task.publish_failed": "Не удалось поставить в очередь",
  "task.updated": "Задача изменена",
  "task.delivery_failed": "Доставка не удалась",
  "policy.allow": "Policy разрешила",
  "policy.deny": "Policy запретила",
  "policy.require_approval": "Policy запросила подтверждение",
};

let selectedId = null;
let listedStatus = null;
let pollTimer = null;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function showView(name) {
  document.querySelector("#view-check").hidden = name !== "check";
  document.querySelector("#view-runs").hidden = name !== "runs";
  document.querySelectorAll(".nav-btn").forEach((button) => {
    const on = button.dataset.view === name;
    button.classList.toggle("active", on);
    if (on) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
}

document.querySelectorAll(".nav-btn").forEach((button) => {
  button.addEventListener("click", () => showView(button.dataset.view));
});

function statusClass(status) {
  return `status ${status || ""}`;
}

function statusText(status) {
  return STATUS_LABEL[status] || status || "—";
}

function formatTime(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function actionText(action) {
  if (!action) return "не указано";
  const gloss = ACTION_LABEL[action];
  return gloss ? `${action} · ${gloss}` : action;
}

function showEmpty(title, text) {
  detailEl.replaceChildren();
  const box = el("div", "empty");
  box.append(el("strong", "", title), el("p", "", text));
  detailEl.append(box);
  markFlow(null);
}

async function loadChecks() {
  const response = await fetch("/ready");
  const body = await response.json();
  const checks = body.checks || {};
  checksEl.replaceChildren(
    ...Object.entries(checks).map(([name, value]) => {
      const pill = document.createElement("span");
      pill.className = `pill ${value === "ok" ? "ok" : "bad"}`;
      pill.textContent = `${name}: ${value}`;
      return pill;
    }),
  );
}

async function loadTasks() {
  const response = await fetch("/tasks");
  const body = await response.json();
  const tasks = body.tasks || [];
  countEl.hidden = false;
  countEl.textContent = String(tasks.length);
  tasksEl.replaceChildren();
  if (!tasks.length) {
    const empty = document.createElement("li");
    empty.className = "note";
    empty.textContent = "Пока пусто.";
    tasksEl.append(empty);
    return;
  }
  for (const task of tasks) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    if (task.id === selectedId) button.className = "active";
    const top = el("div", "task-top");
    top.append(el("strong", "", task.event_type));
    const effect = task.policy?.effect;
    if (effect) top.append(el("span", `effect-pill ${effect}`, effect));
    const sub = el("div", "task-sub");
    sub.append(el("span", statusClass(task.status), statusText(task.status)));
    sub.append(el("span", "task-time", formatTime(task.created_at)));
    button.append(top, sub);
    button.addEventListener("click", () => selectTask(task.id));
    item.append(button);
    tasksEl.append(item);
  }
}

function policyOf(task) {
  return (task.results || []).find((item) => item.agent === "policy")?.result || task.policy || null;
}

function markFlow(task) {
  document.querySelectorAll("#flow li").forEach((node) => {
    node.classList.remove("active", "done", "allow", "deny", "approval");
  });
  if (!task) return;
  const present = new Set((task.results || []).map((item) => item.agent));
  const reached = {
    event: true,
    analyst: present.has("analyst") || task.status === "done",
    security: present.has("security") || task.status === "done",
    policy: Boolean(policyOf(task)) || task.status === "done",
    result: task.status === "done" || task.status === "failed",
  };
  const order = ["event", "analyst", "security", "policy", "result"];
  const active = reached.result ? "result" : order.find((step) => !reached[step]) || "result";
  const effect = policyOf(task)?.effect || "";
  document.querySelectorAll("#flow li").forEach((node) => {
    const step = node.dataset.step;
    node.classList.toggle("done", Boolean(reached[step]) && step !== active);
    node.classList.toggle("active", step === active);
    if (step !== "policy") return;
    node.classList.toggle("allow", effect === "ALLOW");
    node.classList.toggle("deny", effect === "DENY");
    node.classList.toggle("approval", effect === "REQUIRE_APPROVAL");
  });
}

function stepResult(task, agent) {
  return (task.results || []).find((item) => item.agent === agent)?.result || null;
}

function stepCard(title, result, extra) {
  const card = el("article", "step-card");
  const head = document.createElement("header");
  head.append(el("span", "step-name", title));
  if (result?.risk) {
    head.append(el("span", `risk ${result.risk}`, RISK_LABEL[result.risk] || result.risk));
  }
  card.append(head);
  if (!result) {
    card.append(el("p", "note", "Ещё нет ответа."));
    return card;
  }
  if (result.summary) card.append(el("p", "summary", result.summary));
  extra(card, result);
  return card;
}

function renderDetail(task, events) {
  markFlow(task);
  detailEl.replaceChildren();

  const title = el("div", "row result-head");
  title.append(el("h3", "result-title", task.event_type));
  title.append(el("span", statusClass(task.status), statusText(task.status)));

  const meta = el("p", "meta", `${formatTime(task.created_at)} · кэш ${task.cache || "—"}`);
  const idLine = el("p", "id-line", task.id);

  const policy = policyOf(task);
  const decision = el("section", `decision ${policy?.effect || "wait"}`);
  if (policy?.effect) {
    decision.append(el("strong", "", policy.effect));
    const executed = policy.executed ? "исполнение отмечено как запущенное" : "исполнение не запускалось";
    decision.append(el("p", "", `Действие: ${actionText(policy.action)} · ${executed}`));
  } else if (task.status === "failed") {
    decision.append(el("strong", "", "Проверка не завершилась"));
    decision.append(el("p", "", "Цепочка остановилась до решения Policy."));
  } else {
    decision.append(el("strong", "", "Policy ещё не решила"));
    decision.append(el("p", "", "Результат обновится сам, как только цепочка дойдёт до конца."));
  }

  const steps = el("div", "block");
  steps.append(el("h3", "section-label", "Разбор"));
  const grid = el("div", "steps");
  grid.append(stepCard("Analyst", stepResult(task, "analyst"), (card, result) => {
    if (result.facts?.length) {
      const facts = el("ul", "facts");
      for (const fact of result.facts) facts.append(el("li", "", String(fact)));
      card.append(facts);
    }
  }));
  grid.append(stepCard("Security", stepResult(task, "security"), (card, result) => {
    const line = el("p", "step-line");
    line.append(el("span", "", "Предложение: "));
    line.append(document.createTextNode(actionText(result.proposed_action)));
    card.append(line);
  }));
  steps.append(grid);

  const input = el("div", "block");
  input.append(el("h3", "section-label", "Входные данные"));
  const pairs = Object.entries(task.payload || {}).filter(([, value]) => value !== null && typeof value !== "object");
  if (pairs.length) {
    const dl = el("dl", "kv");
    for (const [key, value] of pairs) {
      dl.append(el("dt", "", key), el("dd", "", String(value)));
    }
    input.append(dl);
  }
  const payloadFold = el("details", "fold");
  if (!pairs.length) payloadFold.open = true;
  payloadFold.append(el("summary", "", "Полный JSON"));
  const payloadPre = document.createElement("pre");
  payloadPre.textContent = JSON.stringify(task.payload ?? {}, null, 2);
  payloadFold.append(payloadPre);
  input.append(payloadFold);

  const journal = el("div", "block");
  journal.append(el("h3", "section-label", "Журнал"));
  const audit = el("div", "audit");
  if (!events.length) {
    journal.append(el("p", "note", "Записей пока нет."));
  } else {
    for (const event of events) {
      const row = document.createElement("article");
      const body = document.createElement("div");
      const line = el("div", "row");
      line.append(el("span", "audit-title", AUDIT_LABEL[event.event_type] || event.event_type));
      line.append(el("span", "task-time", formatTime(event.created_at)));
      body.append(line);
      const details = event.details || {};
      const extra = [details.action, details.effect, details.reason].filter(Boolean).join(" · ");
      if (extra) body.append(el("p", "meta audit-extra", extra));
      row.append(body);
      audit.append(row);
    }
    journal.append(audit);
  }

  const raw = el("details", "fold");
  raw.append(el("summary", "", "Полный ответ"));
  const rawPre = document.createElement("pre");
  rawPre.textContent = JSON.stringify(
    {
      analyst: stepResult(task, "analyst"),
      security: stepResult(task, "security"),
      policy,
    },
    null,
    2,
  );
  raw.append(rawPre);

  const actions = el("div", "actions");
  const remove = el("button", "danger", "Удалить");
  remove.type = "button";
  remove.addEventListener("click", () => deleteTask(task.id));
  actions.append(remove);

  detailEl.append(title, meta, idLine, decision, steps, input, journal, raw, actions);
}

async function selectTask(id) {
  selectedId = id;
  listedStatus = null;
  clearTimeout(pollTimer);
  showView("runs");
  await refreshSelected();
}

async function refreshSelected() {
  if (!selectedId) return;
  const [taskResponse, auditResponse] = await Promise.all([
    fetch(`/tasks/${selectedId}`),
    fetch(`/tasks/${selectedId}/audit`),
  ]);
  if (!taskResponse.ok) {
    selectedId = null;
    listedStatus = null;
    showEmpty("Задача не найдена", "Её уже нет в списке запусков.");
    await loadTasks();
    return;
  }
  const task = await taskResponse.json();
  const audit = await auditResponse.json();
  renderDetail(task, audit.events || []);
  if (task.status !== listedStatus) {
    listedStatus = task.status;
    await loadTasks();
  }
  if (task.status === "pending" || task.status === "processing") {
    pollTimer = setTimeout(refreshSelected, 1000);
  }
}

async function deleteTask(id) {
  await fetch(`/tasks/${id}`, { method: "DELETE" });
  if (selectedId === id) {
    selectedId = null;
    listedStatus = null;
    clearTimeout(pollTimer);
    showEmpty("Задача удалена", "Выберите другой запуск в списке.");
  }
  await loadTasks();
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  formError.hidden = true;
  let payload;
  try {
    payload = JSON.parse(document.querySelector("#payload").value);
  } catch {
    formError.hidden = false;
    formError.textContent = "Данные должны быть объектом JSON.";
    return;
  }
  submitButton.disabled = true;
  try {
    const response = await fetch("/tasks", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        event_type: document.querySelector("#event-type").value,
        payload,
      }),
    });
    if (!response.ok) {
      formError.hidden = false;
      formError.textContent = "Сервис не принял событие.";
      return;
    }
    const created = await response.json();
    await selectTask(created.id);
  } finally {
    submitButton.disabled = false;
  }
});

document.querySelector("#refresh").addEventListener("click", loadTasks);
loadChecks();
loadTasks();
setInterval(loadChecks, 10000);
