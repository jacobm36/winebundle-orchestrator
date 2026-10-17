#!/usr/bin/env python3

import argparse
import json
import os
import sys
from pathlib import Path


VERSION = 1
PATH_FIELDS = {"installer", "output", "smokeScript", "preInstallScript"}
ARRAY_FIELDS = {"installArgs", "smokeArgs", "winetricks"}
BOOLEAN_FIELDS = {"keepHostDrive", "skipProvision", "keepVm"}
STRING_FIELDS = {
    "installer",
    "entrypoint",
    "output",
    "smokeScript",
    "smokeWindow",
    "preInstallScript",
    "arch",
    "image",
    "memory",
    "disk",
}
INTEGER_FIELDS = {"smokeExitCode", "timeout", "cpus"}
KNOWN_FIELDS = (
    {"version"} | PATH_FIELDS | ARRAY_FIELDS | BOOLEAN_FIELDS | STRING_FIELDS | INTEGER_FIELDS
)

ARGUMENTS = {
    "installer": "--installer",
    "entrypoint": "--entrypoint",
    "output": "--output",
    "smokeScript": "--smoke-script",
    "smokeWindow": "--smoke-window",
    "smokeExitCode": "--smoke-exit-code",
    "preInstallScript": "--pre-install-script",
    "arch": "--arch",
    "timeout": "--timeout",
    "image": "--image",
    "cpus": "--cpus",
    "memory": "--memory",
    "disk": "--disk",
}
ARRAY_ARGUMENTS = {
    "installArgs": "--install-arg",
    "smokeArgs": "--smoke-arg",
    "winetricks": "--winetricks",
}
BOOLEAN_ARGUMENTS = {
    "keepHostDrive": "--keep-host-drive",
    "skipProvision": "--skip-provision",
    "keepVm": "--keep-vm",
}


class ConfigError(ValueError):
    pass


def _error(message):
    raise ConfigError(message)


def validate_config(config):
    if not isinstance(config, dict):
        _error("configuration must be a JSON object")

    unknown = sorted(set(config) - KNOWN_FIELDS)
    if unknown:
        _error(f"unknown configuration field: {unknown[0]}")
    if config.get("version") != VERSION:
        _error(f"version must be {VERSION}")

    for field in STRING_FIELDS:
        if field in config and not isinstance(config[field], str):
            _error(f"{field} must be a string")
    for field in ARRAY_FIELDS:
        if field in config:
            value = config[field]
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                _error(f"{field} must be an array of strings")
    for field in BOOLEAN_FIELDS:
        if field in config and not isinstance(config[field], bool):
            _error(f"{field} must be a boolean")
    for field in INTEGER_FIELDS:
        if field in config and (
            not isinstance(config[field], int) or isinstance(config[field], bool)
        ):
            _error(f"{field} must be an integer")

    for field in ("installer", "entrypoint", "output"):
        if not config.get(field):
            _error(f"{field} is required")
    if config.get("arch", "win64") not in {"win32", "win64"}:
        _error("arch must be win32 or win64")
    if config.get("timeout", 180) < 1:
        _error("timeout must be positive")
    if config.get("cpus", 2) < 1:
        _error("cpus must be positive")
    if config.get("smokeExitCode", 0) < 0:
        _error("smokeExitCode must be non-negative")

    if config.get("smokeScript") and config.get("smokeArgs"):
        _error("smokeScript cannot be combined with smokeArgs")
    if config.get("smokeScript") and config.get("smokeWindow"):
        _error("smokeScript cannot be combined with smokeWindow")
    if config.get("smokeWindow") and "smokeExitCode" in config:
        _error("smokeExitCode does not apply to smokeWindow")

    return config


def load_config(path):
    try:
        with open(path, "r", encoding="utf-8") as config_file:
            config = json.load(config_file)
    except OSError as exc:
        _error(f"cannot read configuration: {exc}")
    except json.JSONDecodeError as exc:
        _error(f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}")
    return validate_config(config)


