# Model Comparison

Compare architectures on the same task with identical settings.

```{code-block} python
:linenos:

from dreamlake import Episode
import random

def train_model(architecture, episode):
    base_accuracy = {"cnn": 0.85, "resnet": 0.90, "vit": 0.92}[architecture]
    for epoch in range(20):
        progress = epoch / 20
        accuracy = 0.5 + (base_accuracy - 0.5) * progress + random.uniform(-0.02, 0.02)
        loss = (1 - progress) * 2.0 + random.uniform(-0.1, 0.1)
        episode.track("metrics").append(accuracy=accuracy, epoch=epoch)
        episode.track("train").append(loss=loss, epoch=epoch)
    return accuracy

def compare_architectures():
    results = {}
    for arch in ["cnn", "resnet", "vit"]:
        with Episode(
            name=f"comparison-{arch}",
            workspace="architecture-comparison",
            tags=["comparison", arch, "cifar10"],
            local_path=".dreamlake"
        ) as episode:
            episode.params.set(
                architecture=arch, dataset="cifar10",
                batch_size=128, learning_rate=0.001, epochs=20
            )
            results[arch] = train_model(arch, episode)

    for arch in sorted(results, key=results.get, reverse=True):
        print(f"{arch:10s}: {results[arch]:.4f}")

if __name__ == "__main__":
    compare_architectures()
```
