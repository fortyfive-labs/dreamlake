"""
Dreamlake Python SDK

A simple and flexible SDK for ML experiment tracking and data storage.

Usage:

    # Remote mode (API server)
    from dreamlake import Session

    with Session(
        name="my-experiment",
        workspace="my-workspace",
        remote="http://localhost:3000",
        api_key="your-jwt-token"
    ) as session:
        session.log("Training started")
        session.track("loss", {"step": 0, "value": 0.5})

    # Local mode (filesystem)
    with Session(
        name="my-experiment",
        workspace="my-workspace",
        local_path=".dreamlake"
    ) as session:
        session.log("Training started")

    # Decorator style
    from dreamlake import dreamlake_session

    @dreamlake_session(
        name="my-experiment",
        workspace="my-workspace",
        remote="http://localhost:3000",
        api_key="your-jwt-token"
    )
    def train_model(session):
        session.log("Training started")
"""

from .session import Session, dreamlake_session, OperationMode
from .client import RemoteClient
from .storage import LocalStorage
from .log import LogLevel, LogBuilder
from .params import ParametersBuilder

__version__ = "0.1.0"

__all__ = [
    "Session",
    "dreamlake_session",
    "OperationMode",
    "RemoteClient",
    "LocalStorage",
    "LogLevel",
    "LogBuilder",
    "ParametersBuilder",
]
