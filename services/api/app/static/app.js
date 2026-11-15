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
const form = document.querySelector("#create-form");
const formError = document.querySelector("#form-error");

let selectedId = null;
let pollTimer = null;

function statusClass(status) {
  return `status ${status || ""}`;
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
  tasksEl.replaceChildren();
  if (!body.tasks.length) {
    const empty = document.createElement("li");
    empty.className = "note";
    empty.textContent = "Пока пусто.";
    tasksEl.append(empty);
    return;
  }
  for (const task of body.tasks) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = task.id === selectedId ? "active" : "";
    const name = document.createElement("strong");
    name.textContent = task.event_type;
    const state = document.createElement("div");
    state.className = statusClass(task.status);
    state.textContent = task.status;
    button.append(name, state);
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
  const active = reached.result ? "policy" : order.find((step) => !reached[step]) || "result";
  const effect = policyOf(task)?.effect || "";
  document.querySelectorAll("#flow li").forEach((node) => {
    const step = node.dataset.step;
    node.classList.toggle("done", reached[step] && step !== active);
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

function renderDetail(task, events) {
  markFlow(task);
  detailEl.replaceChildren();
  const title = document.createElement("div");
  title.className = "row";
  const name = document.createElement("strong");
  name.textContent = task.event_type;
  const state = document.createElement("span");
  state.className = statusClass(task.status);
  state.textContent = task.status;
  title.append(name, state);
  const meta = document.createElement("p");
  meta.className = "meta";
  meta.textContent = `Кэш: ${task.cache || "—"} · ${task.id}`;
  const policy = policyOf(task);
  const decision = document.createElement("section");
  decision.className = `decision ${policy?.effect || ""}`;
  const decisionName = document.createElement("strong");
  decisionName.textContent = policy?.effect || "Policy ещё не решила";
  const decisionAction = document.createElement("p");
  decisionAction.textContent = policy
    ? `Действие: ${policy.action || "—"} · исполнение не запускалось`
    : "Цепочка ещё не дошла до Policy.";
  decision.append(decisionName, decisionAction);
  const analyst = stepResult(task, "analyst");
  const security = stepResult(task, "security");
  const result = document.createElement("pre");
  result.textContent = JSON.stringify(
    {
      analyst: analyst && { summary: analyst.summary, facts: analyst.facts, risk: analyst.risk },
      security: security && { summary: security.summary, proposed_action: security.proposed_action, risk: security.risk },
      policy,
    },
    null,
    2,
  );
  const audit = document.createElement("div");
  audit.className = "audit";
  for (const event of events) {
    const row = document.createElement("article");
    const line = document.createElement("div");
    line.className = "meta";
    line.textContent = event.event_type;
    row.append(line);
    audit.append(row);
  }
  const actions = document.createElement("div");
  actions.className = "actions";
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "danger";
  remove.textContent = "Удалить";
  remove.addEventListener("click", () => deleteTask(task.id));
  actions.append(remove);
  detailEl.append(title, meta, decision, result, audit, actions);
}

async function selectTask(id) {
  selectedId = id;
  clearTimeout(pollTimer);
  await refreshSelected();
  await loadTasks();
}

async function refreshSelected() {
  if (!selectedId) return;
  const [taskResponse, auditResponse] = await Promise.all([
    fetch(`/tasks/${selectedId}`),
    fetch(`/tasks/${selectedId}/audit`),
  ]);
  if (!taskResponse.ok) {
    selectedId = null;
    detailEl.textContent = "Задача не найдена.";
    markFlow(null);
    return;
  }
  const task = await taskResponse.json();
  const audit = await auditResponse.json();
  renderDetail(task, audit.events || []);
  if (task.status === "pending" || task.status === "processing") {
    pollTimer = setTimeout(refreshSelected, 1000);
  }
}

async function deleteTask(id) {
  await fetch(`/tasks/${id}`, { method: "DELETE" });
  if (selectedId === id) {
    selectedId = null;
    detailEl.textContent = "Задача удалена.";
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
});

document.querySelector("#refresh").addEventListener("click", loadTasks);
loadChecks();
loadTasks();
setInterval(loadChecks, 10000);
