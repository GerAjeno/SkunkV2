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
  const historySelect = $("#jobs-printer");
  const selectedHistoryPrinter = historySelect?.value || "";
  select.innerHTML = '<option value="">Selecciona una impresora</option>';
  if (historySelect) {
    historySelect.innerHTML = '<option value="">Todas</option>';
  }
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
        <button class="btn btn-secondary" data-action="diagnose" data-printer="${escapeHtml(printer.name)}">Diagnosticar</button>
        <button class="btn btn-warning" data-action="purge" data-printer="${escapeHtml(printer.name)}">Vaciar trabajos</button>
        <button class="btn btn-warning" data-action="repair" data-printer="${escapeHtml(printer.name)}">Reparar y fijar 4×6</button>
        <button class="btn btn-danger" data-action="delete" data-printer="${escapeHtml(printer.name)}">Eliminar impresora</button>
      </div>
    </article>
  `).join("");

  for (const printer of printers.filter((item) => item.connected)) {
    const option = document.createElement("option");
    option.value = printer.name;
    option.textContent = printer.description || printer.name;
    select.append(option);
    if (historySelect) {
      const historyOption = document.createElement("option");
      historyOption.value = printer.name;
      historyOption.textContent = printer.description || printer.name;
      historySelect.append(historyOption);
    }
  }
  if (historySelect) historySelect.value = selectedHistoryPrinter;
}

let jobsPage = 1;

function renderJobs(data) {
  const jobs = data.jobs;
  const body = $("#jobs-body");
  if (!jobs.length) {
    body.innerHTML = '<tr><td colspan="6" class="muted">Sin trabajos todavía.</td></tr>';
  } else {
    body.innerHTML = jobs.map((job) => {
      const date = new Date(job.created_at);
      const error = job.error ? `<div class="job-error">${escapeHtml(job.error)}</div>` : "";
      const labels = {
        queued: "En cola",
        processing: "Procesando",
        submitted: "Enviado",
        completed: "Completado",
        failed: "Error",
        cancelled: "Cancelado"
      };
      return `
        <tr>
          <td>${escapeHtml(job.original_name)}</td>
          <td>${escapeHtml(job.source_device || "Origen no informado")}</td>
          <td>${escapeHtml(job.printer_name)}</td>
          <td><span class="job-state ${escapeHtml(job.status)}">${escapeHtml(labels[job.status] || job.status)}</span>${error}</td>
          <td>${escapeHtml(job.pages)}</td>
          <td>${date.toLocaleString("es-CL", {
            year: "2-digit",
            month: "2-digit",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
            hourCycle: "h23"
          })}</td>
        </tr>`;
    }).join("");
  }
  jobsPage = data.page;
  $("#jobs-summary").textContent = `${data.total} trabajo${data.total === 1 ? "" : "s"}`;
  $("#jobs-page-label").textContent = `Página ${data.page} de ${data.pages}`;
  $("#jobs-previous").disabled = data.page <= 1;
  $("#jobs-next").disabled = data.page >= data.pages;
}

function jobsQuery() {
  const query = new URLSearchParams({
    page: String(jobsPage),
    page_size: $("#jobs-page-size").value,
  });
  const printer = $("#jobs-printer").value;
  const result = $("#jobs-result").value;
  const origin = $("#jobs-origin").value.trim();
  const dateFrom = $("#jobs-date-from").value;
  const dateTo = $("#jobs-date-to").value;
  if (printer) query.set("printer", printer);
  if (result) query.set("result", result);
  if (origin) query.set("origin", origin);
  if (dateFrom) {
    query.set("created_after", new Date(`${dateFrom}T00:00:00`).toISOString());
  }
  if (dateTo) {
    const before = new Date(`${dateTo}T00:00:00`);
    before.setDate(before.getDate() + 1);
    query.set("created_before", before.toISOString());
  }
  return query.toString();
}

let refreshInProgress = false;
async function refresh() {
  if (refreshInProgress) return;
  refreshInProgress = true;
  try {
    const [status, printers, jobs] = await Promise.all([
      api("/api/status"),
      api("/api/printers"),
      api(`/api/jobs?${jobsQuery()}`)
    ]);
    $("#cups-badge").textContent = status.cups ? "CUPS ACTIVO" : "CUPS CAÍDO";
    $("#cups-badge").className = `status-pill ${status.cups ? "ready" : "danger"}`;
    $("#metric-printers").textContent = status.zebra_count;
    $("#metric-ready").textContent = `${status.ready_count} listas para imprimir`;
    $("#metric-foreign").textContent = status.foreign_count;
    renderPrinters(printers.printers);
    renderJobs(jobs);
  } catch (error) {
    toast(error.message);
  } finally {
    refreshInProgress = false;
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
  const form = event.currentTarget;
  const submit = $("#print-submit");
  const message = $("#print-message");
  submit.disabled = true;
  submit.textContent = "Procesando…";
  message.textContent = "";
  message.className = "form-message";
  try {
    const data = await api("/api/jobs", {
      method: "POST",
      body: new FormData(form)
    });
    const copies = data.job.copies === 1 ? "1 copia" : `${data.job.copies} copias`;
    message.textContent = `Trabajo recibido: ${data.job.original_name} · ${copies}`;
    message.className = "form-message success";
    form.reset();
    $("#file-label").textContent = "Toca para seleccionar un archivo";
    await refresh();
  } catch (error) {
    message.textContent = error.message;
    message.className = "form-message error";
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
  const labels = {
    test: "prueba",
    calibrate: "calibración",
    diagnose: "diagnóstico",
    purge: "vaciado",
    repair: "reparación",
    delete: "eliminación"
  };
  if (
    action === "calibrate" &&
    !window.confirm("La impresora avanzará varias etiquetas. ¿Continuar?")
  ) return;
  if (
    action === "purge" &&
    !window.confirm(
      `Se cancelarán todos los trabajos pendientes de ${printer}. ` +
      "La impresora y su configuración permanecerán. ¿Continuar?"
    )
  ) return;
  if (
    action === "delete" &&
    !window.confirm(
      `Se eliminará ${printer}, su cola y toda su configuración de CUPS. ` +
      "Esta acción no elimina otras impresoras. ¿Continuar?"
    )
  ) return;
  button.disabled = true;
  try {
    const method = action === "delete" ? "DELETE" : "POST";
    const path = action === "delete"
      ? `/api/printers/${encodeURIComponent(printer)}`
      : `/api/printers/${encodeURIComponent(printer)}/${action}`;
    const data = await api(path, { method });
    if (action === "diagnose") {
      $("#diagnostic-title").textContent = `Diagnóstico · ${printer}`;
      $("#diagnostic-result").textContent = data.message;
      $("#diagnostic-dialog").showModal();
    } else {
      toast(data.message || `${labels[action]} completada`);
    }
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
      const serial = device.serial_available
        ? `S/N ${device.serial}`
        : "S/N no informado";
      const status = device.installed_queue
        ? `YA INSTALADA: ${device.installed_queue}`
        : "DISPONIBLE";
      option.textContent = `${device.model} · ${serial} · ${status}`;
      option.disabled = Boolean(device.installed_queue);
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
  const submit = $("#add-printer-submit");
  submit.disabled = true;
  submit.textContent = "Creando…";
  message.textContent = "Configurando la impresora…";
  message.className = "form-message";
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
  } finally {
    submit.disabled = false;
    submit.textContent = "Crear impresora";
  }
});

$("#refresh-button")?.addEventListener("click", refresh);

$("#job-filters")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  jobsPage = 1;
  await refresh();
});

$("#jobs-page-size")?.addEventListener("change", async () => {
  jobsPage = 1;
  await refresh();
});

$("#jobs-clear-filters")?.addEventListener("click", async () => {
  $("#job-filters").reset();
  jobsPage = 1;
  await refresh();
});

$("#jobs-previous")?.addEventListener("click", async () => {
  if (jobsPage <= 1) return;
  jobsPage -= 1;
  await refresh();
});

$("#jobs-next")?.addEventListener("click", async () => {
  jobsPage += 1;
  await refresh();
});

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
