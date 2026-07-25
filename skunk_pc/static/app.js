const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
}[char]));

async function api(path, options = {}) {
  const method = options.method || "GET";
  const headers = new Headers(options.headers || {});
  if (!["GET", "HEAD"].includes(method.toUpperCase())) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const response = await fetch(path, { ...options, method, headers });
  let data = {};
  try { data = await response.json(); } catch (_) {}
  if (response.status === 401) {
    window.location.href = "/login";
    throw new Error("Sesión expirada");
  }
  if (!response.ok) {
    throw new Error(data.detail || data.message || "La operación no pudo completarse");
  }
  return data;
}

let toastTimer;
function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => element.classList.remove("visible"), 3600);
}

function statusBadge(printer) {
  if (!printer.connected || printer.state === "disconnected") {
    return '<span class="status-pill danger">Desconectada</span>';
  }
  if (printer.state === "stopped") {
    return '<span class="status-pill warning">Requiere atención</span>';
  }
  if (printer.state === "printing") {
    return '<span class="status-pill ready">Imprimiendo</span>';
  }
  return '<span class="status-pill ready">Lista</span>';
}

function renderPrinters(printers) {
  const grid = $("#printer-grid");
  const select = $("#printer-select");
  select.innerHTML = '<option value="">Selecciona una impresora</option>';
  if (!printers.length) {
    grid.innerHTML = '<div class="empty-card">No hay impresoras Zebra configuradas.</div>';
    return;
  }
  grid.innerHTML = printers.map((printer) => `
    <article class="printer-card">
      <div class="printer-card-head">
        <div>
          <h3>${escapeHtml(printer.description || printer.name)}</h3>
          <p class="printer-model">${escapeHtml(printer.make_model)}</p>
        </div>
        ${statusBadge(printer)}
      </div>
      <div class="printer-uri">${escapeHtml(printer.uri)}</div>
      <div class="printer-specs">
        <span>${escapeHtml(printer.language.toUpperCase())}</span>
        <span>${escapeHtml(printer.dpi)} DPI</span>
        <span>${escapeHtml(printer.page_size)}</span>
        <span>${printer.media_type === "thermal" ? "CON RIBBON" : "TÉRMICA DIRECTA"}</span>
      </div>
      <div class="printer-actions">
        <button class="btn btn-secondary" data-action="test" data-printer="${escapeHtml(printer.name)}">Prueba</button>
        <button class="btn btn-quiet" data-action="calibrate" data-printer="${escapeHtml(printer.name)}">Calibrar</button>
        <button class="btn btn-warning repair" data-action="repair" data-printer="${escapeHtml(printer.name)}">Reparar y fijar 4×6</button>
      </div>
    </article>
  `).join("");

  for (const printer of printers.filter((item) => item.connected)) {
    const option = document.createElement("option");
    option.value = printer.name;
    option.textContent = printer.description || printer.name;
    select.append(option);
  }
}

function renderJobs(jobs) {
  const body = $("#jobs-body");
  if (!jobs.length) {
    body.innerHTML = '<tr><td colspan="5" class="muted">Sin trabajos todavía.</td></tr>';
    return;
  }
  body.innerHTML = jobs.map((job) => {
    const date = new Date(job.created_at);
    const error = job.error ? ` title="${escapeHtml(job.error)}"` : "";
    return `
      <tr>
        <td>${escapeHtml(job.original_name)}</td>
        <td>${escapeHtml(job.printer_name)}</td>
        <td${error}><span class="job-state ${escapeHtml(job.status)}">${escapeHtml(job.status)}</span></td>
        <td>${escapeHtml(job.pages)}</td>
        <td>${date.toLocaleString("es-CL", { dateStyle: "short", timeStyle: "short" })}</td>
      </tr>`;
  }).join("");
}

async function refresh() {
  try {
    const [status, printers, jobs] = await Promise.all([
      api("/api/status"),
      api("/api/printers"),
      api("/api/jobs?limit=30")
    ]);
    $("#cups-badge").textContent = status.cups ? "CUPS ACTIVO" : "CUPS CAÍDO";
    $("#cups-badge").className = `status-pill ${status.cups ? "ready" : "danger"}`;
    $("#metric-printers").textContent = status.zebra_count;
    $("#metric-ready").textContent = `${status.ready_count} listas para imprimir`;
    $("#metric-foreign").textContent = status.foreign_count;
    renderPrinters(printers.printers);
    renderJobs(jobs.jobs);
  } catch (error) {
    toast(error.message);
  }
}

