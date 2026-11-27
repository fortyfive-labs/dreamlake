"""Umbrella imports preserve canonical recorder object/module identities."""
import importlib


def test_public_and_private_import_identity():
    import dreamlake
    import dreamlake_dash
    import dreamlake.experiments as existing
    for name in existing.__all__:
        assert getattr(existing, name) is getattr(dreamlake_dash, name)
    for name in ("Experiment", "RUN", "OperationMode", "LogLevel", "NativeConnection"):
        assert getattr(dreamlake, name) is getattr(dreamlake_dash, name)
    for name in ("experiment", "native", "native_job", "buffer", "storage", "files", "metric", "metric_rows", "track", "run", "exceptions", "auth.exceptions"):
        assert importlib.import_module("dreamlake.experiments._native." + name) is importlib.import_module("dreamlake_dash." + name)
    from dreamlake.api.experiments import ExperimentRequestError
    from dreamlake_dash.dash import DashWriterError
    assert issubclass(DashWriterError, ExperimentRequestError)
    assert importlib.import_module("dreamlake.api._experiment_upload") is importlib.import_module("dreamlake_dash._upload")

    assert importlib.import_module("dreamlake.auth.token_storage") is importlib.import_module("dreamlake_dash._secure_storage")
    assert importlib.import_module("dreamlake.auth.exceptions") is importlib.import_module("dreamlake_dash._login_errors")
