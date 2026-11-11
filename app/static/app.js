const POLL_INTERVAL_MS = 3000;
const ARCHIVE_NAME = "llms.zip";

const form = document.getElementById("form");
const input = document.getElementById("url");
const button = document.getElementById("submit");
const loading = document.getElementById("loading");
const errorBox = document.getElementById("error");
const doneBox = document.getElementById("done");
const doneText = document.getElementById("done-text");
const downloadLink = document.getElementById("download");
const fileRows = [...document.querySelectorAll("#files li")].map((row) => ({
  box: row.querySelector("input"),
  status: row.querySelector(".status"),
}));

let fileUrl = null;
let chosen = []; // имена файлов, выбранных для текущей генерации

// Подпись у каждого выбранного файла: «генерируется», «сгенерирован» или пусто.
function setFileStatus(text) {
  for (const row of fileRows) {
    row.status.textContent = chosen.includes(row.box.value) ? text : "";
  }
}

// state: "idle" | "loading" | "error" | "done"
function show(state, text) {
  loading.hidden = state !== "loading";
  errorBox.hidden = state !== "error";
  doneBox.hidden = state !== "done";
  errorBox.textContent = state === "error" ? text : "";
  const busy = state === "loading";
  button.disabled = busy;
  input.disabled = busy;
  for (const row of fileRows) row.box.disabled = busy;
  if (state === "loading") setFileStatus("генерируется");
  else if (state === "done") setFileStatus("сгенерирован");
  else setFileStatus("");
}

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

function offerFile(blob) {
  const isArchive = chosen.length > 1;
  if (fileUrl) URL.revokeObjectURL(fileUrl);
  fileUrl = URL.createObjectURL(blob);
  downloadLink.href = fileUrl;
  downloadLink.download = isArchive ? ARCHIVE_NAME : chosen[0];
  doneText.textContent = isArchive ? "Ваш архив готов" : "Ваш файл готов";
  show("done");
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
    if (response.ok) offerFile(await response.blob());
    else show("error", errorText(null));
    return;
  }

  const data = await response.json();
  if (data.status === "processing") {
    setTimeout(() => poll(taskId), POLL_INTERVAL_MS);
  } else {
    show("error", errorText(data));
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = input.value.trim();
  const files = fileRows.filter((row) => row.box.checked).map((row) => row.box.value);
  const problem = checkUrl(url) || (files.length ? null : "Выберите, что сгенерировать.");
  if (problem) {
    show("error", problem);
    return;
  }

  chosen = files;
  show("loading");
  try {
    const response = await fetch("/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, files }),
    });
    const data = await response.json();
    if (!response.ok || !data.task_id) {
      show("error", errorText(data));
      return;
    }
    setTimeout(() => poll(data.task_id), POLL_INTERVAL_MS);
  } catch {
    show("error", "Не удалось связаться с сервисом. Проверьте интернет и попробуйте ещё раз.");
  }
});
