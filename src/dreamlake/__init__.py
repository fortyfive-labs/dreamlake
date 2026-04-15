"""
Dreamlake Python SDK

A simple and flexible SDK for ML experiment tracking and data storage.

Usage:

    # Remote mode (API server)
    from dreamlake import Episode

    with Episode(
        name="my-experiment",
        workspace="my-workspace",
        remote="http://localhost:3000",
        api_key="your-jwt-token"
    ) as episode:
        episode.log("Training started")
        episode.track("loss", {"step": 0, "value": 0.5})

    # Local mode (filesystem)
    with Episode(
        name="my-experiment",
        workspace="my-workspace",
        local_path=".dreamlake"
    ) as episode:
        episode.log("Training started")

    # Decorator style
    from dreamlake import dreamlake_episode

    @dreamlake_episode(
        name="my-experiment",
        workspace="my-workspace",
        remote="http://localhost:3000",
        api_key="your-jwt-token"
    )
    def train_model(episode):
        episode.log("Training started")
"""

from .episode import Episode, dreamlake_episode, OperationMode
from .client import RemoteClient
from .storage import LocalStorage
from .log import LogLevel, LogBuilder
from .params import ParametersBuilder

__version__ = "0.4.2"

__all__ = [
    "Episode",
    "dreamlake_episode",
    "OperationMode",
    "RemoteClient",
    "LocalStorage",
    "LogLevel",
    "LogBuilder",
    "ParametersBuilder",
]
