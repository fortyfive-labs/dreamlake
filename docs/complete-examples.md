# Complete Examples

This document contains complete, runnable examples showcasing common use cases for Dreamlake.

## Example 1: Simple Training Loop

A basic training loop with logging, parameters, and metrics tracking.

```python
"""Simple training loop example."""
import random
from dreamlake import Session

def train_simple_model():
    """Train a simple model and track with Dreamlake."""

    with Session(
        name="simple-training",
        workspace="tutorials",
        local_path="./experiments",
        description="Simple training example",
        tags=["tutorial", "simple"]
    ) as session:
        # Track hyperparameters
        session.parameters().set(
            learning_rate=0.001,
            batch_size=32,
            epochs=10,
            model="simple_nn"
        )

        session.log("Starting training", level="info")

        # Training loop
        for epoch in range(10):
            # Simulate training
            train_loss = 1.0 / (epoch + 1) + random.uniform(-0.05, 0.05)
            val_loss = 1.2 / (epoch + 1) + random.uniform(-0.05, 0.05)
            accuracy = min(0.95, 0.5 + epoch * 0.05)

            # Track metrics
            session.track("train_loss").append(value=train_loss, epoch=epoch)
            session.track("val_loss").append(value=val_loss, epoch=epoch)
            session.track("accuracy").append(value=accuracy, epoch=epoch)

            # Log progress
            session.log(
                f"Epoch {epoch + 1}/10 complete",
                level="info",
                metadata={
                    "train_loss": train_loss,
                    "val_loss": val_loss,
                    "accuracy": accuracy
                }
            )

        session.log("Training complete!", level="info")
        print(f"✓ Experiment saved to: {session._storage.root_path}")

if __name__ == "__main__":
    train_simple_model()
```

## Example 2: PyTorch MNIST Training

Complete PyTorch MNIST training with full experiment tracking.

