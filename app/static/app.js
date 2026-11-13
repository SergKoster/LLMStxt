const POLL_INTERVAL_MS = 3000;
const ARCHIVE_NAME = "llms.zip";

const $ = (id) => document.getElementById(id);

const analyzeForm = $("analyze-form");
const input = $("url");
const analyzeButton = $("analyze");
const analyzeLoading = $("analyze-loading");
const analyzeError = $("analyze-error");

const result = $("result");
const noAdvice = $("no-advice");
const reportToggle = $("report-toggle");
const adviceCount = $("advice-count");
const adviceList = $("advice"); // весь отчёт: обе группы
const noImportant = $("no-important");
const importantGroup = $("important-group");
const importantList = $("important-list");
const possibleGroup = $("possible-group");
const possibleList = $("possible-list");

const generateForm = $("generate-form");
const filesList = $("files");
const generateButton = $("generate");
const generateLoading = $("generate-loading");
const generateError = $("generate-error");
const doneBox = $("done");
const doneText = $("done-text");
const downloadLink = $("download");
const instructionNote = $("instruction-note");
const sitemapNote = $("sitemap-note");

let analysis = null; // ответ POST /analyze для текущего адреса
let fileRows = []; // {box, status} для каждого файла
let chosen = []; // файлы, выбранные для текущей генерации
let fileUrl = null;

function setError(box, text) {
  box.hidden = !text;
  box.textContent = text || "";
}

function element(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  for (const child of children) node.append(child);
  return node;
}

// --- Шаг 1: анализ ---

function checkUrl(value) {
  if (!value) return "Введите адрес сайта.";
  // Без схемы можно: сервер сам подставит https:// или http://
  const hasScheme = /^[a-z][a-z0-9+.-]*:\/\//i.test(value);
  if (hasScheme && !/^https?:\/\//i.test(value)) return "Адрес должен начинаться с http:// или https://";
  try {
    const host = new URL(hasScheme ? value : `https://${value}`).hostname;
    if (!host.includes(".")) return "Адрес записан с ошибкой.";
  } catch {
    return "Адрес записан с ошибкой.";
  }
  return null;
}

function errorText(data) {
  if (data && typeof data.error === "string") return data.error;
  return "Что-то пошло не так. Попробуйте ещё раз.";
}

function setAnalyzing(busy) {
  analyzeLoading.hidden = !busy;
  analyzeButton.disabled = busy;
  input.disabled = busy;
}

function resetResult() {
  analysis = null;
  result.hidden = true;
  setGenerating("idle");
}

analyzeForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = input.value.trim();
  const problem = checkUrl(url);
  resetResult();
  setError(analyzeError, problem);
  if (problem) return;

  setAnalyzing(true);
  try {
    const response = await fetch("/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const data = await response.json();
    if (!response.ok || data.status !== "done") {
      setError(analyzeError, errorText(data));
      return;
    }
    analysis = data;
    showResult(data);
  } catch {
    setError(analyzeError, "Не удалось связаться с сервисом. Проверьте интернет и попробуйте ещё раз.");
  } finally {
    setAnalyzing(false);
  }
});

// Адрес поменяли — результат анализа относится к старому сайту.
input.addEventListener("input", () => {
  if (analysis) resetResult();
});

// --- Отчёт с советами ---

function setExpanded(button, panel, expanded) {
  button.setAttribute("aria-expanded", String(expanded));
  panel.hidden = !expanded;
}

function adviceItem(item, index) {
  const panelId = `advice-${index}`;
  const button = element("button", { type: "button", className: "toggle advice-title", textContent: item.title });
  button.setAttribute("aria-controls", panelId);
  const panel = element("div", { id: panelId, className: "advice-details" });
  for (const line of item.details) panel.append(element("p", { textContent: line }));
  if (item.code) panel.append(element("code", { textContent: item.code }));
  setExpanded(button, panel, false);
  button.addEventListener("click", () => setExpanded(button, panel, panel.hidden));
  return element("li", {}, [button, panel]);
}

// Группа советов: «важное» (ошибка очевидна) или «возможное» (могло быть сделано нарочно).
function fillGroup(group, list, items, offset) {
  list.replaceChildren(...items.map((item, i) => adviceItem(item, offset + i)));
  group.hidden = items.length === 0;
  setExpanded(group.querySelector(".group-title"), list, true); // группа сначала развёрнута
}

function showReport(advice) {
  const important = advice.filter((item) => item.group === "important");
  const possible = advice.filter((item) => item.group !== "important");
  fillGroup(importantGroup, importantList, important, 0);
  fillGroup(possibleGroup, possibleList, possible, important.length);
  noImportant.hidden = important.length > 0; // важного нет, но есть возможное
  const any = advice.length > 0;
  noAdvice.hidden = any;
  reportToggle.hidden = !any;
  adviceCount.textContent = any ? `(${advice.length})` : "";
  setExpanded(reportToggle, adviceList, any); // отчёт сначала развёрнут
}

