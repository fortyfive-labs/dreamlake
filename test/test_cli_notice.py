"""Tests for the ``dreamlake`` console-script deprecation pointer.

The CLI moved to TypeScript (npm ``@dreamlake/cli``, same ``dreamlake``
bin); every subcommand is ported, so every console-script invocation gets
the pointer. The two exemptions: programmatic ``python -m dreamlake.cli``
(argv[0] gating) and the internal ``append-local`` canonical-writer calls
spawned by dreamlake-server.
"""

from dreamlake.cli._notice import migration_notice


def test_console_script_gets_the_pointer():
    notice = migration_notice(
        "/usr/local/bin/dreamlake",
        ["list", "--episode", "robotics"],
    )
    assert notice is not None
    assert "DEPRECATED" in notice
    assert "dreamlake list" in notice
    assert "@dreamlake/cli" in notice


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


def test_artifact_push_is_no_longer_exempt():
    notice = migration_notice(
        "/usr/local/bin/dreamlake",
        ["artifact", "push", "./dashboard.html"],
    )
    assert notice is not None
    assert "dreamlake artifact" in notice


def test_append_local_writers_are_exempt():
    for group in ["artifact", "workflow"]:
        notice = migration_notice(
            "/usr/local/bin/dreamlake",
            [group, "append-local", "--backend", "http://s3/bucket/x", "--id", "y"],
        )
        assert notice is None


def test_bare_invocation_points_at_the_cli():
    notice = migration_notice(
        "/usr/local/bin/dreamlake",
        [],
    )
    assert notice is not None
    assert "dreamlake ..." in notice


def test_every_command_is_named_in_the_pointer():
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
        "artifact",
        "workflow",
    ]:
        notice = migration_notice(
            "dreamlake",
            [command],
        )
        assert notice is not None
        assert f"dreamlake {command}" in notice


def test_unknown_command_falls_back_to_the_bare_pointer():
    notice = migration_notice(
        "dreamlake",
        ["frobnicate"],
    )
    assert notice is not None
    assert "dreamlake ..." in notice
