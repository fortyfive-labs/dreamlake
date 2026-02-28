# Quickstart

Get started with DreamLake in under 5 minutes.

## Installation

Install the latest version (currently {{VERSION}}):

```bash
pip install dreamlake
```

Or install a specific version:

```bash
pip install dreamlake==0.2.4
```

## Your First Experiment

### Local Mode (Recommended for First Try)

Local mode stores everything on your filesystem - perfect for getting started:

```{code-block} python
:linenos:

from dreamlake import Session

# Create a session (stores data in .dreamlake/ directory)
with Session(prefix="tutorial/my-first-experiment",
        local_path=".dreamlake") as session:
    # Log messages
    session.log("Training started")

    # Track parameters
    session.params.set(
        learning_rate=0.001,
        batch_size=32,
        epochs=10
    )

    # Track metrics over time
    for epoch in range(10):
        loss = 1.0 - epoch * 0.08  # Simulated decreasing loss
        session.track("train").append(loss=loss, epoch=epoch)

    session.log("Training completed")
```

That's it! Your experiment data is now saved in `.dreamlake/tutorial/my-first-experiment/`.

### Where is My Data?

After running the code above, your data is organized like this:

```
.dreamlake/
└── tutorial/                    # workspace
    └── my-first-experiment/     # session
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

from dreamlake import Session

with Session(prefix="project/train-model",
        local_path=".dreamlake") as session:
    # Set hyperparameters
    session.params.set(
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
        session.track("train").append(loss=train_loss, epoch=epoch)
        session.track("val").append(accuracy=val_acc, epoch=epoch)

        # Log important events
        if epoch % 10 == 0:
            session.log(f"Checkpoint at epoch {epoch}")
```

### Uploading Files

```{code-block} python
:linenos:

from dreamlake import Session

with Session(prefix="project/my-experiment",
        local_path=".dreamlake") as session:
    # Train your model...
    # model.save("model.pth")

    # Upload the model file
    session.files.upload("model.pth", path="/models"
    )

    # Upload a config file with metadata
    session.files.upload("config.yaml", path="/configs",
        metadata={"version": "1.0"}
    )
```

## Remote Mode

To collaborate with your team, switch to url mode by pointing to a DreamLake server:

```{code-block} python
:linenos:

from dreamlake import Session

with Session(prefix="team-project/my-experiment",
    url="http://your-server:3000",
    user_name="your-name"
) as session:
    # Use exactly the same API as local mode!
    session.log("Running on url server")
    session.params.set(learning_rate=0.001)
```

The API is identical - just add `url` and `user_name` parameters.

## Next Steps

- **Learn the basics**: Read the [Overview](overview.md) to understand core concepts
- **Explore features**: Check out guides for [Logging](logging.md), [Parameters](parameters.md), [Tracks](tracks.md), and [Files](files.md)
