"""Deprecation pointer for the ``dreamlake`` console script.

The CLI has been fully reimplemented in TypeScript as ``@dreamlake/cli``
(npm), which installs the SAME ``dreamlake`` bin — every subcommand,
including ``artifact push`` and the ``workflow`` group, lives there now.
This Python console script is DEPRECATED: it keeps working, but new
features land in the TS CLI only.

Gating (same pattern as the earlier tranche notices):
  * argv[0]-gated — programmatic ``python -m dreamlake.cli`` invocations
    stay silent;
  * ``append-local`` subcommands are exempt — they are the canonical
    DreamDB writers that dreamlake-server itself spawns (the ONE part of
    the Python CLI that is not deprecated), and their stdout/stderr is a
    machine contract.
"""

from pathlib import PurePath

_KNOWN_COMMANDS = {
    "login",
    "logout",
    "profile",
    "upload",
    "download",
    "list",
    "create",
    "delete",
    "update",
    "vectorize",
    "video",
    "artifact",
    "workflow",
}


def migration_notice(
    argv0: str,
    args: list,
) -> str | None:
    """The stderr pointer for one invocation, or ``None`` to stay silent.

    Silent when:
      * not invoked as the ``dreamlake`` console script (``python -m
        dreamlake.cli`` sees a module path in ``argv[0]``), or
      * the invocation is an internal ``append-local`` writer call
        (``dreamlake artifact|workflow append-local ...``).
    """
    if PurePath(argv0).name != "dreamlake":
        return None

    command = args[0] if args else None
    if command not in _KNOWN_COMMANDS:
        # Bare help / unknown commands: still point at the TS CLI.
        command = None

    if command in ("artifact", "workflow") and len(args) >= 2 and args[1] == "append-local":
        # Canonical-writer subprocess calls (spawned by dreamlake-server) —
        # never add noise to a machine contract.
        return None

    if command:
        equivalent = f"dreamlake {command}"
    else:
        equivalent = "dreamlake"

    return (
        "dreamlake: DEPRECATED — this Python CLI has moved to TypeScript:\n"
        "    npm i -g @dreamlake/cli   (installs the same `dreamlake` bin)\n"
        f"then run:  {equivalent} ...\n"
        "Same commands, flags, and env vars (DREAMLAKE_REMOTE / DREAMLAKE_API_KEY /\n"
        "DREAMLAKE_BSS_URL), including `artifact push` and `workflow push`. This\n"
        "console script keeps working for now, but new features land there."
    )
