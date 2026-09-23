"""CLI and composition root (Architecture Freeze sections 11-12).

Owns only composition: argument parsing, state/host-config paths, Identity
and MemoryStore loading, explicit discovery, registry binding, Core
construction and stdin/stdout. No business logic lives here; every command
is handled by PhanesCore.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from phanes.capabilities import CapabilityRegistry, RegistryError
from phanes.contracts import InvocationResult
from phanes.core import PhanesCore
from phanes.discovery import DiscoveryError, discover_body, load_host_config
from phanes.identity import IdentityError, init_identity, load_identity
from phanes.memory import MemoryStore, MemoryStoreError

EXIT_COMMAND = "退出"


def _format_number(value: float | int) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _cmd_init(state_dir: Path) -> int:
    """Initialize Identity + Memory as one complete Self state.

    Built in a sibling temporary directory and committed by rename, so a
    failure never leaves a half-initialized state directory (Freeze §5:
    init only on an empty state directory).
    """
    if state_dir.exists():
        print(f"error: state path already exists: {state_dir}", file=sys.stderr)
        return 1
    state_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(dir=state_dir.parent, prefix=state_dir.name + ".tmp-"))
    try:
        identity = init_identity(tmp_dir)
        MemoryStore.create(tmp_dir, identity.agent_id)
        load_identity(tmp_dir)
        MemoryStore.load(tmp_dir, identity.agent_id)
        tmp_dir.rename(state_dir)
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print(f"error: init failed: {exc}", file=sys.stderr)
        return 1
    print(f"initialized state: {state_dir}")
    print(f"agent_id: {identity.agent_id}")
    return 0


def _compose_core(state_dir: Path, host_config_path: str | None) -> PhanesCore:
    """Load Self, optionally discover and bind the explicitly configured
    body, and construct the Core. Raises on any startup error."""
    identity = load_identity(state_dir)
    memory = MemoryStore.load(state_dir, identity.agent_id)
    registry = CapabilityRegistry()
    if host_config_path is not None:
        config = load_host_config(host_config_path)
        adapter = discover_body(config)
        if adapter is not None:
            registry.bind(adapter, config.get("allowed_capabilities", []))
    return PhanesCore(identity, memory, registry)


def _print_startup(core: PhanesCore) -> None:
    print(f"agent_id: {core.identity.agent_id}")
    body = core.current_body
    if body is None:
        print("body: none")
        print("capabilities: (none)")
    else:
        print(f"body: {body.body_id} ({body.body_type})")
        names = ", ".join(spec.name for spec in core.capabilities)
        print(f"capabilities: {names or '(none)'}")


def _print_result(result: InvocationResult) -> None:
    if result.ok:
        data = result.data or {}
        action = data.get("action")
        if action == "remember":
            print(f"已保存 {data['place']} = ({_format_number(data['x'])}, {_format_number(data['y'])})")
        elif action == "query":
            print(f"{data['place']} = ({_format_number(data['x'])}, {_format_number(data['y'])})")
        else:
            print(f"已到达 ({_format_number(data['x'])}, {_format_number(data['y'])})")
    else:
        error = result.error
        print(f"{error.code}: {error.message}")


def _repl(core: PhanesCore) -> int:
    """Fixed-syntax interaction loop. 退出 or EOF exits normally."""
    for line in sys.stdin:
        text = line.strip()
        if not text:
            continue
        if text == EXIT_COMMAND:
            break
        try:
            result = core.handle_command(text)
        except (OSError, MemoryStoreError) as exc:
            # A failed write is never reported as success (Freeze AT16).
            print(f"error: command failed: {exc}")
            continue
        _print_result(result)
    return 0


def _cmd_run(state_dir: Path, host_config_path: str | None) -> int:
    try:
        core = _compose_core(state_dir, host_config_path)
    except (IdentityError, MemoryStoreError, DiscoveryError, RegistryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print_startup(core)
    return _repl(core)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="phanes", description="Phanes P0 runtime")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_init = subparsers.add_parser("init", help="initialize a new state directory")
    p_init.add_argument("--state", required=True, help="state directory (must not exist)")

    p_run = subparsers.add_parser("run", help="run the interactive loop")
    p_run.add_argument("--state", required=True, help="state directory")
    p_run.add_argument("--host-config", default=None, help="explicit local host config JSON")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init":
        return _cmd_init(Path(args.state))
    if args.command == "run":
        return _cmd_run(Path(args.state), args.host_config)
    return 2  # unreachable: subparsers are required


if __name__ == "__main__":
    sys.exit(main())
