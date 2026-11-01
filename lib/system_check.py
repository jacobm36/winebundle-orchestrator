"""Read-only checks of what this computer needs to install and play Windows games.

Nothing here installs software or needs administrator rights. Fixes are returned
as distribution-specific commands for the user to review and run themselves.
"""
import importlib.util
import os
import re
import shutil
import subprocess
from pathlib import Path

import batocera
import graphics
import local_setup

MIN_FREE_BYTES = 10 * 1024 ** 3
GPU_VENDORS = {"0x10de": "NVIDIA", "0x1002": "AMD", "0x8086": "Intel"}
# Vulkan driver libraries for the kernel drivers that commonly run games.
VULKAN_DRIVERS = {"nvidia": "libGLX_nvidia.so.0", "i915": "libvulkan_intel.so",
                  "xe": "libvulkan_intel.so", "amdgpu": "libvulkan_radeon.so",
                  "nouveau": "libvulkan_nouveau.so"}
LIBRARY_DIRS = {
    64: ("usr/lib/x86_64-linux-gnu", "lib/x86_64-linux-gnu", "usr/lib64", "usr/lib"),
    32: ("usr/lib/i386-linux-gnu", "lib/i386-linux-gnu", "usr/lib32", "usr/lib"),
}
FAMILIES = (
    ("debian", {"debian", "ubuntu"}),
    ("fedora", {"fedora", "rhel", "centos"}),
    ("arch", {"arch", "archlinux"}),
    ("suse", {"suse", "opensuse", "opensuse-leap", "opensuse-tumbleweed", "sles"}),
)
DEBIAN_I386 = ["sudo dpkg --add-architecture i386", "sudo apt update"]
WINE_INSTALL = {
    "debian": DEBIAN_I386 + ["sudo apt install wine wine32:i386"],
    "fedora": ["sudo dnf install wine"],
    "arch": ["sudo pacman -S wine"],
    "suse": ["sudo zypper install wine"],
}
WINE32_INSTALL = {
    "debian": DEBIAN_I386 + ["sudo apt install wine32:i386"],
    "suse": ["sudo zypper install wine-32bit"],
}
WINETRICKS_INSTALL = {
    "debian": ["sudo apt install winetricks"],
    "fedora": ["sudo dnf install winetricks"],
    "arch": ["sudo pacman -S winetricks"],
    "suse": ["sudo zypper install winetricks"],
}
WEBKIT_INSTALL = {
    "debian": ["sudo apt install python3-gi gir1.2-webkit-6.0"],
    "fedora": ["sudo dnf install python3-gobject webkitgtk6.0"],
    "arch": ["sudo pacman -S python-gobject webkitgtk-6.0"],
    "suse": ["sudo zypper install python3-gobject typelib-1_0-WebKit-6_0"],
}
WEBKIT_TYPELIBS = ("WebKit-6.0.typelib", "WebKit2-4.1.typelib")
ARCH_VULKAN = {"nvidia": "nvidia-utils", "i915": "vulkan-intel", "xe": "vulkan-intel",
               "amdgpu": "vulkan-radeon", "nouveau": "vulkan-nouveau"}
WINEHQ = "https://wiki.winehq.org/Download"


def distro(root=Path("/")):
    text = ""
    for candidate in ("etc/os-release", "usr/lib/os-release"):
        try:
            text = (root / candidate).read_text()
            break
        except OSError:
            continue
    info = {}
    for line in text.splitlines():
        match = re.fullmatch(r"([A-Z_]+)=(.*)", line.strip())
        if match:
            info[match.group(1)] = match.group(2).strip("\"'")
    ids = [info.get("ID", "")] + info.get("ID_LIKE", "").split()
    family = next((name for name, members in FAMILIES if any(i in members for i in ids)), "")
    return {"name": info.get("PRETTY_NAME") or info.get("NAME") or "Linux",
            "id": info.get("ID", ""), "family": family}


def elf_bits(path):
    try:
        with open(path, "rb") as stream:
            head = stream.read(5)
    except OSError:
        return 0
    return {1: 32, 2: 64}.get(head[4], 0) if len(head) == 5 and head[:4] == b"\x7fELF" else 0


def has_library(root, name, bits):
    return any(elf_bits(root / folder / name) == bits for folder in LIBRARY_DIRS[bits])


