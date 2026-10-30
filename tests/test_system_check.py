import os
import sys
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import local_setup  # noqa: E402
import system_check  # noqa: E402


def elf(path, bits):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x7fELF" + bytes([1 if bits == 32 else 2]) + b"\0" * 11)


class SystemCheckTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.desktop = self.root / "home/Desktop"
        self.desktop.mkdir(parents=True)
        patches = [
            patch.object(local_setup, "applications_folder",
                         return_value=self.root / "home/.local/share/applications"),
            patch.object(local_setup, "desktop_folder", return_value=self.desktop),
            patch.object(system_check.shutil, "which",
                         side_effect=lambda name: f"/usr/bin/{name}"),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def tearDown(self):
        self.temporary.cleanup()

    def os_release(self, text):
        (self.root / "etc").mkdir(exist_ok=True)
        (self.root / "etc/os-release").write_text(text)

    def wine(self, version="wine-9.0", layout=("lib/i386-linux-gnu/wine/i386-unix",)):
        bin_dir = self.root / "usr/bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        for name in ("wine", "wineserver"):
            (bin_dir / name).write_text(f"#!/bin/sh\necho {version}\n")
            (bin_dir / name).chmod(0o755)
        for folder in layout:
            (self.root / "usr" / folder).mkdir(parents=True)
        return str(bin_dir / "wine")

    def gpu(self, card, vendor, driver):
        device = self.root / "sys/class/drm" / card / "device"
        device.mkdir(parents=True)
        (device / "vendor").write_text(vendor + "\n")
        target = self.root / "sys/bus/pci/drivers" / driver
        target.mkdir(parents=True, exist_ok=True)
        (device / "driver").symlink_to(target)

    def run_checks(self, runner):
        with patch.object(local_setup, "default_runner", return_value=runner):
            result = system_check.run(self.root / "games", self.root)
        return result, {check["id"]: check for check in result["checks"]}

    def test_distro_families(self):
        for text, family in (('ID=linuxmint\nID_LIKE="ubuntu debian"\n', "debian"),
                             ("ID=manjaro\nID_LIKE=arch\n", "arch"),
                             ('ID="opensuse-tumbleweed"\nID_LIKE="opensuse suse"\n', "suse"),
                             ("ID=nobara\nID_LIKE=fedora\n", "fedora"),
                             ("ID=gentoo\n", "")):
            with self.subTest(text=text):
                self.os_release(text)
                self.assertEqual(system_check.distro(self.root)["family"], family)
        (self.root / "etc/os-release").unlink()
        self.assertEqual(system_check.distro(self.root)["name"], "Linux")

    def test_ready_system_on_hybrid_graphics(self):
        self.os_release('PRETTY_NAME="Ubuntu 24.04 LTS"\nID=ubuntu\nID_LIKE=debian\n')
        runner = self.wine()
        self.gpu("card1", "0x8086", "i915")
        self.gpu("card2", "0x10de", "nvidia")
        (self.root / "sys/class/drm/card1-eDP-1").mkdir()
        for bits, folder in ((64, "usr/lib/x86_64-linux-gnu"), (32, "usr/lib/i386-linux-gnu")):
            for library in ("libvulkan.so.1", "libvulkan_intel.so", "libGLX_nvidia.so.0"):
                elf(self.root / folder / library, bits)
        result, checks = self.run_checks(runner)
        self.assertEqual({key: check["status"] for key, check in checks.items()},
                         dict.fromkeys(checks, "ok"))
        self.assertTrue(result["ready"])
        self.assertEqual((result["errors"], result["warnings"]), (0, 0))
        self.assertIn("wine-9.0", checks["wine"]["detail"])
        self.assertEqual(result["runner"], runner)
        self.assertEqual(checks["gpu"]["detail"], "Intel (i915 driver), NVIDIA (nvidia driver).")
        self.assertEqual(result["distro"]["name"], "Ubuntu 24.04 LTS")

    def test_missing_dependencies_give_distribution_commands(self):
        self.os_release("ID=ubuntu\nID_LIKE=debian\n")
        self.gpu("card0", "0x10de", "nvidia")
        (self.root / "proc/driver/nvidia").mkdir(parents=True)
        (self.root / "proc/driver/nvidia/version").write_text(
            "NVRM version: NVIDIA UNIX Open Kernel Module for x86_64  580.178.04  Release Build\n")
        for library in ("libvulkan.so.1", "libGLX_nvidia.so.0"):
            elf(self.root / "usr/lib/x86_64-linux-gnu" / library, 64)
        # A 64-bit library in a 32-bit search folder must not count as 32-bit support.
        elf(self.root / "usr/lib/libvulkan.so.1", 64)
        result, checks = self.run_checks("")
        self.assertFalse(result["ready"])
        self.assertEqual(checks["wine"]["status"], "error")
        self.assertIn("sudo apt install wine wine32:i386", checks["wine"]["fix"]["commands"])
        self.assertEqual(checks["wine32"]["status"], "skipped")
        self.assertEqual(checks["vulkan"]["status"], "ok")
        self.assertEqual(checks["vulkan32"]["status"], "warning")
        self.assertEqual(checks["vulkan32"]["fix"]["commands"][-1],
                         "sudo apt install libvulkan1:i386 libnvidia-gl-580:i386")

    def test_nouveau_old_wine_and_missing_32bit_support(self):
        self.os_release("ID=ubuntu\nID_LIKE=debian\n")
        self.gpu("card0", "0x10de", "nouveau")
        runner = self.wine("wine-6.0.3", layout=())
        result, checks = self.run_checks(runner)
        self.assertTrue(result["ready"])
        self.assertEqual(checks["wine"]["status"], "warning")
        self.assertIn("Wine 8 or newer", checks["wine"]["detail"])
        self.assertEqual(checks["wine32"]["status"], "warning")
        self.assertIn("sudo apt install wine32:i386", checks["wine32"]["fix"]["commands"])
        self.assertEqual(checks["gpu"]["status"], "warning")
        self.assertEqual(checks["gpu"]["fix"]["commands"], ["sudo ubuntu-drivers install"])
        self.assertIn("sudo apt install libvulkan1 mesa-vulkan-drivers",
                      checks["vulkan"]["fix"]["commands"])

    def test_wow64_and_arch_packages(self):
        self.os_release("ID=arch\n")
        runner = self.wine(layout=("lib/wine/x86_64-unix", "lib/wine/i386-windows"))
        self.gpu("card0", "0x1002", "amdgpu")
        result, checks = self.run_checks(runner)
        self.assertEqual(checks["wine32"]["status"], "ok")
        self.assertIn("WoW64", checks["wine32"]["detail"])
        self.assertEqual(checks["vulkan32"]["fix"]["commands"],
                         ["sudo pacman -S lib32-vulkan-icd-loader lib32-vulkan-radeon"])

    def test_broken_wine_low_disk_and_optional_tools(self):
        self.os_release("ID=fedora\n")
        runner = self.wine()
        Path(runner).write_text("#!/bin/sh\nexit 3\n")
        usage = namedtuple("usage", "total used free")(100, 99, 1024 ** 3)
        with patch.object(system_check.shutil, "disk_usage", return_value=usage), \
                patch.object(system_check.shutil, "which", return_value=None), \
                patch.object(local_setup, "desktop_folder", return_value=None):
            result, checks = self.run_checks(runner)
        self.assertEqual(checks["wine"]["status"], "warning")
        self.assertEqual(checks["wine"]["fix"]["commands"], ["sudo dnf install wine"])
        self.assertEqual(checks["disk"]["status"], "warning")
        self.assertEqual(checks["winetricks"]["status"], "optional")
        self.assertEqual(checks["winetricks"]["fix"]["commands"], ["sudo dnf install winetricks"])
        self.assertIn("aren't available", checks["shortcuts"]["detail"])
        self.assertEqual(checks["gpu"]["status"], "warning")
        self.assertEqual(result["warnings"], 5)
        self.assertTrue(os.access(runner, os.X_OK))


if __name__ == "__main__":
    unittest.main()
