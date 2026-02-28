"""
Example: Batch appending with columnar format for efficient storage

This example demonstrates the columnar format feature in DreamLake tracks.
When you append batches of data, DreamLake automatically uses columnar storage
format which is more efficient for batch writes and storage.

Key features:
- Row format: Used for single appends {"value": 10, "_ts": 1.0}
- Columnar format: Automatically used for batch appends {"_ts": [1.0, 2.0], "value": [10, 20]}
- Format is transparent to users - reading works the same regardless of storage format
- Columnar format provides better compression and batch write performance
"""

import sys
sys.path.insert(0, '../../src')

import time
from dreamlake import Session


def example_single_vs_batch_appends():
    """Compare single appends (row format) vs batch appends (columnar format)."""

    with Session(
        prefix="tutorials/columnar-demo",
        root="./tutorial_data"
    ) as session:
        print("1. Single appends (stored as rows)")
        print("-" * 60)

        # Single appends - each creates a new row entry
        for i in range(5):
            session.track("single_metrics").append(
                loss=0.5 - i * 0.05,
                accuracy=0.7 + i * 0.03,
                epoch=i,
                _ts=time.time() + i
            )

        print("   Appended 5 data points individually")
        print("   Storage format: Row-based (one entry per append)")

        print("\n2. Batch append (stored in columnar format)")
        print("-" * 60)

        # Batch append - automatically uses columnar format
        batch_data = [
            {"loss": 0.45, "accuracy": 0.72, "epoch": 5, "_ts": time.time() + 5},
            {"loss": 0.40, "accuracy": 0.75, "epoch": 6, "_ts": time.time() + 6},
            {"loss": 0.35, "accuracy": 0.78, "epoch": 7, "_ts": time.time() + 7},
            {"loss": 0.30, "accuracy": 0.81, "epoch": 8, "_ts": time.time() + 8},
            {"loss": 0.25, "accuracy": 0.84, "epoch": 9, "_ts": time.time() + 9},
        ]

        result = session.track("batch_metrics").append_batch(batch_data)

        print(f"   Appended {result['count']} data points in batch")
        print("   Storage format: Columnar (one entry for all rows)")
        print(f"   Index range: {result['startIndex']} to {result['endIndex']}")

        print("\n3. Reading data (format is transparent)")
        print("-" * 60)

        # Reading works the same regardless of storage format
        single_data = session.track("single_metrics").read(limit=10)
        batch_data_read = session.track("batch_metrics").read(limit=10)

        print(f"   Single appends: {single_data['total']} points")
        print(f"   Batch appends:  {batch_data_read['total']} points")
        print("   Both return the same data structure!")