def gpus(root=Path("/")):
    found = []
    for card in sorted((root / "sys/class/drm").glob("card*")):
        if not re.fullmatch(r"card\d+", card.name):
            continue
        device = card / "device"
        try:
            vendor = (device / "vendor").read_text().strip().lower()
        except OSError:
            continue
        driver = Path(os.path.realpath(device / "driver")).name if (device / "driver").exists() else ""
        gpu = {"vendor": GPU_VENDORS.get(vendor, "Other"), "driver": driver}
        if gpu not in found:
            found.append(gpu)
    return found


def nvidia_major(root=Path("/")):
    try:
        text = (root / "proc/driver/nvidia/version").read_text()
    except OSError:
        return ""
    match = re.search(r"Kernel Module\s+(?:for \S+\s+)?(\d+)\.\d+", text)
    return match.group(1) if match else ""


def wine_32bit(runner):
    """Return "32-bit", "wow64", or "" for the Wine installation containing runner."""
    base = Path(os.path.realpath(runner)).parent.parent
    for relative in ("lib/wine/i386-unix", "lib32/wine/i386-unix",
                     "lib/i386-linux-gnu/wine/i386-unix"):
        if (base / relative).is_dir():
            return "32-bit"
    # WoW64 builds run 32-bit programs from 64-bit Unix libraries.
    folders = [base / "lib/wine", base / "lib64/wine"]
    if any((f / "x86_64-unix").is_dir() for f in folders) and any(
            (f / "i386-windows").is_dir() for f in folders):
        return "wow64"
    return ""


def fix(text, commands=None):
    return {"text": text, "commands": list(commands or [])}


def item(check_id, title, status, detail, required=False, remedy=None):
    return {"id": check_id, "title": title, "status": status, "detail": detail,
            "required": required, "fix": remedy}


def check_wine(family):
    runner = local_setup.default_runner()
    on_batocera = family == "batocera"
    if not runner:
        if on_batocera:
            return item("wine", "Wine", "error",
                        f"None of Batocera's Wine versions were found in {batocera.SYSTEM_WINE} "
                        f"or {batocera.CUSTOM_WINE}.", True,
                        fix("Update Batocera, or add a Wine version to "
                            f"{batocera.CUSTOM_WINE}.")), ""
        return item("wine", "Wine", "error",
                    "Wine wasn't found at /usr/bin/wine or on your PATH. It's needed to "
                    "run Windows games. You can also choose another Wine build per game.",
                    True, fix("Install Wine from your distribution's packages, or see "
                              f"{WINEHQ} for newer versions.", WINE_INSTALL.get(family))), ""
    env = {**os.environ, **batocera.wine_environment(runner)} if on_batocera else None
    try:
        result = subprocess.run([runner, "--version"], stdin=subprocess.DEVNULL, env=env,
                                capture_output=True, text=True, timeout=15)
        version = result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        version = ""
    if not version:
        return item("wine", "Wine", "warning",
                    f"Wine was found at {runner}, but it didn't report its version. "
                    "It may be damaged or incomplete.", True,
                    fix("Choose another Wine version when adding a game." if on_batocera
                        else "Reinstall Wine.", WINE_INSTALL.get(family))), runner
    match = re.search(r"wine-(\d+)", version)
    if match and int(match.group(1)) < 8:
        result = item("wine", "Wine", "warning",
                      f"{version} at {runner}. This is an older release; many recent games "
                      "need Wine 8 or newer.", True,
                      fix(f"Install a newer Wine. See {WINEHQ} for your distribution."))
    else:
        result = item("wine", "Wine", "ok", f"{version} at {runner}, with its wineserver.", True)
    return {**result, "version": version}, runner


def check_wine32(runner, family):
    title = "32-bit Windows programs"
    if not runner:
        return item("wine32", title, "skipped", "Checked after Wine is installed.")
    support = wine_32bit(runner)
    if support == "32-bit":
        return item("wine32", title, "ok", "Wine's 32-bit support is installed.")
    if support == "wow64":
        return item("wine32", title, "ok", "This Wine runs 32-bit programs in WoW64 mode.")
    text = ("Wine's 32-bit support wasn't found. Many games and most installers are "
            "32-bit and won't start without it.")
    if family == "arch":
        remedy = fix("Update to Arch's current wine package, which runs 32-bit programs "
                     "in WoW64 mode, or enable the multilib repository.",
                     ["sudo pacman -Syu wine"])
    elif family == "fedora":
        remedy = fix("Install Wine's 32-bit packages (wine-core.i686 on older Fedora releases).")
    elif family == "batocera":
        remedy = fix("Choose another Wine version when adding a game.")
    else:
        remedy = fix("Install Wine's 32-bit support.", WINE32_INSTALL.get(family))
    return item("wine32", title, "warning", text, remedy=remedy)


