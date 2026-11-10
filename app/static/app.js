const POLL_INTERVAL_MS = 3000;

const form = document.getElementById("form");
const input = document.getElementById("url");
const button = document.getElementById("submit");
const loading = document.getElementById("loading");
const errorBox = document.getElementById("error");

// state: "idle" | "loading" | "error"
function show(state, text) {
  loading.hidden = state !== "loading";
  errorBox.hidden = state !== "error";
  errorBox.textContent = state === "error" ? text : "";
  button.disabled = state === "loading";
  input.disabled = state === "loading";
}

function checkUrl(value) {
  if (!value) return "Введите адрес сайта.";
  if (!/^https?:\/\//i.test(value)) return "Адрес должен начинаться с http:// или https://";
  try {
    const host = new URL(value).hostname;
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

function saveFile(blob) {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "llms.txt";
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  show("idle");
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
    if (response.ok) saveFile(await response.blob());
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
  const problem = checkUrl(url);
  if (problem) {
    show("error", problem);
    return;
  }

  show("loading");
  try {
    const response = await fetch("/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
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