$("#document")?.addEventListener("change", (event) => {
  const file = event.target.files?.[0];
  $("#file-label").textContent = file ? file.name : "Toca para seleccionar un archivo";
});

for (const eventName of ["dragenter", "dragover"]) {
  $("#drop-zone")?.addEventListener(eventName, (event) => {
    event.preventDefault();
    event.currentTarget.classList.add("dragging");
  });
}
for (const eventName of ["dragleave", "drop"]) {
  $("#drop-zone")?.addEventListener(eventName, (event) => {
    event.preventDefault();
    event.currentTarget.classList.remove("dragging");
    if (eventName === "drop" && event.dataTransfer.files.length) {
      $("#document").files = event.dataTransfer.files;
      $("#file-label").textContent = event.dataTransfer.files[0].name;
    }
  });
}

$("#print-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = $("#print-submit");
  const message = $("#print-message");
  submit.disabled = true;
  submit.textContent = "Procesando…";
  message.textContent = "";
  message.className = "form-message";
  try {
    const data = await api("/api/jobs", {
      method: "POST",
      body: new FormData(event.currentTarget)
    });
    message.textContent = `Trabajo recibido: ${data.job.original_name}`;
    message.classList.add("success");
    event.currentTarget.reset();
    $("#file-label").textContent = "Toca para seleccionar un archivo";
    await refresh();
  } catch (error) {
    message.textContent = error.message;
    message.classList.add("error");
  } finally {
    submit.disabled = false;
    submit.textContent = "Imprimir ahora";
  }
});

$("#printer-grid")?.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const action = button.dataset.action;
  const printer = button.dataset.printer;
  const labels = { test: "prueba", calibrate: "calibración", repair: "reparación" };
  if (action === "calibrate" && !window.confirm("La impresora avanzará varias etiquetas. ¿Continuar?")) return;
  button.disabled = true;
  try {
    const data = await api(`/api/printers/${encodeURIComponent(printer)}/${action}`, { method: "POST" });
    toast(data.message || `${labels[action]} completada`);
    await refresh();
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
});

function closeAddPrinterDialog() {
  const dialog = $("#add-printer-dialog");
  const form = $("#add-printer-form");
  form?.reset();
  const message = $("#add-message");
  if (message) {
    message.textContent = "";
    message.className = "form-message";
  }
  if (dialog?.open) dialog.close();
}

document.querySelectorAll("[data-close-add-printer]").forEach((button) => {
  button.addEventListener("click", closeAddPrinterDialog);
});

$("#add-printer-dialog")?.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeAddPrinterDialog();
});

$("#add-printer-button")?.addEventListener("click", async () => {
  const dialog = $("#add-printer-dialog");
  const select = $("#device-select");
  $("#add-printer-form").reset();
  $("#add-message").textContent = "";
  $("#add-message").className = "form-message";
  select.innerHTML = '<option value="">Buscando dispositivos…</option>';
  dialog.showModal();
  try {
    const data = await api("/api/devices");
    select.innerHTML = "";
    for (const device of data.devices) {
      const option = document.createElement("option");
      option.value = device.uri;
      option.textContent = `${device.model} · S/N ${device.serial || "sin serie"}`;
      select.append(option);
    }
    if (!data.devices.length) {
      select.innerHTML = '<option value="">No hay Zebra USB disponible</option>';
    }
  } catch (error) {
    $("#add-message").textContent = error.message;
  }
});

$("#add-printer-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const message = $("#add-message");
  try {
    const data = await api("/api/printers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.fromEntries(form))
    });
    toast(data.message || "Impresora creada");
    closeAddPrinterDialog();
    await refresh();
  } catch (error) {
    message.textContent = error.message;
    message.className = "form-message error";
  }
});

$("#refresh-button")?.addEventListener("click", refresh);

$("#theme-toggle")?.addEventListener("click", () => {
  const current = document.documentElement.dataset.theme || "dark";
  const next = current === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("skunk-theme", next);
});

const savedTheme = localStorage.getItem("skunk-theme");
if (savedTheme) document.documentElement.dataset.theme = savedTheme;

if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

document.querySelector("form[action='/logout']")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  await api("/logout", { method: "POST" });
  window.location.href = "/login";
});

refresh();
setInterval(refresh, 6000);