reportToggle.addEventListener("click", () => setExpanded(reportToggle, adviceList, adviceList.hidden));
for (const [group, list] of [[importantGroup, importantList], [possibleGroup, possibleList]]) {
  const button = group.querySelector(".group-title");
  button.addEventListener("click", () => setExpanded(button, list, list.hidden));
}

// --- Шаг 2: выбор файлов ---

function closeTips(except) {
  for (const tip of filesList.querySelectorAll(".info-wrap.open")) if (tip !== except) tip.classList.remove("open");
}

function fileRow(file, index) {
  const box = element("input", { type: "checkbox", name: "files", value: file.name, checked: file.recommended });
  const label = element("label", {}, [box, ` ${file.name}`]);

  // Кнопка с пояснением: подсказка при наведении, фокусе и по нажатию (телефон).
  const tipId = `about-${index}`;
  const tip = element("span", { id: tipId, className: "tip", textContent: file.about });
  tip.setAttribute("role", "tooltip");
  const info = element("button", { type: "button", className: "info", textContent: "?" });
  info.setAttribute("aria-label", `Что такое ${file.name}`);
  info.setAttribute("aria-describedby", tipId);
  const infoWrap = element("span", { className: "info-wrap" }, [info, tip]);
  info.addEventListener("click", () => {
    closeTips(infoWrap);
    infoWrap.classList.toggle("open");
  });
  const children = [label, infoWrap];
  if (file.recommended) children.push(element("span", { className: "badge", textContent: "рекомендуем" }));
  const status = element("span", { className: "status" });
  children.push(status, element("div", { className: "reason", textContent: file.reason }));

  fileRows.push({ box, status });
  return element("li", {}, children);
}

function showResult(data) {
  showReport(data.advice);
  fileRows = [];
  filesList.replaceChildren(...data.files.map(fileRow));
  result.hidden = false;
}

document.addEventListener("click", (event) => {
  if (!event.target.closest(".info-wrap")) closeTips(null);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeTips(null);
});

// --- Генерация ---

// Подпись у каждого выбранного файла: «генерируется», «сгенерирован» или пусто.
function setFileStatus(text) {
  for (const row of fileRows) row.status.textContent = chosen.includes(row.box.value) ? text : "";
}

// state: "idle" | "loading" | "error" | "done"
function setGenerating(state, text) {
  generateLoading.hidden = state !== "loading";
  doneBox.hidden = state !== "done";
  setError(generateError, state === "error" ? text : "");
  const busy = state === "loading";
  generateButton.disabled = busy;
  analyzeButton.disabled = busy;
  input.disabled = busy;
  for (const row of fileRows) row.box.disabled = busy;
  if (state === "loading") setFileStatus("генерируется");
  else if (state === "done") setFileStatus("сгенерирован");
  else setFileStatus("");
}

// partialPages — сколько страниц в неполной карте сайта (заголовок X-Sitemap-Partial), иначе null
function offerFile(blob, partialPages) {
  const isArchive = chosen.length > 1;
  if (fileUrl) URL.revokeObjectURL(fileUrl);
  fileUrl = URL.createObjectURL(blob);
  downloadLink.href = fileUrl;
  downloadLink.download = isArchive ? ARCHIVE_NAME : chosen[0];
  doneText.textContent = isArchive ? "Ваш архив готов" : "Ваш файл готов";
  instructionNote.hidden = !isArchive;
  instructionNote.textContent = isArchive ? `Инструкция — в файле ${analysis.instruction} внутри архива.` : "";
  sitemapNote.hidden = !partialPages;
  sitemapNote.textContent = partialPages ? analysis.sitemap_partial_note.replace("{pages}", partialPages) : "";
  setGenerating("done");
}

async function poll(taskId) {
  let response;
  try {
    response = await fetch(`/status/${taskId}`);
  } catch {
    // Сеть моргнула — пробуем ещё раз на следующем шаге.
    setTimeout(() => poll(taskId), POLL_INTERVAL_MS);
    return;
  }

  const type = response.headers.get("content-type") || "";
  if (!type.includes("application/json")) {
    if (response.ok) offerFile(await response.blob(), response.headers.get("x-sitemap-partial"));
    else setGenerating("error", errorText(null));
    return;
  }

  const data = await response.json();
  if (data.status === "processing") {
    setTimeout(() => poll(taskId), POLL_INTERVAL_MS);
  } else {
    setGenerating("error", errorText(data));
  }
}

generateForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!analysis) return;
  const files = fileRows.filter((row) => row.box.checked).map((row) => row.box.value);
  if (!files.length) {
    setGenerating("error", "Выберите, что сгенерировать.");
    return;
  }

  chosen = files;
  setGenerating("loading");
  try {
    const response = await fetch("/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: analysis.url, files }), // адрес уже со схемой, которую выбрал анализ
    });
    const data = await response.json();
    if (!response.ok || !data.task_id) {
      setGenerating("error", errorText(data));
      return;
    }
    setTimeout(() => poll(data.task_id), POLL_INTERVAL_MS);
  } catch {
    setGenerating("error", "Не удалось связаться с сервисом. Проверьте интернет и попробуйте ещё раз.");
  }
});
