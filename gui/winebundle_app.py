#!/usr/bin/env python3
"""Winebundle desktop app: the game library in its own window."""

import argparse
import json
import os
import secrets
import sys
import threading
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import winebundle_gui  # noqa: E402  (also makes lib/ importable)
from local_setup import applications_folder, desktop_exec  # noqa: E402

APP_ID = "io.github.jacobm36.Winebundle"
PROJECT = Path(__file__).resolve().parent.parent
ICON = PROJECT / "data" / f"{APP_ID}.svg"
LAUNCHER = PROJECT / "bin" / "winebundle"
SYSTEM_PREFIX = Path("/usr")


def system_install(project=PROJECT):
    """True when Winebundle runs from a package, which ships its own menu entry."""
    return Path(project).is_relative_to(SYSTEM_PREFIX)


def desktop_entry(launcher=LAUNCHER):
    return ("[Desktop Entry]\nType=Application\nName=Winebundle\n"
            "GenericName=Windows Game Launcher\n"
            "Comment=Install and play Windows games with Wine\n"
            f'Exec="{desktop_exec(str(launcher))}"\nIcon={APP_ID}\nTerminal=false\n'
            "Categories=Game;\nKeywords=wine;windows;games;launcher;\n"
            f"StartupNotify=true\nStartupWMClass={APP_ID}\n")


def menu_files(data_home=None):
    data_home = Path(data_home) if data_home else applications_folder().parent
    return (data_home / "applications" / f"{APP_ID}.desktop",
            data_home / "icons/hicolor/scalable/apps" / f"{APP_ID}.svg")


def install_menu_entry(data_home=None, launcher=LAUNCHER):
    entry, icon = menu_files(data_home)
    for path in (entry, icon):
        path.parent.mkdir(parents=True, exist_ok=True)
    icon.write_bytes(ICON.read_bytes())
    temporary = entry.with_suffix(".tmp")
    temporary.write_text(desktop_entry(launcher))
    temporary.chmod(0o644)
    temporary.replace(entry)
    return entry, icon


def remove_menu_entry(data_home=None):
    removed = []
    for path in menu_files(data_home):
        if path.exists():
            path.unlink()
            removed.append(path)
    return removed


def close_warning(busy):
    """Explain what closing would interrupt, or return None when closing is safe.

    Games and installers run in their own process sessions, so they keep running
    after the window closes; only the result of an install or test would be lost.
    """
    if not busy or busy.get("action") == "play":
        return None
    step = "Installing" if busy.get("action") == "prepare" else "Testing"
    return (f"{step} {busy.get('name', 'a game')} is still running",
            "If you close Winebundle now, this step's result won't be recorded and "
            "you'll need to repeat it. The installer or game itself will stay open.")


class QuietHandler(winebundle_gui.RequestHandler):
    def log_message(self, format_string, *args):
        pass


def start_server(handler=QuietHandler):
    # A random port avoids clashes; preferences are stored server-side so nothing depends on it.
    # The VM bundle builder is a developer tool, so the app doesn't serve it.
    server = winebundle_gui.WinebundleServer(
        ("127.0.0.1", 0), handler, secrets.token_urlsafe(32), builder=False)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/"


def drop_script(name, value):
    """JavaScript that hands a drag-and-drop event to the page."""
    return f"window.winebundleDrop && window.winebundleDrop.{name}({json.dumps(value)});"


def dropped_paths(files):
    """Local paths of dropped files; files from network locations have none."""
    return [path for path in (item.get_path() for item in files) if path]


def add_file_drop(view, Gtk):
    """Accept files dragged onto the window, passing their real paths to the page.

    Web pages can't see where a dropped file lives, so GTK takes the drop first.
    """
    from gi.repository import Gdk

    def run(name, value):
        view.evaluate_javascript(drop_script(name, value), -1, None, None, None, None, None)

    def entered(*_):
        run("hover", True)
        return Gdk.DragAction.COPY

    def dropped(target, value, x, y):
        run("files", dropped_paths(value.get_files()))
        return True

    target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
    target.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    target.connect("enter", entered)
    target.connect("motion", lambda *_: Gdk.DragAction.COPY)
    target.connect("leave", lambda *_: run("hover", False))
    target.connect("drop", dropped)
    view.add_controller(target)
    return target


def webkit_sandbox_blocked(root=Path("/")):
    """True when Ubuntu's AppArmor userns restriction stops WebKit's bubblewrap sandbox."""
    try:
        restricted = (root / "proc/sys/kernel/apparmor_restrict_unprivileged_userns"
                      ).read_text().strip() == "1"
    except OSError:
        return False
    return restricted and not (root / "etc/apparmor.d/bwrap-userns-restrict").exists()


def load_toolkit():
    try:
        import gi
    except ImportError:
        return None
    for webkit, gtk in (("WebKit", "4.0"), ("WebKit2", "3.0")):
        try:
            gi.require_version(webkit, "6.0" if webkit == "WebKit" else "4.1")
            gi.require_version("Gtk", gtk)
            from gi.repository import Gtk
            module = __import__("gi.repository", fromlist=[webkit])
            return Gtk, getattr(module, webkit), gtk == "4.0"
        except (ValueError, ImportError):
            continue
    return None


