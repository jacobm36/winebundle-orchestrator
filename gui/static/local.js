"use strict";
const el = id => document.getElementById(id);
const lines = text => text.split(/\r?\n/).filter(line => line.trim());
let token = "", current = "", state = null, setups = [], step = 1;
let pending = false, timer = null, revision = 0, connected = false;
let librarySignature = "", defaults = {runner: "", places: []};
let runnerTimer = null, runnerRevision = 0, system = null;
const picker = {options: null, path: "", selected: "", revision: 0};

// The prefix's drive_c is shown as C:\ so internal setup folder names stay out of the way.
function toWindows(path, drive = state && state.drive) {
  if (!drive || !path) return path;
  if (path === drive) return "C:\\";
  return path.startsWith(`${drive}/`) ? `C:\\${path.slice(drive.length + 1).replaceAll("/", "\\")}` : path;
}
function toLinux(text, drive = state && state.drive) {
  const match = /^[cC]:(?:[\\/](.*))?$/.exec(text);
  if (!drive || !match) return text;
  const rest = (match[1] || "").replaceAll("\\", "/").replace(/\/+$/, "");
  return rest ? `${drive}/${rest}` : drive;
}
const entrypointPath = () => toLinux(el("entrypoint").value);
const parentOf = path => path && path.startsWith("/") ? path.replace(/\/[^/]*$/, "") || "/" : "";
function remembered(kind) {
  try { return localStorage.getItem(`winebundle.picker.${kind}`) || ""; } catch { return ""; }
}
function remember(kind, folder) {
  try { localStorage.setItem(`winebundle.picker.${kind}`, folder); } catch { /* storage disabled */ }
}
const place = label => (defaults.places.find(item => item.label === label) || {}).path || "";

