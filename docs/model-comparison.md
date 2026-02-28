# Model Comparison

Compare multiple model architectures on the same task to find the best performer.

## The Scenario

You're evaluating different architectures (CNN, ResNet, ViT) and want to:
- Train each model with identical settings
- Track performance metrics for fair comparison
- Identify the best architecture

## Complete Code

```{code-block} python
:linenos:

from dreamlake import Session
import random

def train_model(architecture, session):
    """Train model with given architecture."""
    epochs = 20

    # Different architectures have different performance characteristics
    base_accuracy = {
        "cnn": 0.85,
        "resnet": 0.90,
        "vit": 0.92
    }[architecture]

    # Simulate training convergence
    for epoch in range(epochs):
        # Converge towards base accuracy with some noise
        progress = epoch / epochs
        accuracy = 0.5 + (base_accuracy - 0.5) * progress + random.uniform(-0.02, 0.02)
        accuracy = min(base_accuracy, accuracy)

        loss = (1 - progress) * 2.0 + random.uniform(-0.1, 0.1)

        session.track("metrics").append(accuracy=accuracy, epoch=epoch)
        session.track("train").append(loss=loss, epoch=epoch)

    return accuracy

def compare_architectures():
    """Compare different model architectures."""

    architectures = ["cnn", "resnet", "vit"]
    results = {}

    # Train each architecture
    for arch in architectures:
        with Session(
            name=f"comparison-{arch}",
            workspace="architecture-comparison",
            readme=f"Training {arch} on CIFAR-10",
            tags=["comparison", arch, "cifar10"],
        local_path=".dreamlake"
        ) as session:
            # Same configuration for fair comparison
            session.params.set(
                architecture=arch,
                dataset="cifar10",
                batch_size=128,
                learning_rate=0.001,
                epochs=20,
                optimizer="adam"
            )

            session.log(f"Training {arch} architecture")

            # Train
            final_accuracy = train_model(arch, session)

            session.log(f"Final accuracy: {final_accuracy:.4f}")

            results[arch] = final_accuracy

    # Print comparison
    print("\n" + "=" * 50)
    print("Architecture Comparison Results")
    print("=" * 50)
    sorted_archs = sorted(results.keys(), key=lambda x: results[x], reverse=True)

    for i, arch in enumerate(sorted_archs):
        rank = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else "  "
        print(f"{rank} {arch:10s}: {results[arch]:.4f}")

    print("\nAll experiments tracked in workspace:")
    print("  architecture-comparison/")
    for arch in architectures:
        print(f"    - comparison-{arch}/")

if __name__ == "__main__":
    compare_architectures()
```

## What Gets Created

**3 separate sessions** - One per architecture:
```
architecture-comparison/
├── comparison-cnn/
│   ├── parameters.json
│   ├── logs/logs.jsonl
│   └── tracks/
│       ├── metrics/
│       │   └── data.msgpack
│       └── train/
│           └── data.msgpack
├── comparison-resnet/
└── comparison-vit/
```

**Each session has identical structure:**
- Same parameters (except architecture)
- Same number of epochs tracked
- Same metrics (accuracy, loss)

## Key Patterns

**Consistent naming** - Use descriptive prefixes:
```python
name=f"comparison-{arch}"
```

**Fair comparison** - Same hyperparameters for all:
```python
session.params.set(
    batch_size=128,      # Same for all
    learning_rate=0.001, # Same for all
    epochs=20,           # Same for all
    architecture=arch    # Only this differs
)
```

**Collect and rank** - Compare results programmatically:
```python
results[arch] = final_accuracy
sorted_archs = sorted(results.keys(), key=lambda x: results[x], reverse=True)
```

## Real-World Example

**PyTorch model comparison:**

```{code-block} python
:linenos:

import torch
from dreamlake import Session

def create_model(architecture):
    if architecture == "cnn":
        return SimpleCNN()
    elif architecture == "resnet":
        return torchvision.models.resnet18(pretrained=False)
    elif architecture == "vit":
        return VisionTransformer()

def train_and_evaluate(model, train_loader, val_loader, session):
    optimizer = torch.optim.Adam(model.params, lr=0.001)
    criterion = torch.nn.CrossEntropyLoss()

    for epoch in range(20):
        # Training
        model.train()
        train_loss = 0.0
        for data, target in train_loader:
            optimizer.zero_grad()
            output = model(data)
            loss = criterion(output, target)
            loss.backward()
            optimizer.step()
            train_loss += loss.item()

        # Validation
        model.eval()
        correct = 0
        total = 0
        with torch.no_grad():
            for data, target in val_loader:
                output = model(data)
                pred = output.argmax(dim=1)
                correct += (pred == target).sum().item()
                total += target.size(0)

        accuracy = correct / total

        # Track metrics
        session.track("train").append(loss=train_loss, epoch=epoch)
        session.track("val").append(accuracy=accuracy, epoch=epoch)

    return accuracy

# Compare architectures
for arch in ["cnn", "resnet", "vit"]:
    with Session(name=f"comparison-{arch}", workspace="arch-comp",
        local_path=".dreamlake") as session:
        session.params.set(architecture=arch, dataset="cifar10")

        model = create_model(arch)
        final_acc = train_and_evaluate(model, train_loader, val_loader, session)

        # Save best model
        torch.save(model.state_dict(), f"{arch}_model.pth")
        session.files.upload(f"{arch}_model.pth", path="/models")
```

---

**Next:** See [Remote Collaboration](url-collaboration.md) for team workflows.
