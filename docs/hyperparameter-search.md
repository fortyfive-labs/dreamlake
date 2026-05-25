# Hyperparameter Search

Grid search with each configuration tracked as a separate episode.

```{code-block} python
:linenos:

from itertools import product
import random
from dreamlake import Episode

def train_with_config(lr, batch_size, episode):
    for epoch in range(10):
        loss = 1.0 / (epoch + 1) * (lr / 0.01) + random.uniform(-0.05, 0.05)
        accuracy = min(0.95, 0.5 + epoch * 0.05 * (32 / batch_size))
        episode.track("train").append(loss=loss, epoch=epoch)
        episode.track("metrics").append(accuracy=accuracy, epoch=epoch)
    return accuracy

def hyperparameter_search():
    learning_rates = [0.1, 0.01, 0.001]
    batch_sizes = [16, 32, 64]
    results = []

    for lr, bs in product(learning_rates, batch_sizes):
        with Episode(
            name=f"search-lr{lr}-bs{bs}",
            workspace="hyperparameter-search",
            tags=["grid-search", f"lr-{lr}", f"bs-{bs}"],
            local_path=".dreamlake"
        ) as episode:
            episode.params.set(learning_rate=lr, batch_size=bs, optimizer="sgd", epochs=10)
            final_accuracy = train_with_config(lr, bs, episode)
            results.append({"lr": lr, "batch_size": bs, "accuracy": final_accuracy})

    best = max(results, key=lambda x: x["accuracy"])
    print(f"Best: lr={best['lr']}, bs={best['batch_size']}, acc={best['accuracy']:.4f}")

if __name__ == "__main__":
    hyperparameter_search()
```

## Variations

**Random search:**

```{code-block} python
:linenos:

for i in range(20):
    lr = 10 ** random.uniform(-4, -1)
    bs = random.choice([16, 32, 64, 128])
    with Episode(name=f"random-{i}", workspace="random-search",
        local_path=".dreamlake") as episode:
        episode.params.set(learning_rate=lr, batch_size=bs)
        # ...
```

**Bayesian optimization:**

```{code-block} python
:linenos:

for trial in range(100):
    params = optimizer.suggest()
    with Episode(name=f"trial-{trial}", workspace="bayes-opt",
        local_path=".dreamlake") as episode:
        episode.params.set(**params)
        accuracy = train_and_evaluate(params, episode)
        optimizer.register(params, accuracy)
```
