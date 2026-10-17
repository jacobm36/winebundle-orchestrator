# winebundle-orchestrator

`winebundle-orchestrator` turns a trusted Windows EXE or MSI installer into a
portable, validated Wine prefix. Each build runs in a disposable Ubuntu
Multipass VM, so the host does not need Wine or the application's dependencies
installed.

The orchestrator:

1. creates a fresh VM and copies the installer into it;
2. provisions Wine, Xvfb, winetricks, and supporting tools;
3. runs the installer in a new Wine prefix;
4. removes host-specific links from the prefix;
5. imports the packaged prefix into a separate validation prefix;
6. runs a smoke test; and
7. returns checksummed artifacts and deletes the VM.

This workflow is installer-agnostic. To use it with another Windows
application, provide that installer's unattended arguments, the Windows path
to its installed executable, and an appropriate smoke test.

## Quickstart

### 1. Install the prerequisites

Use a Linux host with:

- [Multipass](https://multipass.run/) installed and able to launch an Ubuntu
  VM;
- Bash 4.4+;
- Python 3.9+;
- GNU `tar`; and
- hardware virtualization enabled.

The default VM needs 2 CPUs, 4 GB of memory, and 20 GB of disk space. Verify
Multipass before starting:

```bash
multipass version
multipass launch 24.04 --name winebundle-check
multipass delete --purge winebundle-check
```

### 2. Build a bundle

Make the orchestrator executable, then replace the sample values below with
values for your installer:

```bash
chmod +x bin/winebundle-vm

bin/winebundle-vm run \
  --installer ./path/to/setup.exe \
  --install-arg /S \
  --entrypoint 'C:\Program Files\Vendor\Application\Application.exe' \
  --smoke-arg --version \
  --output ./artifacts
```

Important:

- `--install-arg` is installer-specific. Common values include `/S`,
  `/VERYSILENT`, or `/quiet`, but you must consult the application's
  documentation. Repeat the option for multiple arguments.
- `--entrypoint` must be the absolute `C:` path at which the installer creates
  the application executable. Use single quotes in Bash so backslashes and
  spaces are preserved.
- The default smoke test runs the entrypoint with any `--smoke-arg` values and
  expects exit code `0`.
- Each install or validation process has a 180-second timeout by default. Use
  `--timeout SECONDS` for a slow installer or application.

For an MSI, pass the MSI directly. The worker invokes it through
`wine msiexec /i`:

```bash
bin/winebundle-vm run \
  --installer ./path/to/application.msi \
  --install-arg /qn \
  --entrypoint 'C:\Program Files\Vendor\Application\Application.exe' \
  --smoke-arg --version \
  --output ./artifacts
```

## Reusable build configurations

Build settings can be stored in a versioned JSON file. Create one from the
same options accepted by `run`:

```bash
bin/winebundle-vm config create ./7zip.json \
  --installer ./7z2604-x64.exe \
  --install-arg /S \
  --entrypoint 'C:\Program Files\7-Zip\7z.exe' \
  --smoke-script ./worker/smoke-tests/7zip-roundtrip.sh \
  --output ./artifacts
```

Validate and run the saved configuration:

```bash
bin/winebundle-vm config validate ./7zip.json
bin/winebundle-vm run --config ./7zip.json
```

Paths in a configuration are resolved relative to the configuration file.
Explicit command-line options override scalar configuration values and append
repeatable values such as `--install-arg`, `--smoke-arg`, and `--winetricks`.
This makes it possible to adjust a known-good configuration without modifying
it:

```bash
bin/winebundle-vm run \
  --config ./7zip.json \
  --timeout 300 \
  --install-arg /NORESTART
```

The version 1 format uses the following fields:

```json
{
  "version": 1,
  "installer": "./setup.exe",
  "entrypoint": "C:\\Program Files\\Vendor\\Application.exe",
  "output": "./artifacts",
  "installArgs": ["/S"],
  "smokeArgs": ["--version"],
  "smokeExitCode": 0,
  "winetricks": ["corefonts"],
  "arch": "win64",
  "timeout": 180,
  "image": "24.04",
  "cpus": 2,
  "memory": "4G",
  "disk": "20G",
  "keepHostDrive": false,
  "skipProvision": false,
  "keepVm": false
}
```

Optional path fields are `smokeScript` and `preInstallScript`; window
validation uses `smokeWindow`. A smoke script cannot be combined with smoke
arguments or window validation.

## Importing a working Batocera Wine game

`bin/winebundle-batocera-import` drafts a configuration from a game that already
runs in Batocera. Copy the game folder from `/userdata/roms/windows/` (and, for
`.pc` games, its prefix from `/userdata/system/wine-bottles/windows/`), or reach
it through the `\\BATOCERA\share` network share, then run:

```bash
bin/winebundle-batocera-import \
  /path/to/userdata/roms/windows/Game.wine ./game.json \
  --installer /absolute/path/to/setup.exe \
  --install-arg /S \
  --smoke-window 'Game Title'
```

It reads `autorun.cmd` (`DIR=` and `CMD=`) for the entrypoint, the prefix's
`system.reg` for the architecture, and `winetricks.log` for winetricks verbs.
Batocera does not keep the installer, so `--installer` and any `--install-arg`
values must be supplied. When the game runs from outside `drive_c` (typical for
`.pc` folders), pass `--entrypoint` with the installed `C:` path. Extract
`.wsquashfs` games with `unsquashfs` first.

Settings the version 1 format cannot represent are printed as notes instead of
being dropped: launch arguments, other `autorun.cmd` entries, `windows.*`
settings from `batocera.conf` (runner, DXVK/VKD3D, sync options), and DLL
overrides from `user.reg`. Load the resulting file with the GUI's import button
or `bin/winebundle-vm run --config ./game.json`.

## Local web GUI

Start the dependency-free local GUI from the repository root:

```bash
python3 gui/winebundle_gui.py
```

The browser interface exposes installer, entrypoint, dependency, validation,
and VM fields. It can:

- validate and launch a build;
- show build output and completion status;
- export the form as a versioned JSON configuration; and
- import a known-good configuration to reproduce or adjust it.

Use absolute paths in the GUI. Browsers do not expose a selected file's host
path, so installer and script inputs are server-side path fields; configuration
import itself uses a normal browser file picker and is processed locally.

The server listens only on `127.0.0.1` by default, accepts API requests only
from loopback clients, and requires a per-process session token for
state-changing requests. It launches the orchestrator with an argument array
rather than a shell command. Stop it with `Ctrl+C`. To start without opening a
browser, or to select another local port:

```bash
python3 gui/winebundle_gui.py --no-browser --port 9000
```

## Find and run the result

The command prints the exact result directory. Its layout is:

```text
artifacts/
  <orchestrator-job-id>/
    artifacts/
      <bundle-job-id>/
        prefix.tar.gz
        run.sh
        manifest.json
        SHA256SUMS
        status.json
        build.log
        validation.log
    work/
```

On a Linux machine with Wine and `sha256sum` installed:

```bash
cd artifacts/<orchestrator-job-id>/artifacts/<bundle-job-id>
./run.sh
```

Pass application arguments after `run.sh`. The launcher verifies checksums,
extracts the prefix on first use, and starts the configured entrypoint:

```bash
./run.sh --help
```

By default the prefix is extracted to
`~/.local/share/winebundle/<bundle-job-id>`. Set `WINEBUNDLE_PREFIX_DIR` to
change that location or `WINE` to select a different Wine executable. The
launcher warns when the installed Wine version differs from the version used
to build the bundle. Wine itself is not included.

## Adapting the command to an installer

Before running a new installer, determine:

| Input | What to find |
| --- | --- |
| Installer | A trusted `.exe` or `.msi` file |
| Unattended arguments | Flags that install without user interaction |
| Entrypoint | The final absolute Windows path to the installed executable |
| Architecture | `win64` (default) or `win32` |
| Dependencies | Any required winetricks verbs or pre-install setup |
| Validation | A command, expected exit code, window title, or smoke script |

Start with the smallest working command. Add dependencies and custom validation
only when the application needs them:

```bash
bin/winebundle-vm run \
  --installer ./setup.exe \
  --install-arg /silent \
  --entrypoint 'C:\Program Files\Example\Example.exe' \
  --output ./artifacts
```

With no explicit validation option, the entrypoint is launched with no
arguments and must exit successfully. This is suitable only for applications
that terminate on their own. Use one of the validation strategies below for
other applications.

## Validation strategies

Every build validates the packaged prefix after re-extracting it into a
separate prefix. Choose one strategy.

### Command and exit code

Use repeatable `--smoke-arg` options for a CLI application:

```bash
--smoke-arg --version
```

Exit code `0` is expected by default. For an application whose successful test
uses another code:

```bash
--smoke-arg --self-test \
--smoke-exit-code 7
```

### GUI window

For an application that stays open, wait for a window title instead of waiting
for the process to exit:

```bash
--smoke-window 'Application Name'
```

The test passes when a visible window title contains the supplied text. It
fails if the process exits first or the timeout expires. Do not combine
`--smoke-window` with `--smoke-script` or `--smoke-exit-code`.

### Custom smoke script

Use a trusted Bash script for multi-step or application-specific checks:

```bash
--smoke-script ./smoke-tests/application-smoke.sh
```

The script runs with:

| Variable | Value |
| --- | --- |
| `WINEPREFIX` | Path to the validation prefix |
| `WINE_COMMAND` | Wine executable |
| `ENTRYPOINT` | Configured Windows entrypoint |
| `SMOKE_WORK_DIR` | Writable work directory inside `C:` |

The script must exit nonzero when validation fails. It cannot be combined with
`--smoke-arg` or `--smoke-window`. The 7-Zip round-trip script in
`worker/smoke-tests/7zip-roundtrip.sh` is a complete example.

## Complete 7-Zip example

This example downloads a known installer, installs it silently, creates and
extracts an archive during validation, and publishes the resulting bundle:

```bash
curl -fL \
  https://github.com/ip7z/7zip/releases/download/26.04/7z2604-x64.exe \
  -o 7z2604-x64.exe

chmod +x bin/winebundle-vm
bin/winebundle-vm run \
  --installer ./7z2604-x64.exe \
  --install-arg /S \
  --entrypoint 'C:\Program Files\7-Zip\7z.exe' \
  --smoke-script ./worker/smoke-tests/7zip-roundtrip.sh \
  --output ./artifacts
```

## Installer customization

### winetricks dependencies

Install one or more components before running the installer:

```bash
--winetricks corefonts \
--winetricks vcrun2019
```

Each verb is applied to the build prefix and recorded in `manifest.json`.

### Pre-install hook

Use a trusted Bash script when setup requires more than winetricks:

```bash
--pre-install-script ./scripts/prepare-prefix.sh
```

The hook runs before the Windows installer with these variables set:

- `WINEPREFIX`
- `WINEARCH`
- `WINE_COMMAND`
- `WINEBOOT_COMMAND`
- `WINESERVER_COMMAND`
- `INSTALLER`

The hook and its checksum are included in the published bundle.

### VM resources and prepared images

Override VM sizing when an installer needs more resources:

```bash
--cpus 4 --memory 8G --disk 40G
```

By default the guest installs Wine, Xvfb, `x11-utils`, winetricks, and
`cabextract` on every run. For faster builds, maintain an immutable Ubuntu
image containing those dependencies and use:

```bash
--image <prepared-image> --skip-provision
```

Create a fresh VM from the image for every installer.

## Command reference

Run `bin/winebundle-vm --help` for the authoritative option list.

### Required

| Option | Description |
| --- | --- |
| `--installer PATH` | Trusted EXE or MSI installer |
| `--entrypoint WINDOWS_PATH` | Installed executable at an absolute `C:` path |
| `--output DIR` | Host directory for bundles and diagnostics |

### Installer and validation

| Option | Description |
| --- | --- |
| `--install-arg VALUE` | Installer argument; repeat as needed |
| `--winetricks VERB` | winetricks dependency; repeat as needed |
| `--pre-install-script PATH` | Trusted Bash hook run before installation |
| `--arch win32\|win64` | Prefix architecture; default `win64` |
| `--timeout SECONDS` | Per-process timeout; default `180` |
| `--smoke-arg VALUE` | Entrypoint test argument; repeat as needed |
| `--smoke-exit-code CODE` | Expected test exit code; default `0` |
| `--smoke-window TITLE` | Wait for a window title containing this text |
| `--smoke-script PATH` | Trusted Bash script for custom validation |
| `--keep-host-drive` | Retain Wine's `z:` mapping and host device links |

### VM

| Option | Description |
| --- | --- |
| `--image IMAGE` | Multipass image; default `24.04` |
| `--cpus COUNT` | Virtual CPUs; default `2` |
| `--memory SIZE` | Memory; default `4G` |
| `--disk SIZE` | Disk; default `20G` |
| `--skip-provision` | Assume the image already has all guest dependencies |
| `--keep-vm` | Retain the VM after success or failure for debugging |

Set `MULTIPASS_COMMAND` to use a non-default Multipass executable.
Set `PYTHON_COMMAND` to use a Python 3 executable with a different name or
path.

## Bundle contents

| File | Purpose |
| --- | --- |
| `prefix.tar.gz` | Sanitized installed Wine prefix |
| `run.sh` | Checksum-verifying launcher |
| `manifest.json` | Installer hash, arguments, Wine version, hooks, and test configuration |
| `SHA256SUMS` | Checksums for the published files |
| `status.json` | Final build status |
| `build.log` | Prefix setup and installer output |
| `validation.log` | Smoke-test output |
| `smoke-test.sh` | Included only when a custom smoke script is used |
| `pre-install.sh` | Included only when a pre-install hook is used |

Before packaging, the worker removes the `z:` mapping to the host filesystem
and absolute links in the Wine user profile. The build fails if the
re-extracted bundle still contains host-specific links. Use
`--keep-host-drive` only when the application explicitly requires those
mappings; doing so makes the bundle less portable.

## Failure handling and troubleshooting

The VM is deleted after success, failure, or interruption. On failure, the
orchestrator attempts to retrieve logs and retained Wine working data beneath
the result directory.

- Inspect `build.log` when installation or prefix setup fails.
- Inspect `validation.log` when the entrypoint or smoke test fails.
- Increase `--timeout` if a legitimate process exceeds 180 seconds.
- Confirm the unattended flags if the installer waits for a dialog.
- Confirm `--entrypoint` exactly matches the installed location and
  architecture.
- Add required runtimes with `--winetricks`.
- Use `--keep-vm` only for interactive debugging. The command prints the VM
  name; delete it manually with `multipass delete --purge <name>` afterward.

A timed-out Wine step also runs `wineserver -k` to avoid leaving Wine processes
behind.

## Architecture

```text
bin/winebundle-vm
  -> lib/winebundle_config.py
  -> gui/winebundle_gui.py (optional local interface)
  -> lib/backends/multipass.sh
      -> disposable Ubuntu VM
          -> guest/run-job.sh
              -> Xvfb + worker/winebundle
```

The VM backend is isolated behind shell functions so another backend, such as
libvirt/KVM, can implement the same launch, execute, transfer, and delete
operations.

Do not mount the host workspace into the VM. Inputs and outputs use
`multipass transfer`. The returned archive is treated as untrusted and rejected
if it contains absolute paths, parent-relative paths, hard links, or files
written beneath a symlink created by the archive.

## Security model and limitations

This prototype accepts trusted operator inputs: the installer, pre-install
hook, and smoke script can execute code. Multipass provides a VM boundary, but
its default networking allows outbound access. Do not place host credentials
inside the VM. For untrusted installers, enforce egress filtering outside the
guest or use a backend with an isolated virtual network.

Wine is not a sandbox, and a successful smoke test does not prove that an
application is safe.

Current portability limitations:

- validation uses a second prefix on the same VM, not a different machine or
  user account;
- the prefix still records the build user's name; and
- applications may depend on a specific Wine version even when validation
  succeeds.

## Development

Run the shell test suites from the repository root:

```bash
make test
```

The test target also runs the Python configuration and GUI API tests.
