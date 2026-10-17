const form = document.querySelector("#build-form");
const message = document.querySelector("#message");
const startButton = document.querySelector("#start-build");
const jobStatus = document.querySelector("#job-status");
const statusDot = document.querySelector("#status-dot");
const buildLog = document.querySelector("#build-log");
let token = "";
let defaults = {};

function lines(value) {
  return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
}

function field(name) {
  return form.elements.namedItem(name);
}

function buildConfig() {
  const type = field("validationType").value;
  const config = {
    version: 1,
    installer: field("installer").value.trim(),
    entrypoint: field("entrypoint").value.trim(),
    output: field("output").value.trim(),
    installArgs: lines(field("installArgs").value),
    winetricks: lines(field("winetricks").value),
    arch: field("arch").value,
    timeout: Number(field("timeout").value),
    image: field("image").value.trim(),
    cpus: Number(field("cpus").value),
    memory: field("memory").value.trim(),
    disk: field("disk").value.trim(),
    keepHostDrive: field("keepHostDrive").checked,
    skipProvision: field("skipProvision").checked,
    keepVm: field("keepVm").checked,
  };
  const preInstallScript = field("preInstallScript").value.trim();
  if (preInstallScript) config.preInstallScript = preInstallScript;
  if (type === "command") {
    config.smokeArgs = lines(field("smokeArgs").value);
    config.smokeExitCode = Number(field("smokeExitCode").value);
  } else if (type === "window") {
    config.smokeWindow = field("smokeWindow").value.trim();
  } else {
    config.smokeScript = field("smokeScript").value.trim();
  }
  return config;
}

function setValue(name, value) {
  const input = field(name);
  if (input.type === "checkbox") input.checked = Boolean(value);
  else input.value = value ?? "";
}

function populate(config) {
  const merged = { ...defaults, ...config };
  [
    "installer", "entrypoint", "output", "arch", "timeout", "image", "cpus",
    "memory", "disk", "preInstallScript", "keepHostDrive", "skipProvision", "keepVm",
  ].forEach((name) => setValue(name, merged[name]));
  setValue("installArgs", (merged.installArgs || []).join("\n"));
  setValue("winetricks", (merged.winetricks || []).join("\n"));
  setValue("smokeArgs", (merged.smokeArgs || []).join("\n"));
  setValue("smokeExitCode", merged.smokeExitCode ?? 0);
  setValue("smokeWindow", merged.smokeWindow);
  setValue("smokeScript", merged.smokeScript);

  let validationType = "command";
  if (merged.smokeWindow) validationType = "window";
  if (merged.smokeScript) validationType = "script";
  const radio = form.querySelector(`[name="validationType"][value="${validationType}"]`);
  radio.checked = true;
  showValidation(validationType);
}

function showValidation(type) {
  ["command", "window", "script"].forEach((name) => {
    document.querySelector(`#validation-${name}`).classList.toggle("hidden", name !== type);
  });
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Winebundle-Token": token,
      ...(options.headers || {}),
    },
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
  return body;
}

async function pollJob(jobId) {
  const job = await api(`/api/jobs/${jobId}`);
  jobStatus.textContent = job.status[0].toUpperCase() + job.status.slice(1);
  statusDot.className = job.status;
  buildLog.textContent = job.log || "Waiting for build output...";
  buildLog.scrollTop = buildLog.scrollHeight;
  if (job.status === "queued" || job.status === "running") {
    window.setTimeout(() => pollJob(jobId).catch(showError), 1000);
  } else {
    startButton.disabled = false;
  }
}

function showError(error) {
  message.textContent = error.message;
  startButton.disabled = false;
}

form.addEventListener("change", (event) => {
  if (event.target.name === "validationType") showValidation(event.target.value);
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  message.textContent = "";
  startButton.disabled = true;
  try {
    const config = buildConfig();
    await api("/api/config/validate", {
      method: "POST",
      body: JSON.stringify({ config }),
    });
    const result = await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify({ config }),
    });
    jobStatus.textContent = "Queued";
    statusDot.className = "running";
    buildLog.textContent = "Build queued...";
    await pollJob(result.jobId);
  } catch (error) {
    showError(error);
  }
});

document.querySelector("#import-config").addEventListener("change", async (event) => {
  message.textContent = "";
  try {
    const selected = event.target.files[0];
    if (!selected) return;
    const config = JSON.parse(await selected.text());
    await api("/api/config/validate", {
      method: "POST",
      body: JSON.stringify({ config }),
    });
    populate(config);
  } catch (error) {
    showError(error);
  } finally {
    event.target.value = "";
  }
});

document.querySelector("#export-config").addEventListener("click", async () => {
  message.textContent = "";
  try {
    const config = buildConfig();
    await api("/api/config/validate", {
      method: "POST",
      body: JSON.stringify({ config }),
    });
    const blob = new Blob([`${JSON.stringify(config, null, 2)}\n`], {
      type: "application/json",
    });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "winebundle-config.json";
    link.click();
    URL.revokeObjectURL(link.href);
  } catch (error) {
    showError(error);
  }
});

fetch("/api/session")
  .then((response) => response.json())
  .then((session) => {
    token = session.token;
    defaults = session.defaults;
    populate(defaults);
  })
  .catch(showError);
