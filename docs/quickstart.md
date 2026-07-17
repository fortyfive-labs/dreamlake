# Quickstart

## Installation

```bash
pip install dreamlake=={VERSION}
```

## Local Mode

```{code-block} python
:linenos:

from dreamlake import Episode

with Episode(prefix="tutorial/my-first-experiment",
        local_path=".dreamlake") as episode:
    episode.log("Training started")
    episode.params.set(learning_rate=0.001, batch_size=32, epochs=10)

    for epoch in range(10):
        loss = 1.0 - epoch * 0.08
        episode.track("train").append(loss=loss, epoch=epoch)

    episode.log("Training completed")
```

Data is saved to `.dreamlake/tutorial/my-first-experiment/`:

```
.dreamlake/
└── tutorial/
    └── my-first-experiment/
        ├── logs/logs.jsonl
        ├── parameters/parameters.json
        └── tracks/train/data.msgpack
```

## Remote Mode

Point to a DreamLake server for team collaboration:

```{code-block} python
:linenos:

from dreamlake import Episode

with Episode(prefix="team-project/my-experiment",
    url="http://your-server:3000",
    user_name="your-name"
) as episode:
    episode.log("Running on remote server")
    episode.params.set(learning_rate=0.001)
```

The API is identical — just add `url` and `user_name`.

## Next Steps

- [Logging](logging.md), [Parameters](parameters.md), [Tracks](tracks.md), [Files](files.md) — feature guides
- [CLI](cli.md) — command-line interface
