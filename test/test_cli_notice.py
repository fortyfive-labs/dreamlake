"""Tests for the tranche-2 TS-CLI migration pointer (pure helper).

Mirrors the tranche-1 ``lakeshore-daemon`` notice tests: the pointer is
argv[0]-gated so only the ``dreamlake`` console script prints it, and
``artifact push`` (the one subcommand that stays Python) is exempt.
"""

from dreamlake.cli._notice import migration_notice


def test_console_script_gets_the_pointer():
    notice = migration_notice(
        "/usr/local/bin/dreamlake",
        ["list", "--episode", "robotics"],
    )
    assert notice is not None
    assert "lakeshore dreamlake list" in notice
    assert "@dreamlake/lakeshore" in notice


def test_module_invocation_stays_silent():
    notice = migration_notice(
        "/repo/src/dreamlake/cli/__init__.py",
        ["list"],
    )
    assert notice is None


def test_python_m_stays_silent():
    notice = migration_notice(
        "/usr/bin/python3",
        ["list"],
    )
    assert notice is None


def test_artifact_push_is_exempt():
    notice = migration_notice(
        "/usr/local/bin/dreamlake",
        ["artifact", "push", "./dashboard.html"],
    )
    assert notice is None


def test_other_artifact_subcommands_get_the_pointer():
    notice = migration_notice(
        "/usr/local/bin/dreamlake",
        ["artifact", "list"],
    )
    assert notice is not None
    assert "lakeshore dreamlake artifact" in notice


def test_bare_invocation_points_at_the_group():
    notice = migration_notice(
        "/usr/local/bin/dreamlake",
        [],
    )
    assert notice is not None
    assert "lakeshore dreamlake ..." in notice


def test_every_ported_command_is_named_in_the_pointer():
    for command in [
        "login",
        "logout",
        "profile",
        "upload",
        "download",
        "create",
        "delete",
        "update",
        "vectorize",
        "video",
    ]:
        notice = migration_notice(
            "dreamlake",
            [command],
        )
        assert notice is not None
        assert f"lakeshore dreamlake {command}" in notice


def test_unknown_command_falls_back_to_the_group_pointer():
    notice = migration_notice(
        "dreamlake",
        ["frobnicate"],
    )
    assert notice is not None
    assert "lakeshore dreamlake ..." in notice