async function api(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: {"X-Winebundle-Token": token, "Content-Type": "application/json"},
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
function message(text, failed = false) {
  el("result").textContent = text;
  el("result").classList.toggle("error", failed);
  el("result").setAttribute("role", failed ? "alert" : "status");
}
function error(exc) {
  const help = current ? "Check the activity log or refresh status before retrying." :
    el("workspace").hidden ? "Use Game library to refresh, or reload the page to reconnect." :
    "Check the paths above and try again.";
  message(`${exc.message} ${help}`, true);
}
function busy() { return setups.some(setup => setup.status === "running"); }
function showView(name) {
  for (const view of ["library", "workspace", "system"]) el(view).hidden = view !== name;
}
const SYSTEM_LABELS = {ok: "OK", warning: "Recommended", error: "Required", optional: "Optional", skipped: "Waiting"};
function systemSummary(result) {
  const missing = result.checks.filter(check => check.status === "error").map(check => check.title);
  if (missing.length) return `${missing.join(" and ")} must be installed before you can add games.`;
  if (result.warnings) {
    return `Ready to add games, with ${result.warnings} recommendation${result.warnings === 1 ? "" : "s"} for better compatibility.`;
  }
  return "This computer is ready to install and play Windows games.";
}
async function copyCommands(commands, button) {
  const text = commands.join(" && ");
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  button.textContent = "Copied";
  setTimeout(() => { button.textContent = "Copy commands"; }, 1500);
}
function systemCard(check) {
  const card = document.createElement("li"), heading = document.createElement("div");
  const title = document.createElement("h3"), badge = document.createElement("span");
  const detail = document.createElement("p");
  card.className = `system-check status-${check.status}`;
  heading.className = "system-check-heading";
  title.textContent = check.title;
  badge.className = "badge";
  badge.textContent = SYSTEM_LABELS[check.status] || check.status;
  detail.textContent = check.detail;
  heading.append(title, badge);
  card.append(heading, detail);
  if (check.fix) {
    const help = document.createElement("p");
    help.className = "muted";
    help.textContent = check.fix.text;
    card.append(help);
    if (check.fix.commands.length) {
      const block = document.createElement("div"), code = document.createElement("pre");
      const copy = document.createElement("button");
      block.className = "command";
      code.textContent = check.fix.commands.join("\n");
      copy.type = "button";
      copy.className = "secondary";
      copy.textContent = "Copy commands";
      copy.setAttribute("aria-label", `Copy commands for ${check.title}`);
      copy.addEventListener("click", () => copyCommands(check.fix.commands, copy));
      block.append(code, copy);
      card.append(block);
    }
  }
  return card;
}
function renderSystem() {
  const summary = systemSummary(system);
  el("system-summary").textContent = summary;
  el("system-summary").classList.toggle("error", !system.ready);
  el("system-distro").textContent = `Detected ${system.distro.name}.${system.distro.family ? "" :
    " Install commands aren't available for this distribution; use its package manager."}`;
  el("system-checks").replaceChildren(...system.checks.map(systemCard));
  el("system-banner").hidden = system.ready && !system.warnings;
  el("system-banner").classList.toggle("required", !system.ready);
  el("system-banner-text").textContent = summary;
}
async function loadSystem() {
  el("system-refresh").disabled = true;
  el("system-summary").classList.remove("error");
  el("system-summary").textContent = "Checking this computer...";
  try {
    system = await api("/api/system");
    if (system.runner) defaults.runner = system.runner;
    renderSystem();
  } catch (exc) {
    el("system-summary").classList.add("error");
    el("system-summary").textContent = `Couldn't check this computer: ${exc.message}`;
  } finally {
    el("system-refresh").disabled = false;
  }
}
function showSystem() {
  ++revision;
  current = "";
  state = null;
  showView("system");
  message("");
  el("system-title").focus();
  refresh().catch(error);
  return loadSystem();
}
function tested() {
  return state && state.action === "test" && state.status === "finished" &&
    entrypointPath() === state.config.entrypoint &&
    JSON.stringify(lines(el("args").value)) === JSON.stringify(state.config.args);
}
function statusText(setup) {
  if (setup.status === "running") {
    return setup.action === "prepare" ? "Installing / preparing" :
      setup.action === "play" ? "Playing" : "Testing game";
  }
  if (setup.status === "failed") return "Needs attention";
  return setup.accepted ? "Ready to play" :
    setup.prepared ? "Ready to test" : "Installation needed";
}
function showStep(value, focus = false) {
  step = value;
  document.querySelectorAll("[data-panel]").forEach(panel => {
    panel.hidden = Number(panel.dataset.panel) !== step;
  });
  document.querySelectorAll("[data-step]").forEach(button => {
    const number = Number(button.dataset.step);
    if (number === step) button.setAttribute("aria-current", "step");
    else button.removeAttribute("aria-current");
    button.disabled = pending || !connected || (number === 1 ? Boolean(current) :
      !state || (number >= 3 && !state.config.prepared) ||
      (number === 4 && !state.config.accepted && !tested()));
  });
  if (focus) el("setup-title").focus();
}
function library() {
  const query = el("search").value.trim().toLocaleLowerCase();
  const signature = JSON.stringify([setups, query, pending, connected]);
  if (signature === librarySignature) return;
  librarySignature = signature;
  const focused = el("games").contains(document.activeElement) ?
    document.activeElement.dataset.libraryAction : "";
  const visible = setups.filter(setup => setup.name.toLocaleLowerCase().includes(query))
    .sort((a, b) => a.name.localeCompare(b.name));
  el("games").replaceChildren();
  el("library-status").textContent = !connected ? "Library unavailable. Reload to reconnect." :
    !setups.length ? "No games yet. Add your first game to get started." :
    !visible.length ? "No games match your search." :
    `${setups.length} saved game${setups.length === 1 ? "" : "s"}${busy() ? " - an operation is running" : ""}`;
  el("add-game").disabled = pending || !connected || busy();
  for (const setup of visible) {
    const card = document.createElement("article");
    card.className = "game-card";
    const title = document.createElement("h3");
    title.textContent = setup.name;
    const status = document.createElement("p");
    status.className = "game-status";
    status.textContent = statusText(setup);
    const actions = document.createElement("div");
    actions.className = "actions";
    if (setup.accepted) {
      const play = document.createElement("button");
      play.type = "button";
      play.dataset.libraryAction = `play-${setup.id}`;
      play.textContent = "Play";
      play.setAttribute("aria-label", `Play ${setup.name}`);
      play.disabled = pending || busy() || !connected;
      play.addEventListener("click", () => launch(setup.id).catch(error));
      actions.append(play);
    }
    const open = document.createElement("button");
    open.type = "button";
    open.dataset.libraryAction = `open-${setup.id}`;
    open.className = "secondary";
    open.textContent = setup.status === "running" ? "View progress" :
      setup.accepted ? "Setup & details" : "Continue setup";
    open.setAttribute("aria-label", `${open.textContent}: ${setup.name}`);
    open.disabled = pending || !connected;
    open.addEventListener("click", () => openSetup(setup.id).catch(error));
    actions.append(open);
    card.append(title, status);
    if (setup.error) {
      const detail = document.createElement("p");
      detail.className = "error";
      detail.textContent = setup.error;
      card.append(detail);
    }
    card.append(actions);
    el("games").append(card);
  }
  if (focused) {
    const replacement = Array.from(el("games").querySelectorAll("button"))
      .find(button => button.dataset.libraryAction === focused && !button.disabled);
    if (replacement) replacement.focus({preventScroll: true});
  }
}
function render() {
  library();
  const config = state && state.config;
  const running = state && state.status === "running";
  el("activity").hidden = !config;
  for (const id of ["home", "back-library"]) el(id).disabled = pending;
  el("setup-title").textContent = config ? config.name : "Add a game";
  el("summary").textContent = config ? `${statusText({...config, ...state})}${state.error ? `: ${state.error}` : ""}` : "";
  el("prepare").disabled = pending || !connected || busy() || !config || config.prepared;
  el("test").disabled = pending || !connected || busy() || !config || !config.prepared || !el("entrypoint").value.trim();
  el("save").disabled = pending || !connected || busy() || !tested() || !el("accepted").checked || Boolean(config && config.accepted);
  el("stop").disabled = pending || !connected || !running;
  el("operation-controls").hidden = !running;
  el("play").hidden = !config || !config.accepted;
  el("play").disabled = pending || !connected || busy();
  el("save").hidden = Boolean(config && config.accepted);
  el("acceptance").hidden = Boolean(config && config.accepted);
  el("to-test").hidden = !config || !config.prepared;
  el("to-test").disabled = pending || Boolean(running);
  el("to-finish").hidden = !tested();
  el("to-finish").disabled = pending || Boolean(running);
  el("retest").disabled = pending || Boolean(running);
  el("finish-title").textContent = config && config.accepted ? "Your game is ready" : "Did the game work?";
  el("finish-help").textContent = config && config.accepted ?
    `Play from your library${shortcutText(state.shortcuts)}. Shortcuts work without this app; change them below.` :
    "Confirm gameplay worked to add Play to your library and create the shortcuts you choose.";
  const desktopFolder = Boolean(state && state.shortcuts && state.shortcuts.desktopFolder);
  el("shortcut-menu").disabled = pending || !connected || !config;
  el("shortcut-desktop").disabled = pending || !connected || !config || !desktopFolder;
  if (state && !desktopFolder) {
    el("shortcut-status").textContent = "No desktop folder was found, so a desktop shortcut isn't available.";
  } else if (el("shortcut-status").textContent.startsWith("No desktop folder")) {
    el("shortcut-status").textContent = "";
  }
  technical();
  el("install-help").textContent = config && config.mode === "existing" ?
    "Prepare a separate Wine environment. Your existing game folder is not copied or imported." :
    "Start preparation, then complete the installer on your desktop. Close the installer and any game it opens. The next step appears when they exit.";
  el("prepare").textContent = config && config.mode === "existing" ? "Prepare Wine environment" : "Prepare and open installer";
  el("setup").querySelector("button[type=submit]").disabled = pending || !connected || busy();
  showStep(step);
}
function shortcutText(shortcuts) {
  const places = [shortcuts && shortcuts.menu && "your application menu",
    shortcuts && shortcuts.desktop && "your desktop"].filter(Boolean);
  return places.length === 2 ? `, ${places.join(" or ")}` : places.length ? ` or ${places[0]}` : "";
}
function technical() {
  el("technical").hidden = !state;
  if (!state) return;
  const config = state.config;
  const rows = [
    ["Game folder", state.directory],
    ["Windows drive (C:)", state.drive],
    ["Game executable", config.entrypoint],
    ["Wine", config.runnerVersion ? `${config.runner} (${config.runnerVersion})` : config.runner],
    ["Launcher script", config.launcher],
    ["Menu shortcut", config.desktop],
    ["Desktop shortcut", config.desktopShortcut]
  ].filter(([, value]) => value);
  const signature = JSON.stringify(rows);
  if (el("technical-paths").dataset.signature === signature) return;
  el("technical-paths").dataset.signature = signature;
  el("technical-paths").replaceChildren(...rows.flatMap(([label, value]) => {
    const term = document.createElement("dt"), detail = document.createElement("dd");
    term.textContent = label;
    detail.textContent = value;
    return [term, detail];
  }));
}
function details(fill) {
  if (!state) return;
  const config = state.config;
  if (fill) {
    el("entrypoint").value = toWindows(config.entrypoint);
    el("args").value = config.args.join("\n");
    el("accepted").checked = false;
    el("shortcut-menu").checked = true;
    el("shortcut-desktop").checked = false;
    el("shortcut-status").textContent = "";
    el("shortcut-status").classList.remove("error");
  }
  if (config.accepted && !pending) {
    el("shortcut-menu").checked = state.shortcuts.menu;
    el("shortcut-desktop").checked = state.shortcuts.desktop;
  }
  if (state.status !== "running" && !el("entrypoint").value && state.recommendedExecutable) {
    el("entrypoint").value = toWindows(state.recommendedExecutable);
  }
  el("log").textContent = state.log;
  el("detection").textContent = state.status === "running" ?
    "Game detection resumes when the operation finishes." :
    state.recommendedExecutable ? "A likely game executable was found. Check it before testing." :
    state.executables.length ? "Choose the game itself, not an installer or helper." :
    "No game found in this game's C: drive. Use Browse to find it.";
  el("candidates").replaceChildren(new Option("Choose a detected game, or browse", ""));
  state.executables.forEach(path => el("candidates").add(new Option(
    path === state.recommendedExecutable ? `Suggested: ${toWindows(path)}` : toWindows(path), path)));
  el("candidates").value = entrypointPath();
  if (el("candidates").selectedIndex < 0) el("candidates").value = "";
}
async function refresh(fill = false, completedAction = "") {
  clearTimeout(timer);
  const version = ++revision, id = current;
  try {
    const saved = await api("/api/local");
    const next = id ? await api(`/api/local/${id}`) : null;
    if (version !== revision) return;
    const previous = state;
    setups = saved;
    state = next;
    if (!connected && el("result").classList.contains("error")) {
      message("Connection restored. Status is up to date.");
    }
    connected = true;
    details(fill);
    if (state && previous && state.status !== "running" &&
        (previous.status === "running" || previous.status !== state.status ||
         previous.action !== state.action || state.action === completedAction)) {
      if (state.status === "failed") {
        message(`Operation failed: ${state.error}. Open the activity log for details.`, true);
        el("activity").open = true;
      } else if (state.action === "prepare") {
        step = 3;
        message("Preparation finished. Check the game executable and test gameplay.");
      } else if (state.action === "test") {
        step = 4;
        message("The game exited. Confirm gameplay worked before saving.");
      } else {
        message("The game exited. Ready to play again.");
      }
    }
    render();
    if (busy()) timer = setTimeout(() => refresh().catch(error), 1500);
  } catch (exc) {
    if (version !== revision) return;
    connected = false;
    render();
    throw exc;
  }
}
async function openSetup(id) {
  current = id;
  state = null;
  message("");
  showView("workspace");
  el("activity").open = false;
  render();
  await refresh(true);
  if (current !== id || !state) return;
  showStep(state.config.accepted ? 4 : state.config.prepared ? 3 : 2, true);
  if (state.status === "failed") el("activity").open = true;
}
async function perform(action, body = {}) {
  pending = true;
  clearTimeout(timer);
  ++revision;
  render();
  try {
    await api(`/api/local/${current}/${action}`, body);
    if (action === "save") message(`Saved. Play from your library${shortcutText({
      menu: body.menu, desktop: body.desktop})}.`);
    else if (action === "stop") message("Stop requested. Waiting for Wine processes to exit.");
    else message(action === "prepare" ?
      "Preparing. Watch for installer windows on your desktop; finish and close them to continue." :
      "Game started. Exit normally when you're finished.");
    await refresh(false, action);
  } catch (exc) {
    try { await refresh(); } catch (refreshError) {
      error(new Error(`${exc.message}; status refresh failed: ${refreshError.message}`));
      return;
    }
    error(exc);
    el("activity").open = true;
  } finally {
    pending = false;
    render();
  }
}
async function launch(id) {
  await openSetup(id);
  if (current === id && state && connected) await perform("play");
}
function home() {
  ++revision;
  current = "";
  state = null;
  showView("library");
  message("");
  refresh().catch(error);
  el("search").focus();
}
for (const id of ["home", "back-library", "system-back"]) el(id).addEventListener("click", home);
for (const id of ["system-open", "system-banner-open"]) el(id).addEventListener("click", showSystem);
el("system-refresh").addEventListener("click", loadSystem);
el("search").addEventListener("input", library);
el("add-game").addEventListener("click", () => {
  ++revision;
  current = "";
  state = null;
  el("setup").reset();
  el("runner").value = defaults.runner;
  el("setup").elements.mode.dispatchEvent(new Event("change"));
  checkRunner();
  showView("workspace");
  el("activity").hidden = true;
  message("");
  step = 1;
  render();
  el("setup").elements.name.focus();
});
el("setup").elements.mode.addEventListener("change", event => {
  const existing = event.target.value === "existing";
  el("source-label").textContent = existing ? "Installed game file (.exe)" : "Installer file";
  el("source").placeholder = existing ? "Choose the game's .exe" : "Choose an .exe or .msi installer";
  document.querySelector("[data-browse=source]").setAttribute("aria-label",
    existing ? "Browse for installed game file" : "Browse for installer file");
});
async function checkRunner() {
  clearTimeout(runnerTimer);
  const version = ++runnerRevision, value = el("runner").value;
  const status = el("runner-status");
  if (!value) {
    status.classList.toggle("error", !defaults.runner);
    status.textContent = defaults.runner ? "" :
      "Wine wasn't found. Open System check for install steps, or browse to a Wine executable.";
    return;
  }
  try {
    const result = await api(`/api/runner?path=${encodeURIComponent(value)}`);
    if (version !== runnerRevision) return;
    status.classList.toggle("error", Boolean(result.error));
    status.textContent = result.error || "Wine found, with its matching wineserver.";
  } catch (exc) {
    if (version === runnerRevision) status.textContent = "";
  }
}
el("runner").addEventListener("input", () => {
  clearTimeout(runnerTimer);
  runnerTimer = setTimeout(checkRunner, 350);
});

const pickerFields = {
  source: () => {
    const existing = el("setup").elements.mode.value === "existing";
    return {
      title: existing ? "Choose the installed game's .exe" : "Choose an installer",
      filter: existing ? "Showing folders and .exe files." : "Showing folders, .exe and .msi files.",
      kind: existing ? "exe" : "installer", input: el("source"),
      starts: [parentOf(el("source").value), remembered(existing ? "exe" : "installer"),
        place(existing ? "Games" : "Downloads")]
    };
  },
  runner: () => ({
    title: "Choose a Wine executable",
    filter: "Showing folders and programs. Choose a file named wine, such as /usr/bin/wine or a Wine-GE bin/wine.",
    kind: "runner", input: el("runner"),
    starts: [parentOf(el("runner").value), remembered("runner"), "/usr/bin"]
  }),
  dxvk: () => ({
    title: "Choose the DXVK release folder",
    filter: "Open the extracted DXVK folder (it contains x64 and x32), then select Use this folder.",
    kind: "folder", input: el("dxvk"),
    starts: [el("dxvk").value, remembered("folder"), place("Downloads")]
  }),
  entrypoint: () => ({
    title: "Choose the game's .exe",
    filter: "Showing folders and .exe files. C:\\ is this game's own Windows drive.",
    kind: "exe", input: el("entrypoint"), drive: state && state.drive,
    starts: [parentOf(entrypointPath()), state && state.drive, remembered("exe")]
  })
};
function pickerDisplay(path) { return toWindows(path, picker.options.drive); }
async function loadPicker(paths) {
  const version = ++picker.revision, options = picker.options;
  const candidates = paths.filter(Boolean);
  el("picker-status").textContent = candidates.length ? "Loading..." : "";
  for (let index = 0; index < candidates.length; index++) {
    try {
      const query = new URLSearchParams({path: candidates[index], kind: options.kind,
        hidden: el("picker-hidden").checked ? "1" : "0"});
      const data = await api(`/api/browse?${query}`);
      if (version !== picker.revision) return;
      showFolder(data);
      return;
    } catch (exc) {
      if (version !== picker.revision) return;
      if (index === candidates.length - 1) el("picker-status").textContent = exc.message;
    }
  }
}
function showFolder(data) {
  const folderMode = picker.options.kind === "folder";
  picker.path = data.path;
  picker.selected = folderMode ? data.path : "";
  el("picker-path").value = pickerDisplay(data.path);
  el("picker-up").disabled = !data.parent;
  el("picker-up").dataset.path = data.parent;
  el("picker-entries").replaceChildren(...data.entries.map(entry => {
    const item = document.createElement("li");
    const button = document.createElement("button");
    const icon = document.createElement("span");
    button.type = "button";
    button.className = "picker-entry";
    button.dataset.type = entry.type;
    icon.className = "picker-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = entry.type === "folder" ? "\u{1F4C1}" : "\u{1F4C4}";
    button.append(icon, entry.name);
    button.setAttribute("aria-label", entry.type === "folder" ? `Folder ${entry.name}` : entry.name);
    if (entry.type === "folder") {
      button.addEventListener("click", () => loadPicker([entry.path]));
    } else {
      button.setAttribute("aria-pressed", "false");
      button.addEventListener("click", () => {
        el("picker-entries").querySelectorAll("[aria-pressed]").forEach(other =>
          other.setAttribute("aria-pressed", String(other === button)));
        picker.selected = entry.path;
        updatePickerSelection();
      });
      button.addEventListener("dblclick", choosePicked);
    }
    item.append(button);
    return item;
  }));
  const files = data.entries.filter(entry => entry.type === "file").length;
  el("picker-status").textContent = (!data.entries.length ? "Nothing to show in this folder." :
    folderMode ? `${data.entries.length} folder${data.entries.length === 1 ? "" : "s"}` :
    `${files} matching file${files === 1 ? "" : "s"}, ${data.entries.length - files} folder${data.entries.length - files === 1 ? "" : "s"}`) +
    (data.truncated ? ". This folder is very large; only the first 5000 items are shown." : "");
  updatePickerSelection();
}
function updatePickerSelection() {
  const folderMode = picker.options.kind === "folder";
  el("picker-choose").disabled = !picker.selected;
  el("picker-selection").textContent = picker.selected ?
    `${folderMode ? "Folder" : "Selected"}: ${pickerDisplay(picker.selected)}` :
    "Select a file, or open a folder.";
}
function openPicker(field) {
  const options = pickerFields[field]();
  picker.options = options;
  picker.selected = "";
  el("picker-title").textContent = options.title;
  el("picker-filter").textContent = options.filter;
  el("picker-choose").textContent = options.kind === "folder" ? "Use this folder" : "Choose";
  const places = (options.drive ? [{label: "This game's C: drive", path: options.drive}] : [])
    .concat(defaults.places);
  el("picker-places").replaceChildren(...places.map(item => {
    const entry = document.createElement("li"), button = document.createElement("button");
    button.type = "button";
    button.className = "secondary";
    button.textContent = item.label;
    button.title = pickerDisplay(item.path);
    button.addEventListener("click", () => loadPicker([item.path]));
    entry.append(button);
    return entry;
  }));
  el("picker-entries").replaceChildren();
  el("picker-path").value = pickerDisplay(options.starts.find(Boolean) || place("Home"));
  el("picker-up").disabled = true;
  updatePickerSelection();
  el("picker").showModal();
  loadPicker([...options.starts, place("Home")]);
}
function choosePicked() {
  const options = picker.options;
  if (!picker.selected) return;
  remember(options.kind, options.kind === "folder" ? picker.selected : parentOf(picker.selected));
  options.input.value = options.input === el("entrypoint") ? toWindows(picker.selected) : picker.selected;
  el("picker").close();
  options.input.dispatchEvent(new Event("input", {bubbles: true}));
  if (options.input === el("runner")) checkRunner();
  options.input.focus();
}
document.querySelectorAll("[data-browse]").forEach(button => {
  button.addEventListener("click", () => openPicker(button.dataset.browse));
});
el("picker-choose").addEventListener("click", choosePicked);
el("picker-cancel").addEventListener("click", () => el("picker").close());
el("picker").addEventListener("close", () => { ++picker.revision; });
el("picker").addEventListener("keydown", event => {
  if (event.key !== "Escape") return;
  event.preventDefault();
  el("picker").close();
});
el("picker-up").addEventListener("click", () => loadPicker([el("picker-up").dataset.path]));
el("picker-hidden").addEventListener("change", () => loadPicker([picker.path]));
el("picker-go").addEventListener("submit", event => {
  event.preventDefault();
  loadPicker([toLinux(el("picker-path").value.trim(), picker.options.drive)]);
});
el("setup").addEventListener("submit", async event => {
  event.preventDefault();
  pending = true;
  render();
  try {
    const values = Object.fromEntries(new FormData(event.target));
    values.installArgs = lines(values.installArgs);
    const created = await api("/api/local", values);
    await openSetup(created.id);
    message("Setup saved. Prepare your game next; you can return to it later from the library.");
  } catch (exc) { error(exc); } finally { pending = false; render(); }
});
document.querySelectorAll("[data-step]").forEach(button => {
  button.addEventListener("click", () => showStep(Number(button.dataset.step), true));
});
for (const [id, value] of [["to-test", 3], ["to-finish", 4], ["retest", 3]]) {
  el(id).addEventListener("click", () => showStep(value, true));
}
el("candidates").addEventListener("change", () => {
  if (el("candidates").value) el("entrypoint").value = toWindows(el("candidates").value);
  el("accepted").checked = false;
  render();
});
for (const id of ["entrypoint", "args"]) el(id).addEventListener("input", () => {
  el("accepted").checked = false;
  render();
});
el("accepted").addEventListener("change", render);
el("prepare").addEventListener("click", () => perform("prepare"));
el("test").addEventListener("click", () => {
  el("accepted").checked = false;
  perform("test", {entrypoint: entrypointPath(), args: lines(el("args").value)});
});
el("stop").addEventListener("click", () => {
  if (confirm("Stop this game's Wine processes? Unsaved progress may be lost.")) perform("stop");
});
el("save").addEventListener("click", () => perform("save", {accepted: el("accepted").checked,
  menu: el("shortcut-menu").checked, desktop: el("shortcut-desktop").checked}));
async function updateShortcuts() {
  if (!state || !state.config.accepted) return;
  const menu = el("shortcut-menu").checked, desktop = el("shortcut-desktop").checked;
  pending = true;
  clearTimeout(timer);
  ++revision;
  render();
  let status = "", failed = false;
  try {
    await api(`/api/local/${current}/shortcuts`, {menu, desktop});
    status = menu || desktop ?
      `Shortcuts updated: ${[menu && "application menu", desktop && "desktop"].filter(Boolean).join(" and ")}.` :
      "Shortcuts removed. You can still play from your library.";
  } catch (exc) {
    failed = true;
    status = `Couldn't update shortcuts: ${exc.message}`;
  } finally {
    pending = false;
    try { await refresh(); } catch (exc) { error(exc); }
    el("shortcut-status").classList.toggle("error", failed);
    el("shortcut-status").textContent = status;
  }
}
el("shortcut-menu").addEventListener("change", updateShortcuts);
el("shortcut-desktop").addEventListener("change", updateShortcuts);
el("play").addEventListener("click", () => perform("play"));
el("retry-refresh").addEventListener("click", () => refresh().catch(error));
(async () => {
  try {
    const session = await api("/api/session");
    token = session.token;
    defaults = session.local || defaults;
    await Promise.all([refresh(), loadSystem()]);
    let seen = true;
    try {
      seen = localStorage.getItem("winebundle.systemChecked") === "1";
      localStorage.setItem("winebundle.systemChecked", "1");
    } catch { /* storage disabled */ }
    // Show the check on first run, and whenever required software is missing.
    if (!current && el("workspace").hidden && (!seen || (system && !system.ready))) showView("system");
  } catch (exc) {
    connected = false;
    render();
    error(exc);
  }
})();