```python
"""PyTorch MNIST training with Dreamlake tracking."""
import torch
import torch.nn as nn
import torch.optim as optim
from torchvision import datasets, transforms
from dreamlake import Session

class SimpleNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.fc1 = nn.Linear(784, 128)
        self.fc2 = nn.Linear(128, 64)
        self.fc3 = nn.Linear(64, 10)
        self.relu = nn.ReLU()

    def forward(self, x):
        x = x.view(-1, 784)
        x = self.relu(self.fc1(x))
        x = self.relu(self.fc2(x))
        return self.fc3(x)

def train_mnist():
    """Train MNIST model with Dreamlake tracking."""

    # Setup
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    batch_size = 64
    epochs = 5
    learning_rate = 0.001

    # Data loaders
    transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize((0.1307,), (0.3081,))
    ])

    train_dataset = datasets.MNIST('./data', train=True, download=True, transform=transform)
    test_dataset = datasets.MNIST('./data', train=False, transform=transform)

    train_loader = torch.utils.data.DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
    test_loader = torch.utils.data.DataLoader(test_dataset, batch_size=batch_size)

    # Model
    model = SimpleNet().to(device)
    optimizer = optim.Adam(model.parameters(), lr=learning_rate)
    criterion = nn.CrossEntropyLoss()

    # Dreamlake session
    with Session(
        name="mnist-pytorch",
        workspace="computer-vision",
        local_path="./experiments",
        description="MNIST classification with PyTorch",
        tags=["mnist", "pytorch", "classification"]
    ) as session:
        # Track configuration
        session.parameters().set({
            "model": {
                "architecture": "SimpleMLP",
                "layers": [784, 128, 64, 10]
            },
            "training": {
                "optimizer": "adam",
                "learning_rate": learning_rate,
                "batch_size": batch_size,
                "epochs": epochs
            },
            "device": str(device),
            "dataset": "MNIST"
        })

        session.log(f"Training on {device}", level="info")

        best_accuracy = 0.0

        # Training loop
        for epoch in range(epochs):
            # Training
            model.train()
            train_loss = 0.0
            correct = 0
            total = 0

            for batch_idx, (data, target) in enumerate(train_loader):
                data, target = data.to(device), target.to(device)

                optimizer.zero_grad()
                output = model(data)
                loss = criterion(output, target)
                loss.backward()
                optimizer.step()

                train_loss += loss.item()
                pred = output.argmax(dim=1)
                correct += pred.eq(target).sum().item()
                total += target.size(0)

            avg_train_loss = train_loss / len(train_loader)
            train_accuracy = correct / total

            # Validation
            model.eval()
            val_loss = 0.0
            correct = 0
            total = 0

            with torch.no_grad():
                for data, target in test_loader:
                    data, target = data.to(device), target.to(device)
                    output = model(data)
                    loss = criterion(output, target)

                    val_loss += loss.item()
                    pred = output.argmax(dim=1)
                    correct += pred.eq(target).sum().item()
                    total += target.size(0)

            avg_val_loss = val_loss / len(test_loader)
            val_accuracy = correct / total

            # Track metrics
            session.track("train_loss").append(value=avg_train_loss, epoch=epoch)
            session.track("train_accuracy").append(value=train_accuracy, epoch=epoch)
            session.track("val_loss").append(value=avg_val_loss, epoch=epoch)
            session.track("val_accuracy").append(value=val_accuracy, epoch=epoch)

            # Log progress
            session.log(
                f"Epoch {epoch + 1}/{epochs}",
                level="info",
                metadata={
                    "train_loss": avg_train_loss,
                    "train_acc": train_accuracy,
                    "val_loss": avg_val_loss,
                    "val_acc": val_accuracy
                }
            )

            # Save best model
            if val_accuracy > best_accuracy:
                best_accuracy = val_accuracy
                torch.save(model.state_dict(), "best_model.pth")
                session.files().upload(
                    "best_model.pth",
                    path="/models",
                    description=f"Best model (accuracy: {best_accuracy:.4f})",
                    tags=["best"],
                    metadata={"epoch": epoch, "accuracy": best_accuracy}
                )
                session.log(f"New best model saved: {best_accuracy:.4f}", level="info")

        # Save final model
        torch.save(model.state_dict(), "final_model.pth")
        session.files().upload(
            "final_model.pth",
            path="/models",
            description="Final model after all epochs",
            tags=["final"]
        )

        session.log("Training complete!", level="info")
        print(f"✓ Best accuracy: {best_accuracy:.4f}")

if __name__ == "__main__":
    train_mnist()
```

## Example 3: Hyperparameter Search

Grid search over hyperparameters with full tracking.

```python
"""Hyperparameter search example."""
from itertools import product
import random
from dreamlake import Session

def train_with_config(lr, batch_size, session):
    """Simulate training with given hyperparameters."""
    # Simulate training
    epochs = 10
    for epoch in range(epochs):
        # Lower learning rate = better but slower convergence
        loss = 1.0 / (epoch + 1) * (lr / 0.01) + random.uniform(-0.05, 0.05)
        # Larger batch size = slightly worse performance
        accuracy = min(0.95, 0.5 + epoch * 0.05 * (32 / batch_size))

        session.track("loss").append(value=loss, epoch=epoch)
        session.track("accuracy").append(value=accuracy, epoch=epoch)

    return accuracy

def hyperparameter_search():
    """Run grid search over hyperparameters."""

    learning_rates = [0.1, 0.01, 0.001]
    batch_sizes = [16, 32, 64]

    results = []

    for lr, bs in product(learning_rates, batch_sizes):
        session_name = f"search-lr{lr}-bs{bs}"

        with Session(
            name=session_name,
            workspace="hyperparameter-search",
            local_path="./experiments",
            description=f"Grid search: lr={lr}, batch_size={bs}",
            tags=["grid-search", f"lr-{lr}", f"bs-{bs}"]
        ) as session:
            # Track hyperparameters
            session.parameters().set(
                learning_rate=lr,
                batch_size=bs,
                optimizer="sgd",
                epochs=10
            )

            session.log(f"Starting training with lr={lr}, bs={bs}")

            # Train
            final_accuracy = train_with_config(lr, bs, session)

            session.log(f"Final accuracy: {final_accuracy:.4f}")

            results.append({
                "lr": lr,
                "batch_size": bs,
                "accuracy": final_accuracy
            })

    # Find best configuration
    best = max(results, key=lambda x: x["accuracy"])
    print("\n" + "=" * 50)
    print("Hyperparameter Search Complete!")
    print("=" * 50)
    print(f"Best configuration:")
    print(f"  Learning rate: {best['lr']}")
    print(f"  Batch size: {best['batch_size']}")
    print(f"  Accuracy: {best['accuracy']:.4f}")

if __name__ == "__main__":
    hyperparameter_search()
```