def normalized_config(config, base_dir):
    normalized = dict(config)
    for field in PATH_FIELDS:
        value = normalized.get(field)
        if value and not os.path.isabs(value):
            normalized[field] = os.path.abspath(os.path.join(base_dir, value))
    return normalized


def config_arguments(config, base_dir):
    config = normalized_config(validate_config(config), base_dir)
    arguments = []
    for field, option in ARGUMENTS.items():
        if field in config and config[field] != "":
            arguments.extend((option, str(config[field])))
    for field, option in ARRAY_ARGUMENTS.items():
        for value in config.get(field, []):
            arguments.extend((option, value))
    for field, option in BOOLEAN_ARGUMENTS.items():
        if config.get(field, False):
            arguments.append(option)
    return arguments


def write_config(path, config):
    validate_config(config)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as config_file:
            json.dump(config, config_file, indent=2)
            config_file.write("\n")
        os.replace(temporary, destination)
    except OSError as exc:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        _error(f"cannot write configuration: {exc}")


def create_parser():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--installer", required=True)
    parser.add_argument("--entrypoint", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--install-arg", action="append", default=[])
    parser.add_argument("--smoke-arg", action="append", default=[])
    parser.add_argument("--smoke-script")
    parser.add_argument("--smoke-window")
    parser.add_argument("--smoke-exit-code", type=int)
    parser.add_argument("--winetricks", action="append", default=[])
    parser.add_argument("--pre-install-script")
    parser.add_argument("--keep-host-drive", action="store_true")
    parser.add_argument("--arch", choices=("win32", "win64"), default="win64")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--image", default="24.04")
    parser.add_argument("--cpus", type=int, default=2)
    parser.add_argument("--memory", default="4G")
    parser.add_argument("--disk", default="20G")
    parser.add_argument("--skip-provision", action="store_true")
    parser.add_argument("--keep-vm", action="store_true")
    return parser


def config_from_namespace(namespace):
    config = {
        "version": VERSION,
        "installer": namespace.installer,
        "entrypoint": namespace.entrypoint,
        "output": namespace.output,
        "installArgs": namespace.install_arg,
        "smokeArgs": namespace.smoke_arg,
        "winetricks": namespace.winetricks,
        "arch": namespace.arch,
        "timeout": namespace.timeout,
        "image": namespace.image,
        "cpus": namespace.cpus,
        "memory": namespace.memory,
        "disk": namespace.disk,
        "keepHostDrive": namespace.keep_host_drive,
        "skipProvision": namespace.skip_provision,
        "keepVm": namespace.keep_vm,
    }
    optional = {
        "smokeScript": namespace.smoke_script,
        "smokeWindow": namespace.smoke_window,
        "smokeExitCode": namespace.smoke_exit_code,
        "preInstallScript": namespace.pre_install_script,
    }
    config.update({key: value for key, value in optional.items() if value is not None})
    return validate_config(config)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Manage winebundle build configurations")
    subparsers = parser.add_subparsers(dest="command", required=True)

    arguments_parser = subparsers.add_parser("arguments")
    arguments_parser.add_argument("config")

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("config")

    create_command = subparsers.add_parser("create", parents=[create_parser()])
    create_command.add_argument("config")

    args = parser.parse_args(argv)
    if args.command == "arguments":
        path = os.path.abspath(args.config)
        arguments = config_arguments(load_config(path), os.path.dirname(path))
        sys.stdout.buffer.write(b"\0".join(value.encode("utf-8") for value in arguments))
        if arguments:
            sys.stdout.buffer.write(b"\0")
    elif args.command == "validate":
        load_config(os.path.abspath(args.config))
        print(f"Configuration is valid: {args.config}")
    elif args.command == "create":
        write_config(args.config, config_from_namespace(args))
        print(f"Configuration saved to {args.config}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ConfigError as exc:
        print(f"winebundle config: {exc}", file=sys.stderr)
        raise SystemExit(2)
