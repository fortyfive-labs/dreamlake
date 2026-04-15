# Quickstart

Get started with DreamLake in under 5 minutes.

## Installation

Install the latest version (currently {VERSION}):

```bash
pip install dreamlake=={VERSION}
```

## Your First Experiment

### Local Mode (Recommended for First Try)

Local mode stores everything on your filesystem - perfect for getting started:

```{code-block} python
:linenos:

from dreamlake import Episode

# Create a episode (stores data in .dreamlake/ directory)
with Episode(prefix="tutorial/my-first-experiment",
        local_path=".dreamlake") as episode:
    # Log messages
    episode.log("Training started")

    # Track parameters
    episode.params.set(
        learning_rate=0.001,
        batch_size=32,
        epochs=10
    )

    # Track metrics over time
    for epoch in range(10):
        loss = 1.0 - epoch * 0.08  # Simulated decreasing loss
        episode.track("train").append(loss=loss, epoch=epoch)

    episode.log("Training completed")
```

That's it! Your experiment data is now saved in `.dreamlake/tutorial/my-first-experiment/`.

### Where is My Data?

After running the code above, your data is organized like this:

```
.dreamlake/
└── tutorial/                    # workspace
    └── my-first-experiment/     # episode
        ├── logs/
        │   └── logs.jsonl       # your log messages
        ├── parameters/
        │   └── parameters.json  # your hyperparameters
        └── tracks/
            └── train/
                └── data.msgpack  # your metrics (msgpack-lines format)
```

## Common Patterns

### Tracking a Training Loop

```{code-block} python
:linenos:

from dreamlake import Episode

with Episode(prefix="project/train-model",
        local_path=".dreamlake") as episode:
    # Set hyperparameters
    episode.params.set(
        model="resnet50",
        optimizer="adam",
        learning_rate=0.001
    )

    # Training loop
    for epoch in range(100):
        # ... your training code ...
        train_loss = 0.5  # your actual loss
        val_acc = 0.9     # your actual accuracy

        # Track metrics
        episode.track("train").append(loss=train_loss, epoch=epoch)
        episode.track("val").append(accuracy=val_acc, epoch=epoch)

        # Log important events
        if epoch % 10 == 0:
            episode.log(f"Checkpoint at epoch {epoch}")
```

### Uploading Files

```{code-block} python
:linenos:

from dreamlake import Episode

with Episode(prefix="project/my-experiment",
        local_path=".dreamlake") as episode:
    # Train your model...
    # model.save("model.pth")

    # Upload the model file
    episode.files.upload("model.pth", path="/models"
    )

    # Upload a config file with metadata
    episode.files.upload("config.yaml", path="/configs",
        metadata={"version": "1.0"}
    )
```

## Remote Mode

To collaborate with your team, switch to url mode by pointing to a DreamLake server:

```{code-block} python
:linenos:

from dreamlake import Episode

with Episode(prefix="team-project/my-experiment",
    url="http://your-server:3000",
    user_name="your-name"
) as episode:
    # Use exactly the same API as local mode!
    episode.log("Running on url server")
    episode.params.set(learning_rate=0.001)
```

The API is identical - just add `url` and `user_name` parameters.

## Next Steps

- **Learn the basics**: Read the [Overview](overview.md) to understand core concepts
- **Explore features**: Check out guides for [Logging](logging.md), [Parameters](parameters.md), [Tracks](tracks.md), and [Files](files.md)
