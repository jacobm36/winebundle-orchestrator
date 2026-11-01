import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import graphics  # noqa: E402

SWITCHEROO = {"type": "aa{sv}", "data": [
    {"Name": {"type": "s", "data": "Intel Corporation TigerLake-LP GT2 [Iris Xe Graphics]"},
     "Environment": {"type": "as", "data": ["DRI_PRIME", "pci-0000_00_02_0"]},
     "Default": {"type": "b", "data": True}},
    {"Name": {"type": "s", "data": "NVIDIA Corporation TU117GLM [Quadro T500 Mobile]"},
     "Environment": {"type": "as", "data": ["__GLX_VENDOR_LIBRARY_NAME", "nvidia",
                                            "__NV_PRIME_RENDER_OFFLOAD", "1"]},
     "Default": {"type": "b", "data": False}}]}


class GraphicsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def card(self, number, address, vendor, driver, boot=None):
        device = self.root / "sys/devices/pci0000:00" / address
        device.mkdir(parents=True)
        (device / "vendor").write_text(vendor + "\n")
        if boot is not None:
            (device / "boot_vga").write_text(("1" if boot else "0") + "\n")
        target = self.root / "sys/bus/pci/drivers" / driver
        target.mkdir(parents=True, exist_ok=True)
        (device / "driver").symlink_to(target)
        card = self.root / "sys/class/drm" / f"card{number}"
        card.mkdir(parents=True)
        (card / "device").symlink_to(device)

    def test_friendly_names(self):
        self.assertEqual(graphics.friendly_name("NVIDIA Corporation TU117GLM [Quadro T500 Mobile]"),
                         "NVIDIA Quadro T500 Mobile")
        self.assertEqual(graphics.friendly_name(
            "Advanced Micro Devices, Inc. [AMD/ATI] Navi 23 [Radeon RX 6600M]"), "AMD Radeon RX 6600M")
        self.assertEqual(graphics.friendly_name("Some GPU"), "Some GPU")

    def test_nvidia_offload_from_sysfs(self):
        self.card(0, "0000:00:02.0", "0x8086", "i915", boot=True)
        self.card(1, "0000:01:00.0", "0x10de", "nvidia")
        found = graphics.discrete_gpu(self.root)
        self.assertEqual(found["name"], "NVIDIA graphics card")
        self.assertEqual(found["env"], graphics.NVIDIA_OFFLOAD)

    def test_mesa_card_uses_dri_prime_with_its_address(self):
        self.card(0, "0000:00:02.0", "0x8086", "i915", boot=True)
        self.card(1, "0000:03:00.0", "0x1002", "amdgpu", boot=False)
        self.assertEqual(graphics.discrete_gpu(self.root),
                         {"name": "AMD graphics card", "env": {"DRI_PRIME": "pci-0000_03_00_0"}})

    def test_single_or_unknown_cards_have_no_discrete_choice(self):
        self.assertIsNone(graphics.discrete_gpu(self.root))
        self.card(0, "0000:00:02.0", "0x8086", "i915", boot=True)
        self.assertIsNone(graphics.discrete_gpu(self.root))
        self.card(1, "0000:01:00.0", "0x10de", "vfio-pci")
        self.assertIsNone(graphics.discrete_gpu(self.root))

    def test_switcheroo_is_preferred_and_falls_back_to_sysfs(self):
        def reply(data, code=0):
            return subprocess.CompletedProcess([], code, stdout=json.dumps(data), stderr="")

        with patch.object(graphics.shutil, "which", return_value="/usr/bin/busctl"), \
                patch.object(graphics.subprocess, "run", return_value=reply(SWITCHEROO)):
            self.assertEqual(graphics.discrete_gpu(), {
                "name": "NVIDIA Quadro T500 Mobile",
                "env": {"__GLX_VENDOR_LIBRARY_NAME": "nvidia", "__NV_PRIME_RENDER_OFFLOAD": "1"}})
        single = {"data": SWITCHEROO["data"][:1]}
        with patch.object(graphics.shutil, "which", return_value="/usr/bin/busctl"), \
                patch.object(graphics.subprocess, "run", return_value=reply(single)), \
                patch.object(graphics, "_sysfs") as sysfs:
            self.assertIsNone(graphics.discrete_gpu())
            sysfs.assert_not_called()
        for result in (reply({}, 1), subprocess.CompletedProcess([], 0, stdout="not json")):
            with patch.object(graphics.shutil, "which", return_value="/usr/bin/busctl"), \
                    patch.object(graphics.subprocess, "run", return_value=result), \
                    patch.object(graphics, "_sysfs", return_value={"name": "x", "env": {}}) as sysfs:
                self.assertEqual(graphics.discrete_gpu(), {"name": "x", "env": {}})
                sysfs.assert_called_once()


if __name__ == "__main__":
    unittest.main()