def check_gpu(distro_id, cards):
    title = "Graphics driver"
    if not cards:
        return item("gpu", title, "warning",
                    "No graphics card was detected. Games need a GPU with working drivers.")
    names = ", ".join(f"{card['vendor']} ({card['driver'] + ' driver' if card['driver'] else 'no driver'})"
                      for card in cards)
    if any(card["driver"] == "nouveau" for card in cards):
        commands = ["sudo ubuntu-drivers install"] if distro_id == "ubuntu" else []
        return item("gpu", title, "warning",
                    f"{names}. The open-source nouveau driver runs most games slowly or "
                    "not at all.", remedy=fix("Install NVIDIA's driver, then restart.", commands))
    if not any(card["driver"] for card in cards):
        return item("gpu", title, "warning", f"{names}. No graphics driver is loaded.",
                    remedy=fix("Install your GPU's driver, then restart."))
    return item("gpu", title, "ok", names + ".")


def vulkan_missing(root, cards, bits):
    missing = [] if has_library(root, "libvulkan.so.1", bits) else ["loader"]
    for driver in dict.fromkeys(card["driver"] for card in cards):
        library = VULKAN_DRIVERS.get(driver)
        if library and not has_library(root, library, bits):
            missing.append(driver)
    return missing


def vulkan_commands(family, distro_id, missing, bits, nvidia):
    drivers = [d for d in missing if d != "loader"]
    if family == "debian":
        suffix = ":i386" if bits == 32 else ""
        packages = (["libvulkan1" + suffix] if "loader" in missing else [])
        if any(d != "nvidia" for d in drivers):
            packages.append("mesa-vulkan-drivers" + suffix)
        if "nvidia" in drivers:
            if bits == 32 and nvidia:
                packages.append(f"libnvidia-gl-{nvidia}:i386")
            elif distro_id == "ubuntu" and bits == 64:
                return ["sudo ubuntu-drivers install"] + (
                    ["sudo apt install " + " ".join(packages)] if packages else [])
        if not packages:
            return []
        return (DEBIAN_I386 if bits == 32 else []) + ["sudo apt install " + " ".join(packages)]
    if family == "arch":
        prefix = "lib32-" if bits == 32 else ""
        packages = ([prefix + "vulkan-icd-loader"] if "loader" in missing else []) + [
            prefix + ARCH_VULKAN[d] for d in dict.fromkeys(drivers)]
        return ["sudo pacman -S " + " ".join(dict.fromkeys(packages))]
    if family == "fedora":
        suffix = ".i686" if bits == 32 else ""
        packages = (["vulkan-loader" + suffix] if "loader" in missing else []) + (
            ["mesa-vulkan-drivers" + suffix] if any(d != "nvidia" for d in drivers) else [])
        return ["sudo dnf install " + " ".join(packages)] if packages else []
    return []


def check_vulkan(root, family, distro_id, cards, bits):
    title = "Vulkan graphics" + (" (32-bit)" if bits == 32 else "")
    check_id = "vulkan32" if bits == 32 else "vulkan"
    missing = vulkan_missing(root, cards, bits)
    purpose = ("Needed by DXVK for 32-bit Direct3D games." if bits == 32 else
               "Needed by DXVK, which makes many Direct3D games run faster.")
    if not missing:
        return item(check_id, title, "ok", f"Vulkan is installed. {purpose}")
    names = ["the Vulkan loader" if d == "loader" else f"the {d} Vulkan driver" for d in missing]
    commands = vulkan_commands(family, distro_id, missing, bits, nvidia_major(root))
    text = "Install the missing Vulkan packages."
    if "nvidia" in missing and not commands:
        text = "Install your NVIDIA driver's matching Vulkan libraries."
    return item(check_id, title, "warning",
                f"Missing {', '.join(names)}. {purpose}", remedy=fix(text, commands))