## Example 4: Experiment Comparison

Track multiple experiments and compare results.

```python
"""Compare multiple experiments."""
from dreamlake import Session
import random

def train_model(architecture, session):
    """Train model with given architecture."""
    epochs = 20

    # Different architectures converge differently
    base_lr = {"cnn": 0.85, "resnet": 0.90, "vit": 0.92}[architecture]

    for epoch in range(epochs):
        acc = min(base_lr, 0.5 + epoch * (base_lr - 0.5) / epochs + random.uniform(-0.02, 0.02))
        session.track("accuracy").append(value=acc, epoch=epoch)

    return acc

def compare_architectures():
    """Compare different model architectures."""

    architectures = ["cnn", "resnet", "vit"]
    results = {}

    for arch in architectures:
        with Session(
            name=f"comparison-{arch}",
            workspace="architecture-comparison",
            local_path="./experiments",
            description=f"Training {arch} on CIFAR-10",
            tags=["comparison", arch, "cifar10"]
        ) as session:
            # Configuration
            session.parameters().set(
                architecture=arch,
                dataset="cifar10",
                batch_size=128,
                epochs=20
            )

            session.log(f"Training {arch} architecture")

            # Train
            final_acc = train_model(arch, session)

            session.log(f"Final accuracy: {final_acc:.4f}")

            results[arch] = final_acc

    # Print comparison
    print("\n" + "=" * 50)
    print("Architecture Comparison Results")
    print("=" * 50)
    for arch in sorted(results.keys(), key=lambda x: results[x], reverse=True):
        print(f"{arch:10s}: {results[arch]:.4f}")

if __name__ == "__main__":
    compare_architectures()
```

## Example 5: Logging and Debugging

Comprehensive logging for debugging.

```python
"""Debugging example with comprehensive logging."""
from dreamlake import Session
import random

def train_with_debug():
    """Training with extensive debugging logs."""

    with Session(
        name="debug-training",
        workspace="debugging",
        local_path="./experiments",
        description="Training with debug logging",
        tags=["debug"]
    ) as session:
        session.parameters().set(
            learning_rate=0.001,
            batch_size=32,
            model="debug_net"
        )

        session.log("Training session started", level="info")
        session.log("Initializing model", level="debug")

        # Simulate some issues
        for epoch in range(5):
            session.log(f"Starting epoch {epoch + 1}", level="debug")

            # Simulate varying behavior
            loss = 1.0 / (epoch + 1)

            if epoch == 2:
                # Simulate a warning
                session.log(
                    "Learning rate may be too high",
                    level="warn",
                    metadata={"current_lr": 0.001, "suggested_lr": 0.0001}
                )

            if random.random() < 0.2:
                # Simulate occasional error
                session.log(
                    "Gradient clipping applied",
                    level="warn",
                    metadata={"gradient_norm": 15.5, "max_norm": 10.0}
                )

            session.track("loss").append(value=loss, epoch=epoch)

            session.log(
                f"Epoch {epoch + 1} complete",
                level="info",
                metadata={"loss": loss}
            )

        session.log("Training complete", level="info")

if __name__ == "__main__":
    train_with_debug()
```

## Running the Examples

All examples are available in the `docs/examples/` directory:

```bash
# Navigate to docs/examples
cd docs/examples

# Run any example
python complete_training.py
python hyperparameter_search.py
python architecture_comparison.py
```

## Next Steps

- Explore individual feature docs:
  - [Logging](03-logging.md)
  - [Parameters](04-parameters.md)
  - [Tracks](05-tracks.md)
  - [Files](06-files.md)

- Check out runnable examples in `docs/examples/`