def example_large_batch_efficiency():
    """Demonstrate efficiency of columnar format for large batches."""

    with Session(
        prefix="tutorials/large-batch-demo",
        root="./tutorial_data"
    ) as session:
        print("Batch appending 1000 training steps efficiently")
        print("-" * 60)

        # Create a large batch
        batch_size = 1000
        base_time = time.time()

        large_batch = [
            {
                "step": i,
                "train_loss": 1.0 / (i + 1),
                "val_loss": 1.2 / (i + 1),
                "learning_rate": 0.001 * (0.99 ** (i // 100)),
                "_ts": base_time + i * 0.1
            }
            for i in range(batch_size)
        ]

        # Batch append - uses columnar format internally
        start = time.time()
        result = session.track("training_metrics").append_batch(large_batch)
        elapsed = time.time() - start

        print(f"   Appended {result['count']} points in {elapsed:.3f}s")
        print(f"   Throughput: {result['count'] / elapsed:.0f} points/sec")
        print(f"   Storage: Columnar format (efficient compression)")

        # Read back a sample
        print("\n   Reading sample of data...")
        sample = session.track("training_metrics").read(start_index=0, limit=5)

        print(f"   First 3 points:")
        for i, point in enumerate(sample['data'][:3]):
            data = point['data']
            print(f"     [{i}] step={data['step']}, "
                  f"train_loss={data['train_loss']:.4f}, "
                  f"val_loss={data['val_loss']:.4f}")


def example_mixed_append_styles():
    """Mix single and batch appends in the same track."""

    with Session(
        prefix="tutorials/mixed-appends-demo",
        root="./tutorial_data"
    ) as session:
        print("Mixing single and batch appends")
        print("-" * 60)

        # Start with some single appends
        for epoch in range(3):
            session.track("mixed").append(
                epoch=epoch,
                loss=1.0 / (epoch + 1),
                _ts=time.time() + epoch
            )

        print("   Added 3 points individually (row format)")

        # Add a batch
        batch = [
            {"epoch": i, "loss": 1.0 / (i + 1), "_ts": time.time() + i}
            for i in range(3, 8)
        ]
        session.track("mixed").append_batch(batch)

        print("   Added 5 points in batch (columnar format)")

        # Add more single appends
        for epoch in range(8, 10):
            session.track("mixed").append(
                epoch=epoch,
                loss=1.0 / (epoch + 1),
                _ts=time.time() + epoch
            )

        print("   Added 2 more points individually (row format)")

        # Read all - format mixing is transparent
        all_data = session.track("mixed").read(limit=100)

        print(f"\n   Total points: {all_data['total']}")
        print("   All data accessible uniformly, regardless of storage format!")

        # Show statistics
        stats = session.track("mixed").stats()
        print(f"\n   Track statistics:")
        print(f"     Total data points: {stats['totalDataPoints']}")
        print(f"     Created at: {stats['createdAt']}")


def example_columnar_with_complex_data():
    """Batch append with nested/complex data structures."""

    with Session(
        prefix="tutorials/complex-columnar-demo",
        root="./tutorial_data"
    ) as session:
        print("Batch appending complex nested data")
        print("-" * 60)

        # Complex batch with nested structures
        batch = [
            {
                "step": i,
                "position": [i * 0.1, i * 0.2, i * 0.3],  # 3D vector
                "quaternion": [0.0, 0.0, 0.0, 1.0],        # Rotation
                "metrics": {                                # Nested dict
                    "speed": i * 0.5,
                    "distance": i * 2.0
                },
                "_ts": time.time() + i * 0.1
            }
            for i in range(10)
        ]

        result = session.track("robot/state").append_batch(batch)

        print(f"   Appended {result['count']} complex data points")
        print("   Each point contains: position, quaternion, nested metrics")

        # Read back
        data = session.track("robot/state").read(limit=3)

        print(f"\n   First point:")
        first = data['data'][0]['data']
        print(f"     position: {first['position']}")
        print(f"     quaternion: {first['quaternion']}")
        print(f"     metrics: {first['metrics']}")


def example_performance_comparison():
    """Compare performance of single vs batch appends."""

    with Session(
        prefix="tutorials/performance-demo",
        root="./tutorial_data"
    ) as session:
        print("Performance comparison: Single vs Batch appends")
        print("-" * 60)

        n = 100

        # Test single appends
        print(f"\n   Single appends ({n} points)...")
        start = time.time()
        for i in range(n):
            session.track("perf_single").append(
                value=i,
                _ts=time.time() + i * 0.01
            )
        session.track("perf_single").flush()
        single_time = time.time() - start

        print(f"     Time: {single_time:.3f}s")
        print(f"     Throughput: {n / single_time:.0f} points/sec")

        # Test batch append
        print(f"\n   Batch append ({n} points)...")
        batch = [
            {"value": i, "_ts": time.time() + i * 0.01}
            for i in range(n)
        ]
        start = time.time()
        session.track("perf_batch").append_batch(batch)
        batch_time = time.time() - start

        print(f"     Time: {batch_time:.3f}s")
        print(f"     Throughput: {n / batch_time:.0f} points/sec")

        speedup = single_time / batch_time
        print(f"\n   Speedup: {speedup:.1f}x faster with batch append")
        print("   Plus: Columnar format provides better compression!")


if __name__ == "__main__":
    print("=" * 60)
    print("DreamLake Batch Appending & Columnar Format Examples")
    print("=" * 60)
    print()

    print("Example 1: Single vs Batch Appends")
    print("=" * 60)
    example_single_vs_batch_appends()
    print()

    print("\nExample 2: Large Batch Efficiency")
    print("=" * 60)
    example_large_batch_efficiency()
    print()

    print("\nExample 3: Mixed Append Styles")
    print("=" * 60)
    example_mixed_append_styles()
    print()

    print("\nExample 4: Complex Data Structures")
    print("=" * 60)
    example_columnar_with_complex_data()
    print()

    print("\nExample 5: Performance Comparison")
    print("=" * 60)
    example_performance_comparison()
    print()

    print("=" * 60)
    print("All examples completed successfully!")
    print()
    print("Key takeaways:")
    print("  - Use append() for single data points (row format)")
    print("  - Use append_batch() for multiple data points (columnar format)")
    print("  - Columnar format is automatic and transparent")
    print("  - Reading works the same regardless of storage format")
    print("  - Batch appends are faster and more storage-efficient")
    print("=" * 60)