def nearest_existing(path):
    path = Path(path)
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def check_disk(games_root):
    folder = nearest_existing(games_root)
    try:
        free = shutil.disk_usage(folder).free
    except OSError:
        return item("disk", "Free disk space", "warning",
                    f"Couldn't check free space for {games_root}.")
    amount = f"{free / 1024 ** 3:.0f} GB free where games are stored."
    if free < MIN_FREE_BYTES:
        return item("disk", "Free disk space", "warning",
                    f"Only {amount} Most games need 10 GB or more.",
                    remedy=fix("Free up space before installing large games."))
    return item("disk", "Free disk space", "ok", amount)


def check_shortcuts():
    title = "Menu and desktop shortcuts"
    menu = os.access(nearest_existing(local_setup.applications_folder()), os.W_OK)
    desktop = local_setup.desktop_folder()
    if not menu:
        return item("shortcuts", title, "warning",
                    "The application menu folder isn't writable, so menu shortcuts can't be added.")
    parts = ["Application menu shortcuts are available."]
    if desktop:
        parts.append("Desktop shortcuts are available.")
        if not shutil.which("gio"):
            parts.append("gio wasn't found, so you may need to choose Allow Launching on new desktop icons.")
    else:
        parts.append("No desktop folder was found, so desktop shortcuts aren't available.")
    return item("shortcuts", title, "ok", " ".join(parts))


def check_winetricks(family):
    title = "winetricks (optional)"
    path = shutil.which("winetricks")
    if path:
        return item("winetricks", title, "ok",
                    f"Installed at {path}. Useful for adding Windows components when troubleshooting.")
    return item("winetricks", title, "optional",
                "Not installed. It's useful for adding Windows components such as fonts or "
                "Visual C++ runtimes when a game needs them.",
                remedy=fix("Install winetricks.", WINETRICKS_INSTALL.get(family)))


def check_app_window(root, family, has_gi=None):
    title = "App window (optional)"
    if has_gi is None:
        has_gi = importlib.util.find_spec("gi") is not None
    found = has_gi and any((root / folder / "girepository-1.0" / name).is_file()
                           for folder in LIBRARY_DIRS[64] for name in WEBKIT_TYPELIBS)
    if found:
        return item("app-window", title, "ok",
                    "WebKitGTK is installed, so Winebundle opens in its own window.")
    return item("app-window", title, "optional",
                "WebKitGTK isn't installed, so Winebundle opens in your web browser instead "
                "of its own window. Everything still works.",
                remedy=fix("Install WebKitGTK, then reopen Winebundle.",
                           WEBKIT_INSTALL.get(family)))


def check_batocera():
    title = "Batocera Windows games"
    folder = nearest_existing(batocera.ROMS)
    if not os.access(folder, os.W_OK):
        return item("batocera", title, "error",
                    f"{batocera.ROMS} isn't writable, so games can't be added to Batocera's "
                    "Windows menu.", True)
    parts = [f"Games are installed in {batocera.ROMS} and appear in Batocera's Windows menu."]
    if shutil.which("mksquashfs"):
        parts.append("Saved games can be compressed to .wsquashfs files.")
    else:
        parts.append("mksquashfs wasn't found, so games can't be compressed.")
    return item("batocera", title, "ok", " ".join(parts))


def run(games_root=None, root=Path("/")):
    system = distro(root)
    family, distro_id = system["family"], system["id"]
    on_batocera = batocera.active(root)
    if on_batocera:
        family = "batocera"
    cards = gpus(root)
    wine, runner = check_wine(family)
    version = wine.pop("version", "")
    if on_batocera:
        # Batocera ships its own Wine, Vulkan and window libraries and has no package manager.
        checks = [wine, check_wine32(runner, family), check_gpu(distro_id, cards),
                  check_disk(batocera.ROMS), check_batocera()]
    else:
        checks = [
            wine,
            check_wine32(runner, family),
            check_gpu(distro_id, cards),
            check_vulkan(root, family, distro_id, cards, 64),
            check_vulkan(root, family, distro_id, cards, 32),
            check_disk(games_root or Path.home() / ".local/share/winebundle/local"),
            check_shortcuts(),
            check_winetricks(family),
            check_app_window(root, family),
        ]
    errors = sum(check["status"] == "error" for check in checks)
    warnings = sum(check["status"] == "warning" for check in checks)
    card = graphics.discrete_gpu(root)
    return {"distro": system, "checks": checks, "runner": runner,
            "discreteGpu": card["name"] if card else "",
            "runnerVersion": version, "ready": not errors,
            "errors": errors, "warnings": warnings}
