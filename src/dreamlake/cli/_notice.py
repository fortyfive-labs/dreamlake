"""Deprecation pointer for the ``dreamlake`` console script.

The CLI has been replaced by the standalone DreamLake CLI
(https://github.com/dreamlake-ai/dreamlake-cli), installed with
``curl -fsSL https://dl.dreamlake.ai/install.sh | bash`` — every
subcommand, including ``artifact push`` and the ``workflow`` group, lives
there now, plus environment switching (``dreamlake env use``). This
package no longer registers a console script; this pointer only fires for
stale ``dreamlake`` bins left behind by older installs.

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
    "source",
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
        "dreamlake: DEPRECATED — this Python CLI has been replaced by the\n"
        "standalone DreamLake CLI. Install the latest version with:\n"
        "    curl -fsSL https://dl.dreamlake.ai/install.sh | bash\n"
        f"then run:  {equivalent} ...\n"
        "Same commands, flags, and env vars (DREAMLAKE_REMOTE / DREAMLAKE_API_KEY /\n"
        "DREAMLAKE_BSS_URL), plus environment switching (`dreamlake env use`).\n"
        "New releases of the `dreamlake` Python package no longer install this\n"
        "console script."
    )
