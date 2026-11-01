"""Find a laptop's discrete graphics card and the environment that makes games use it.

Asks switcheroo-control first, which is what GNOME's "Launch using Discrete Graphics
Card" uses, then falls back to reading sysfs. Read-only; never changes the system.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

# Variables any card selection may set; cleared first so an inherited choice can't leak.
VARIABLES = ("DRI_PRIME", "__NV_PRIME_RENDER_OFFLOAD", "__GLX_VENDOR_LIBRARY_NAME",
             "__VK_LAYER_NV_optimus")
NVIDIA_OFFLOAD = {"__NV_PRIME_RENDER_OFFLOAD": "1", "__GLX_VENDOR_LIBRARY_NAME": "nvidia",
                  "__VK_LAYER_NV_optimus": "NVIDIA_only"}
VENDORS = {"0x10de": "NVIDIA", "0x1002": "AMD", "0x8086": "Intel"}
MESA_DRIVERS = {"amdgpu", "radeon", "i915", "xe", "nouveau"}


def friendly_name(name):
    """Turn "NVIDIA Corporation TU117GLM [Quadro T500 Mobile]" into "NVIDIA Quadro T500 Mobile"."""
    brackets = re.findall(r"\[([^\]]+)\]", name)
    model = brackets[-1] if brackets else name
    vendor = next((short for prefix, short in (("nvidia", "NVIDIA"), ("advanced micro", "AMD"),
                                               ("amd", "AMD"), ("intel", "Intel"))
                   if name.lower().startswith(prefix)), "")
    if vendor and not model.lower().startswith(vendor.lower()):
        model = f"{vendor} {model}"
    return model.strip()


def _switcheroo():
    busctl = shutil.which("busctl")
    if not busctl:
        return None
    try:
        result = subprocess.run(
            [busctl, "--system", "--json=short", "get-property", "net.hadess.SwitcherooControl",
             "/net/hadess/SwitcherooControl", "net.hadess.SwitcherooControl", "GPUs"],
            capture_output=True, text=True, timeout=3, stdin=subprocess.DEVNULL, check=False)
        cards = json.loads(result.stdout)["data"] if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(cards, list):
        return None
    for card in cards:
        try:
            default = card["Default"]["data"]
            values = card["Environment"]["data"]
            name = card["Name"]["data"]
        except (KeyError, TypeError):
            continue
        if default or not values or len(values) % 2:
            continue
        return {"name": friendly_name(name), "env": dict(zip(values[::2], values[1::2]))}
    return {} if len(cards) else None


def _sysfs(root):
    cards = []
    for card in sorted((root / "sys/class/drm").glob("card*")):
        if not re.fullmatch(r"card\d+", card.name):
            continue
        device = card / "device"
        try:
            vendor = (device / "vendor").read_text().strip().lower()
        except OSError:
            continue
        try:
            boot = (device / "boot_vga").read_text().strip() == "1"
        except OSError:
            boot = False
        driver = Path(os.path.realpath(device / "driver")).name if (device / "driver").exists() else ""
        real = os.path.realpath(device)
        if real not in (item["path"] for item in cards):
            cards.append({"vendor": VENDORS.get(vendor, "Other"), "driver": driver,
                          "boot": boot, "path": real, "address": Path(real).name})
    if len(cards) < 2 or not any(card["boot"] for card in cards):
        return None
    for card in cards:
        if card["boot"]:
            continue
        name = f"{card['vendor']} graphics card" if card["vendor"] != "Other" else "Discrete graphics card"
        if card["driver"] == "nvidia":
            return {"name": name, "env": dict(NVIDIA_OFFLOAD)}
        if card["driver"] in MESA_DRIVERS:
            return {"name": name, "env": {"DRI_PRIME": "pci-" + re.sub(r"[:.]", "_", card["address"])}}
    return None


def discrete_gpu(root=Path("/")):
    """Return {"name", "env"} for a second, more powerful card, or None when there isn't one."""
    root = Path(root)
    if root == Path("/"):
        found = _switcheroo()
        if found is not None:
            return found or None
    return _sysfs(root)
