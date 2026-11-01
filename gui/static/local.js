"use strict";
const el = id => document.getElementById(id);
const lines = text => text.split(/\r?\n/).filter(line => line.trim());
let token = "", current = "", state = null, setups = [], step = 1;
let pending = false, timer = null, revision = 0, connected = false;
let librarySignature = "", defaults = {runner: "", places: []};
let runnerTimer = null, runnerRevision = 0, runnerProblem = "", system = null, systemLoading = false;
let sourceTimer = null, sourceRevision = 0, sourceWarning = null, suggestedName = "";
let preferences = {systemChecked: false, discreteGpu: true, pickerFolders: {}};
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
// Preferences live on the server so they survive the desktop app's per-launch port.
const remembered = kind => preferences.pickerFolders[kind] || "";
function savePreferences(values) {
  api("/api/preferences", values).then(saved => { preferences = saved; }).catch(() => {});
}
function remember(kind, folder) {
  preferences.pickerFolders[kind] = folder;
  savePreferences({pickerFolders: {[kind]: folder}});
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
// The window opens with a one-time launch code; the token it buys is kept for this tab only.
async function connect() {
  const launch = /^#launch=([\w-]+)$/.exec(location.hash);
  if (launch) history.replaceState(null, "", location.pathname + location.search);
  const headers = launch ? {"X-Winebundle-Launch": launch[1]} :
    {"X-Winebundle-Token": sessionStorage.getItem("winebundle-token") || ""};
  const response = await fetch("/api/session", {headers});
  const session = await response.json();
  if (!response.ok && !launch) throw new Error("This page isn't connected to Winebundle. Open Winebundle again.");
  if (!response.ok) throw new Error(session.error || `HTTP ${response.status}`);
  sessionStorage.setItem("winebundle-token", session.token);
  return session;
}
function message(text, failed = false) {
  el("result").textContent = text;
  el("result").classList.toggle("error", failed);
  el("result").setAttribute("role", failed ? "alert" : "status");
}
function error(exc) {
  const help = current ? "Check the activity log, then try again." :
    el("workspace").hidden ? "Reload the page, or open Winebundle again." :
    "Check the choices above and try again.";
  message(`${exc.message} ${help}`, true);
}
function busy() { return setups.some(setup => setup.status === "running"); }
function showView(name) {
  el("remove-confirm").hidden = true;
  for (const view of ["welcome", "library", "workspace", "system"]) el(view).hidden = view !== name;
  const section = name === "system" ? "system-open" : name === "welcome" ? "" : "home";
  for (const id of ["home", "system-open"]) {
    if (id === section) el(id).setAttribute("aria-current", "page");
    else el(id).removeAttribute("aria-current");
  }
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
  renderGraphicsDefault();
  el("system-banner").hidden = system.ready && !system.warnings;
  el("system-banner").classList.toggle("required", !system.ready);
  el("system-banner-text").textContent = summary;
}
function renderGraphicsDefault() {
  el("graphics-default").hidden = !(system && system.discreteGpu);
  if (!system || !system.discreteGpu) return;
  el("discrete-default-name").textContent = system.discreteGpu;
  el("discrete-default").checked = preferences.discreteGpu;
}
function cardText(discrete, name) {
  return discrete ? `the ${name}` : "the built-in graphics";
}
function renderWelcomeCheck(failure = "") {
  const badge = el("welcome-check-badge"), summary = el("welcome-check-summary");
  badge.hidden = systemLoading || !system;
  summary.classList.toggle("error", Boolean(failure) || Boolean(system && !system.ready));
  el("welcome-check-again").disabled = systemLoading;
  if (systemLoading) {
    summary.textContent = "Checking this computer...";
    el("welcome-check-problems").replaceChildren();
    return;
  }
  if (!system) {
    summary.textContent = `Couldn't check this computer${failure ? `: ${failure}` : ""}. Select Check again.`;
    return;
  }
  badge.textContent = !system.ready ? "Action needed" : system.warnings ? "Ready, with tips" : "Ready";
  badge.className = `welcome-badge ${!system.ready ? "status-error" : system.warnings ? "status-warning" : "status-ok"}`;
  summary.textContent = system.warnings || !system.ready ?
    `${systemSummary(system)} Each item below shows how to fix it.` : systemSummary(system);
  el("welcome-check-problems").replaceChildren(...system.checks
    .filter(check => check.status === "error" || check.status === "warning").map(systemCard));
}
function renderWelcomeActions() {
  el("library-welcome").hidden = !connected || setups.length > 0;
  const blocked = Boolean(system && !system.ready);
  for (const id of ["welcome-installer", "welcome-existing"]) {
    el(id).disabled = pending || !connected || busy() || systemLoading || blocked;
  }
  el("welcome-add-status").textContent = !connected || systemLoading ? "" :
    blocked ? "Install what step 1 lists as required, then select Check again." :
    busy() ? "Another game is installing or running. You can add a game when it finishes." : "";
}
async function loadSystem() {
  el("system-refresh").disabled = true;
  el("system-summary").classList.remove("error");
  el("system-summary").textContent = "Checking this computer...";
  systemLoading = true;
  renderWelcomeCheck();
  renderWelcomeActions();
  let failure = "";
  try {
    system = await api("/api/system");
    if (system.runner) defaults.runner = system.runner;
    renderSystem();
  } catch (exc) {
    failure = exc.message;
    el("system-summary").classList.add("error");
    el("system-summary").textContent = `Couldn't check this computer: ${exc.message}`;
  } finally {
    systemLoading = false;
    el("system-refresh").disabled = false;
    renderWelcomeCheck(failure);
    renderWelcomeActions();
    showRunner();
  }
}
function showWelcome() {
  ++revision;
  current = "";
  state = null;
  showView("welcome");
  message("");
  el("welcome-title").focus();
  refresh().catch(error);
  return loadSystem();
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
  renderWelcomeActions();
  const query = el("search").value.trim().toLocaleLowerCase();
  const signature = JSON.stringify([setups, query, pending, connected]);
  if (signature === librarySignature) return;
  librarySignature = signature;
  const focused = el("games").contains(document.activeElement) ?
    document.activeElement.dataset.libraryAction : "";
  const visible = setups.filter(setup => setup.name.toLocaleLowerCase().includes(query))
    .sort((a, b) => a.name.localeCompare(b.name));
  el("games").replaceChildren();
  el("library-status").textContent = !connected ? "Can't reach Winebundle. Reload the page, or open Winebundle again." :
    !setups.length ? "No games yet. Choose an option above to add your first one." :
    !visible.length ? "No games match your search." :
    `${setups.length} game${setups.length === 1 ? "" : "s"}${busy() ? " - a game or installer is running" : ""}`;
  el("search-field").hidden = !setups.length;
  for (const id of ["add-installer", "add-existing"]) el(id).disabled = pending || !connected || busy();
  el("add-status").textContent = connected && busy() ?
    "Another game is installing or running. You can add a game when it finishes." : "";
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
  el("save").disabled = pending || !connected || busy() || !tested() || Boolean(config && config.accepted);
  el("stop").disabled = pending || !connected || !running;
  el("operation-controls").hidden = !running;
  el("play").hidden = !config || !config.accepted;
  el("play").disabled = pending || !connected || busy();
  el("save").hidden = Boolean(config && config.accepted);
  el("to-test").hidden = !config || !config.prepared;
  el("to-test").disabled = pending || Boolean(running);
  el("to-finish").hidden = !tested();
  el("to-finish").disabled = pending || Boolean(running);
  el("retest").disabled = pending || Boolean(running);
  el("finish-title").textContent = config && config.accepted ? "Your game is ready" : "Did the game work?";
  el("finish-help").textContent = config && config.accepted ?
    `Play from your library${shortcutText(state.shortcuts)}. Shortcuts work without this app; change them below.` :
    tested() ? "If you played it and it works, save it to your library with the shortcuts you choose." :
    "Test the game first, then come back here to save it.";
  el("retest").textContent = config && config.accepted ? "Test again" : "Not yet — test again";
  const desktopFolder = Boolean(state && state.shortcuts && state.shortcuts.desktopFolder);
  el("shortcut-menu").disabled = pending || !connected || !config;
  el("shortcut-desktop").disabled = pending || !connected || !config || !desktopFolder;
  if (state && !desktopFolder) {
    el("shortcut-status").textContent = "No desktop folder was found, so a desktop shortcut isn't available.";
  } else if (el("shortcut-status").textContent.startsWith("No desktop folder")) {
    el("shortcut-status").textContent = "";
  }
  const graphics = state && state.graphics;
  el("graphics").hidden = !(graphics && graphics.available);
  if (graphics && graphics.available) {
    el("game-discrete-name").textContent = graphics.name;
    el("game-discrete").disabled = pending || !connected;
    if (!pending) el("game-discrete").checked = graphics.discrete;
  }
  technical();
  el("install-help").textContent = config && config.mode === "existing" ?
    "Winebundle sets up Wine for this game. Your game folder stays where it is." :
    "The installer opens on your desktop. Finish it, then close it and any game it starts. The next step appears when they exit.";
  el("prepare").textContent = config && config.mode === "existing" ? "Set up Wine for this game" : "Open the installer";
  el("setup").querySelector("button[type=submit]").disabled = pending || !connected || busy() ||
    Boolean(sourceWarning && !el("source-anyway").checked);
  el("remove-open").hidden = !config;
  el("remove-open").disabled = pending || !connected || Boolean(running);
  if (running) el("remove-confirm").hidden = true;
  el("remove-yes").disabled = pending || !connected || Boolean(running);
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
        message(`Something went wrong: ${state.error}. The activity log below has details.`, true);
        el("activity").open = true;
      } else if (state.action === "prepare") {
        step = 3;
        message("Ready to test. Check the game's .exe, then select Test game.");
      } else if (state.action === "test") {
        step = 4;
        message("The game exited. If it worked, save it on the Finish step.");
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
    else if (action === "stop") message("Stopping. This can take a few seconds.");
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
  el("add-title").focus();
}
for (const id of ["home", "back-library", "system-back", "welcome-skip"]) el(id).addEventListener("click", home);
el("library-welcome").addEventListener("click", showWelcome);
el("welcome-check-again").addEventListener("click", loadSystem);
el("welcome-check-details").addEventListener("click", showSystem);
for (const id of ["system-open", "system-banner-open"]) el(id).addEventListener("click", showSystem);
el("system-refresh").addEventListener("click", loadSystem);
el("search").addEventListener("input", library);
function addGame(mode = "installer") {
  ++revision;
  current = "";
  state = null;
  el("setup").reset();
  suggestedName = "";
  el("options").open = false;
  runnerProblem = "";
  setMode(mode);
  el("runner").value = defaults.runner;
  checkRunner();
  checkSource();
  showView("workspace");
  el("activity").hidden = true;
  message("");
  step = 1;
  render();
  el("setup").elements.name.focus();
}
el("add-installer").addEventListener("click", () => addGame("installer"));
el("add-existing").addEventListener("click", () => addGame("existing"));
el("welcome-installer").addEventListener("click", () => addGame("installer"));
el("welcome-existing").addEventListener("click", () => addGame("existing"));
// The home screen already asked what kind of file this is, so step 1 only offers a switch.
function setMode(mode) {
  const existing = mode === "existing";
  el("setup").elements.mode.value = mode;
  el("choose-title").textContent = existing ? "Choose the installed game" : "Choose the installer";
  el("choose-help").textContent = existing ?
    "Pick the game's own .exe. Its files stay where they are, so back up your saves first." :
    "Pick the game's offline installer, such as GOG's setup_<game>.exe.";
  el("mode-switch").textContent = existing ? "I have an installer instead" : "The game is already installed";
  el("source-label").textContent = existing ? "Installed game file (.exe)" : "Installer file";
  el("source").placeholder = existing ? "Choose the game's .exe" : "Choose an .exe or .msi installer";
  document.querySelector("[data-browse=source]").setAttribute("aria-label",
    existing ? "Browse for installed game file" : "Browse for installer file");
}
el("mode-switch").addEventListener("click", () => {
  setMode(el("setup").elements.mode.value === "existing" ? "installer" : "existing");
  checkSource();
});
function showRunner() {
  const value = el("runner").value.trim(), problem = runnerProblem;
  const known = system && system.runner === value && system.runnerVersion;
  el("runner-summary").classList.toggle("error", Boolean(problem));
  el("runner-summary-text").textContent = problem ||
    (known ? `Using ${system.runnerVersion.split(" ")[0].replace(/^wine-/, "Wine ")}.` : `Using Wine at ${value}.`);
  el("runner-change").textContent = problem ? "Choose Wine" : "Change";
  if (problem) el("options").open = true;
}
async function checkRunner() {
  clearTimeout(runnerTimer);
  const version = ++runnerRevision, value = el("runner").value.trim();
  const status = el("runner-status");
  let problem = "";
  if (!value) {
    problem = defaults.runner ? `Choose a Wine executable, such as ${defaults.runner}.` :
      "Wine wasn't found. Open System check for install steps, or choose a Wine executable.";
  } else {
    try {
      problem = (await api(`/api/runner?path=${encodeURIComponent(value)}`)).error;
    } catch (exc) {
      problem = "";
    }
  }
  if (version !== runnerRevision) return;
  runnerProblem = problem;
  status.classList.toggle("error", Boolean(problem));
  status.textContent = problem || (value ? "Wine found." : "");
  showRunner();
}
el("runner-change").addEventListener("click", () => {
  el("options").open = true;
  el("runner").focus();
});
// A required field inside closed options can't be focused, so open them first.
el("runner").addEventListener("invalid", () => { el("options").open = true; });
el("runner").addEventListener("input", () => {
  clearTimeout(runnerTimer);
  runnerTimer = setTimeout(checkRunner, 350);
});
function showSourceWarning(warning) {
  const changed = JSON.stringify(warning) !== JSON.stringify(sourceWarning);
  sourceWarning = warning;
  el("source-warning").hidden = !warning;
  if (changed) el("source-anyway").checked = false;
  if (warning) {
    el("source-warning-text").textContent = warning.message;
    el("source-warning-advice").textContent = warning.advice;
  }
  render();
}
function inspectSource(path, mode) {
  return api(`/api/installer-check?path=${encodeURIComponent(path)}&mode=${mode}`);
}
// Fill in the game name from the file, unless the user has typed their own.
function suggestName(name) {
  const field = el("setup").elements.name;
  if (!name || (field.value.trim() && field.value !== suggestedName)) return;
  field.value = suggestedName = name;
}
async function checkSource() {
  clearTimeout(sourceTimer);
  const version = ++sourceRevision, value = el("source").value.trim();
  if (!value.startsWith("/")) {
    showSourceWarning(null);
    return;
  }
  try {
    const result = await inspectSource(value, el("setup").elements.mode.value);
    if (version !== sourceRevision) return;
    suggestName(result.name);
    showSourceWarning(result.warning);
  } catch (exc) {
    if (version === sourceRevision) showSourceWarning(null);
  }
}
el("source").addEventListener("input", () => {
  clearTimeout(sourceTimer);
  sourceTimer = setTimeout(checkSource, 350);
});
el("source-anyway").addEventListener("change", render);

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
    values.allowWebInstaller = Boolean(sourceWarning) && el("source-anyway").checked;
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
  render();
});
for (const id of ["entrypoint", "args"]) el(id).addEventListener("input", render);
el("prepare").addEventListener("click", () => perform("prepare"));
el("test").addEventListener("click", () => {
  perform("test", {entrypoint: entrypointPath(), args: lines(el("args").value)});
});
el("stop").addEventListener("click", () => {
  if (confirm("Stop the game or installer now? Unsaved progress may be lost.")) perform("stop");
});
el("save").addEventListener("click", () => perform("save", {accepted: true,
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
async function updateGraphics() {
  if (!state || !state.graphics) return;
  const id = current, discrete = el("game-discrete").checked, running = state.status === "running";
  pending = true;
  clearTimeout(timer);
  ++revision;
  render();
  let status = "", failed = false;
  try {
    // Matching the app-wide setting means "follow it", so later changes there apply here too.
    await api(`/api/local/${id}/graphics`, {discrete: discrete === state.graphics.default ? null : discrete});
    status = `This game uses ${cardText(discrete, state.graphics.name)}` +
      (running ? " from the next time it starts." : ".");
  } catch (exc) {
    failed = true;
    status = `Couldn't change the graphics card: ${exc.message}`;
  } finally {
    pending = false;
    try { await refresh(); } catch (exc) { error(exc); }
    el("graphics-status").classList.toggle("error", failed);
    el("graphics-status").textContent = status;
  }
}
el("game-discrete").addEventListener("change", updateGraphics);
el("discrete-default").addEventListener("change", async () => {
  const discrete = el("discrete-default").checked, status = el("discrete-default-status");
  el("discrete-default").disabled = true;
  try {
    preferences = await api("/api/preferences", {discreteGpu: discrete});
    status.classList.remove("error");
    status.textContent = `Games use ${cardText(discrete, system.discreteGpu)}, ` +
      "except any you've set differently.";
    if (current) await refresh();
  } catch (exc) {
    el("discrete-default").checked = !discrete;
    status.classList.add("error");
    status.textContent = `Couldn't change the setting: ${exc.message}`;
  } finally {
    el("discrete-default").disabled = false;
  }
});
el("shortcut-menu").addEventListener("change", updateShortcuts);
el("shortcut-desktop").addEventListener("change", updateShortcuts);
el("play").addEventListener("click", () => perform("play"));
// Shown where the window has no title bar, such as on Batocera. The app asks first if
// an install or test is still running.
el("quit").addEventListener("click", () => {
  api("/api/quit", {}).catch(exc => message(`Couldn't close Winebundle: ${exc.message}`, true));
});
el("remove-open").addEventListener("click", () => {
  if (!state) return;
  const config = state.config, existing = config.mode === "existing";
  el("remove-title").textContent = `Remove ${config.name}?`;
  el("remove-text").textContent = !config.prepared && !existing ?
    "Nothing has been installed yet. This removes the unfinished setup." :
    `This deletes ${config.name}'s Wine environment${existing ? "" : ", including the installed game"}, ` +
    "any saves or settings stored in it, and its launcher and shortcuts. This can't be undone.";
  el("remove-kept").textContent = existing ?
    `Your game folder is kept: ${parentOf(config.source)}` : `The installer file is kept: ${config.source}`;
  el("remove-confirm").hidden = false;
  el("remove-title").focus();
});
el("remove-no").addEventListener("click", () => {
  el("remove-confirm").hidden = true;
  el("remove-open").focus();
});
el("remove-yes").addEventListener("click", async () => {
  pending = true;
  clearTimeout(timer);
  ++revision;
  render();
  try {
    const result = await api(`/api/local/${current}/remove`, {});
    pending = false;
    home();
    message(`Removed ${result.name}.`);
  } catch (exc) {
    error(exc);
  } finally {
    pending = false;
    render();
  }
});
el("retry-refresh").addEventListener("click", () => refresh().catch(error));

// Dropping a file: the desktop app passes real paths in from GTK; browsers may offer file:// URIs.
async function dropFiles(paths) {
  showDropZone(false);
  const file = paths.find(path => /\.(exe|msi)$/i.test(path));
  if (el("picker").open) return;
  if (!file) {
    message(paths.length ? "Winebundle needs a Windows game: drop a setup .exe or .msi, or the game's .exe." :
      "Couldn't tell where that file is. Use Browse... to choose it instead.", true);
    return;
  }
  const welcome = !el("welcome").hidden;
  if (pending || el(welcome ? "welcome-installer" : "add-installer").disabled) {
    message(el(welcome ? "welcome-add-status" : "add-status").textContent ||
      "Winebundle can't add a game right now. Try again in a moment.", true);
    return;
  }
  if (!el("workspace").hidden && current) {
    if (step === 3 && /\.exe$/i.test(file)) {
      el("entrypoint").value = toWindows(file);
      render();
      message(`Selected ${file.split("/").pop()} as the game to test.`);
    } else {
      message("Finish this game's setup first, or go back to the library to add another game.", true);
    }
    return;
  }
  let kind = /\.msi$/i.test(file) ? "installer" : null;
  try {
    kind = kind || (await inspectSource(file, "installer")).kind;
  } catch (exc) { /* The file is checked again when the setup is created. */ }
  const inStep1 = !el("workspace").hidden;
  const mode = kind === "installer" ? "installer" :
    inStep1 ? el("setup").elements.mode.value : "existing";
  if (inStep1) setMode(mode);
  else addGame(mode);
  el("source").value = file;
  message("File added. Check the game name, then continue.");
  await checkSource();
  el("setup").elements.name.focus();
}
function showDropZone(visible) { el("drop-zone").hidden = !visible; }
window.winebundleDrop = {hover: showDropZone, files: dropFiles};
const dragsFiles = event => Array.from(event.dataTransfer.types).includes("Files");
document.addEventListener("dragover", event => {
  if (!dragsFiles(event)) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = "copy";
  showDropZone(true);
});
document.addEventListener("dragleave", event => {
  if (!event.relatedTarget) showDropZone(false);
});
document.addEventListener("drop", event => {
  if (!dragsFiles(event)) return;
  event.preventDefault();
  const paths = event.dataTransfer.getData("text/uri-list").split(/\r?\n/)
    .filter(uri => uri.startsWith("file:///"))
    .map(uri => decodeURIComponent(new URL(uri).pathname));
  dropFiles(paths);
});
(async () => {
  try {
    const session = await connect();
    token = session.token;
    defaults = session.local || defaults;
    el("builder-link").hidden = !session.builder;
    el("quit").hidden = !session.quit;
    const [saved] = await Promise.all([api("/api/preferences"), refresh(), loadSystem()]);
    preferences = saved;
    renderGraphicsDefault();
    const firstRun = !preferences.systemChecked;
    if (firstRun) savePreferences({systemChecked: true});
    // New users get the guide until their first game is added; others see what's missing.
    if (!current && el("workspace").hidden) {
      if (firstRun || !setups.length) showView("welcome");
      else if (system && !system.ready) showView("system");
    }
  } catch (exc) {
    connected = false;
    render();
    message(exc.message, true);
  }
})();