def run_window(toolkit):
    Gtk, WebKit, gtk4 = toolkit
    from gi.repository import Gio, GLib

    GLib.set_prgname(APP_ID)
    GLib.set_application_name("Winebundle")
    app = Gtk.Application(application_id=APP_ID)
    state = {"window": None, "server": None, "closing": False}

    def open_externally(uri):
        if uri.startswith(("http://", "https://")):
            try:
                Gio.AppInfo.launch_default_for_uri(uri, None)
            except GLib.Error:
                pass

    def decide_policy(view, decision, kind, base):
        types = WebKit.PolicyDecisionType
        if kind not in (types.NAVIGATION_ACTION, types.NEW_WINDOW_ACTION):
            return False
        uri = decision.get_navigation_action().get_request().get_uri()
        if uri.startswith(base) and kind == types.NAVIGATION_ACTION:
            return False
        decision.ignore()
        if uri.startswith(base):
            view.load_uri(uri)
        else:
            open_externally(uri)
        return True

    def confirm_close(window, warning):
        def finish(accepted):
            if accepted:
                state["closing"] = True
                window.close()

        title, detail = warning
        if gtk4 and hasattr(Gtk, "AlertDialog"):
            dialog = Gtk.AlertDialog(message=title, detail=detail, modal=True,
                                     buttons=["Keep Winebundle Open", "Close Anyway"],
                                     cancel_button=0, default_button=0)

            def chosen(dialog, result):
                try:
                    finish(dialog.choose_finish(result) == 1)
                except GLib.Error:
                    pass

            dialog.choose(window, None, chosen)
            return
        dialog = Gtk.MessageDialog(transient_for=window, modal=True,
                                   message_type=Gtk.MessageType.WARNING,
                                   buttons=Gtk.ButtonsType.NONE, text=title,
                                   secondary_text=detail)
        dialog.add_buttons("Keep Winebundle Open", Gtk.ResponseType.CANCEL,
                           "Close Anyway", Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)

        def responded(dialog, response):
            dialog.destroy()
            finish(response == Gtk.ResponseType.ACCEPT)

        dialog.connect("response", responded)
        dialog.present()

    def close_requested(window, *_):
        if state["closing"]:
            return False
        warning = close_warning(state["server"].local.busy())
        if warning is None:
            return False
        confirm_close(window, warning)
        return True

    def activate(app):
        if state["window"]:
            state["window"].present()
            return
        server, url = start_server()
        state["server"] = server
        if ICON.parent.is_dir():
            if gtk4:
                from gi.repository import Gdk
                Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(
                    str(ICON.parent))
            else:
                Gtk.IconTheme.get_default().append_search_path(str(ICON.parent))
        window = Gtk.ApplicationWindow(application=app, title="Winebundle")
        window.set_default_size(1180, 820)
        window.set_icon_name(APP_ID)
        view = WebKit.WebView()
        view.connect("decide-policy", decide_policy, url)
        view.connect("notify::title",
                     lambda view, _: window.set_title(view.get_title() or "Winebundle"))
        if gtk4:
            add_file_drop(view, Gtk)
            window.set_child(view)
            window.connect("close-request", close_requested)
        else:
            window.add(view)
            window.connect("delete-event", close_requested)
            window.show_all()
        view.load_uri(server.launch_url(url))
        state["window"] = window
        window.present()

    def shutdown(app):
        if state["server"]:
            state["server"].shutdown()
            state["server"].server_close()

    quit_action = Gio.SimpleAction.new("quit", None)
    quit_action.connect("activate", lambda *_: state["window"] and state["window"].close())
    app.add_action(quit_action)
    app.set_accels_for_action("app.quit", ["<Control>q"])
    app.connect("activate", activate)
    app.connect("shutdown", shutdown)
    return app.run([sys.argv[0]])


def run_browser():
    server, url = start_server(winebundle_gui.RequestHandler)
    url = server.launch_url(url)
    print(f"Winebundle: open {url} in your web browser (Ctrl+C to stop).\n"
          "This link works once.", flush=True)
    threading.Timer(0.25, webbrowser.open, args=(url,)).start()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\nWinebundle: stopping")
    finally:
        server.shutdown()
        server.server_close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="winebundle", description="Install and play Windows games with Wine.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--install-menu-entry", action="store_true",
                       help="add Winebundle to the application menu")
    group.add_argument("--remove-menu-entry", action="store_true",
                       help="remove Winebundle from the application menu")
    group.add_argument("--browser", action="store_true",
                       help="open in the web browser instead of an app window")
    args = parser.parse_args(argv)

    if args.install_menu_entry and system_install():
        print("Winebundle is installed as a package, so it's already in the "
              "application menu.")
        return 0
    if args.install_menu_entry:
        entry, icon = install_menu_entry()
        print(f"Added Winebundle to the application menu:\n  {entry}\n  {icon}")
        return 0
    if args.remove_menu_entry:
        removed = remove_menu_entry()
        print("Removed:\n" + "\n".join(f"  {path}" for path in removed) if removed
              else "Winebundle was not in the application menu.")
        return 0
    toolkit = None if args.browser else load_toolkit()
    if toolkit:
        if webkit_sandbox_blocked():
            # The window only loads Winebundle's own loopback page (strict CSP, and
            # any other link opens in the user's browser), so running WebKit without
            # its sandbox is the only way to start on Ubuntu 24.04's default policy.
            os.environ["WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS"] = "1"
        return run_window(toolkit)
    if not args.browser:
        check = winebundle_gui.system_check
        commands = check.WEBKIT_INSTALL.get(check.distro().get("family", ""))
        hint = commands[0] if commands else "your distribution's WebKitGTK 6.0 package"
        print("Winebundle: the app window needs WebKitGTK; opening in your web browser "
              f"instead.\nTo get the app window, install: {hint}", file=sys.stderr)
    return run_browser()


if __name__ == "__main__":
    sys.exit(main())
