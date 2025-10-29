# Hyperparameter Search

Run grid search over multiple hyperparameters and track each experiment separately.

## The Scenario

You want to find the best learning rate and batch size by:
- Testing multiple combinations
- Tracking each experiment independently
- Comparing results to find the best configuration

## Complete Code

```{code-block} python
:linenos:

from itertools import product
import random
from dreamlake import Session

def train_with_config(lr, batch_size, session):
    """Simulate training with given hyperparameters."""
    epochs = 10
    final_accuracy = 0

    for epoch in range(epochs):
        # Simulate: lower lr = better but slower convergence
        loss = 1.0 / (epoch + 1) * (lr / 0.01) + random.uniform(-0.05, 0.05)
        # Simulate: larger batch_size = slightly worse performance
        accuracy = min(0.95, 0.5 + epoch * 0.05 * (32 / batch_size))

        session.track("loss").append(value=loss, epoch=epoch)
        session.track("accuracy").append(value=accuracy, epoch=epoch)

        final_accuracy = accuracy

    return final_accuracy

def hyperparameter_search():
    """Run grid search over hyperparameters."""

    learning_rates = [0.1, 0.01, 0.001]
    batch_sizes = [16, 32, 64]

    results = []

    # Test each combination
    for lr, bs in product(learning_rates, batch_sizes):
        session_name = f"search-lr{lr}-bs{bs}"

        with Session(
            name=session_name,
            workspace="hyperparameter-search",
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
    print("\nAll experiments tracked separately in workspace:")
    print("  hyperparameter-search/")
    for r in results:
        print(f"    - search-lr{r['lr']}-bs{r['batch_size']}: {r['accuracy']:.4f}")

if __name__ == "__main__":
    hyperparameter_search()
```

## What Gets Created

**9 separate sessions** - One for each combination:
```
hyperparameter-search/
├── search-lr0.1-bs16/
├── search-lr0.1-bs32/
├── search-lr0.1-bs64/
├── search-lr0.01-bs16/
├── search-lr0.01-bs32/
├── search-lr0.01-bs64/
├── search-lr0.001-bs16/
├── search-lr0.001-bs32/
└── search-lr0.001-bs64/
```

**Each session contains:**
- Parameters: `{"learning_rate": 0.01, "batch_size": 32, ...}`
- Logs: Start and completion messages
- Tracks: Loss and accuracy over 10 epochs

## Key Patterns

**Unique session names** - Each experiment gets its own session:
```python
session_name = f"search-lr{lr}-bs{bs}"
```

**Descriptive tags** - Easy filtering later:
```python
tags=["grid-search", f"lr-{lr}", f"bs-{bs}"]
```

**Collect results** - Compare across experiments:
```python
results.append({"lr": lr, "batch_size": bs, "accuracy": final_accuracy})
best = max(results, key=lambda x: x["accuracy"])
```

## Variations

**Random search** instead of grid search:
```python
import random

for i in range(20):
    lr = 10 ** random.uniform(-4, -1)  # 0.0001 to 0.1
    bs = random.choice([16, 32, 64, 128])

    with Session(name=f"random-{i}", workspace="random-search") as session:
        session.parameters().set(learning_rate=lr, batch_size=bs)
        # Train and track...
```

**Bayesian optimization** - Track trials sequentially:
```python
for trial in range(100):
    # Get next params from Bayesian optimizer
    params = optimizer.suggest()

    with Session(name=f"trial-{trial}", workspace="bayes-opt") as session:
        session.parameters().set(**params)
        accuracy = train_and_evaluate(params, session)

        # Update Bayesian optimizer
        optimizer.register(params, accuracy)
```

---

**Next:** See [Model Comparison](model-comparison.md) for comparing different architectures.
