"""Migration pointer for the ``dreamlake`` console script.

Tranche 2 of the TS CLI migration ported this CLI's subcommands to the
TypeScript ``lakeshore`` CLI (npm ``@dreamlake/lakeshore``) as the
``lakeshore dreamlake`` group. The Python implementations keep working,
but the console script prints a one-line-per-run stderr pointer so
muscle memory migrates.

Same pattern as the ``lakeshore-daemon`` notice (tranche 1): gated on
``argv[0]`` so programmatic ``python -m dreamlake.cli`` invocations stay
silent, and skipped entirely for ``artifact push`` — the one subcommand
that stays Python (it writes through the ``dreamdb`` SigV4 writer,
which has no TypeScript port yet).
"""

from pathlib import PurePath

# Subcommand → the TS command that supersedes it. ``artifact`` is
# handled specially (push stays here; list/delete/restore moved).
_PORTED_COMMANDS = {
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
}


def migration_notice(
    argv0: str,
    args: list,
) -> str | None:
    """The stderr pointer for one invocation, or ``None`` to stay silent.

    Silent when:
      * not invoked as the ``dreamlake`` console script (``python -m
        dreamlake.cli`` sees a module path in ``argv[0]``), or
      * the invocation is ``dreamlake artifact push ...`` — the only
        subcommand that has not moved.
    """
    if PurePath(argv0).name != "dreamlake":
        return None

    command = args[0] if args else None
    if command not in _PORTED_COMMANDS:
        # Bare help / unknown commands: still point at the TS CLI.
        command = None

    if command == "artifact" and len(args) >= 2 and args[1] == "push":
        # `artifact push` stays Python (dreamdb SigV4 writer) — no noise.
        return None

    if command:
        equivalent = f"lakeshore dreamlake {command}"
    else:
        equivalent = "lakeshore dreamlake"

    return (
        "dreamlake: note — this CLI now lives in the TypeScript `lakeshore` CLI\n"
        "(npm i -g @dreamlake/lakeshore):\n"
        f"    {equivalent} ...\n"
        "Same flags and env vars (DREAMLAKE_REMOTE / DREAMLAKE_API_KEY /\n"
        "DREAMLAKE_BSS_URL). Only `dreamlake artifact push` stays Python for\n"
        "now. This console script keeps working, but new features land there."
    )
