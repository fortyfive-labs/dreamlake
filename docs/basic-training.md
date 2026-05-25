# Basic Training Loop

```{code-block} python
:linenos:

import random
from dreamlake import Episode

def train_simple_model():
    with Episode(prefix="tutorials/simple-training",
        tags=["tutorial"],
        local_path=".dreamlake"
    ) as episode:
        episode.params.set(
            learning_rate=0.001,
            batch_size=32,
            epochs=10,
            optimizer="adam",
            model="simple_nn"
        )

        episode.log("Starting training")

        for epoch in range(10):
            train_loss = 1.0 / (epoch + 1) + random.uniform(-0.05, 0.05)
            val_loss = 1.2 / (epoch + 1) + random.uniform(-0.05, 0.05)
            accuracy = min(0.95, 0.5 + epoch * 0.05)

            episode.track("train").append(loss=train_loss, epoch=epoch)
            episode.track("val").append(loss=val_loss, epoch=epoch)
            episode.track("metrics").append(accuracy=accuracy, epoch=epoch)

            episode.log(f"Epoch {epoch + 1}/10",
                metadata={"train_loss": train_loss, "val_loss": val_loss, "accuracy": accuracy})

        # Save model
        with open("model.pth", "w") as f:
            f.write("model weights")
        episode.files.upload("model.pth", path="/models", tags=["final"])

        episode.log("Training complete")

if __name__ == "__main__":
    train_simple_model()
```
