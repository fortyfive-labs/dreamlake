"""Tracks example - Time-series metrics tracking."""
import sys
sys.path.insert(0, '../../src')

from dreamlake import Session
import random

def main():
    print("=" * 60)
    print("Tracks Example - Time-Series Metrics")
    print("=" * 60)

    with Session(
        name="tracks-demo",
        workspace="tutorials",
        local_path="./tutorial_data"
    ) as session:
        session.parameters().set(epochs=10, learning_rate=0.001)

        print("\n1. Tracking training metrics...")

        # Track metrics over epochs
        for epoch in range(10):
            # Simulate training
            train_loss = 1.0 / (epoch + 1) + random.uniform(-0.05, 0.05)
            val_loss = 1.2 / (epoch + 1) + random.uniform(-0.05, 0.05)
            accuracy = min(0.95, 0.5 + epoch * 0.05)

            # Append single data points
            session.track("train").append(loss=train_loss, epoch=epoch)
            session.track("val").append(loss=val_loss, epoch=epoch)
            session.track("metrics").append(accuracy=accuracy, epoch=epoch)

            print(f"   Epoch {epoch + 1}: loss={train_loss:.4f}, acc={accuracy:.4f}")

        print("\n2. Batch appending data points...")

        # Batch append for better performance
        # Note: Batch appends use columnar storage format internally for efficiency
        batch_data = [
            {"value": 0.45, "step": 100, "batch": 1},
            {"value": 0.42, "step": 200, "batch": 2},
            {"value": 0.40, "step": 300, "batch": 3},
            {"value": 0.38, "step": 400, "batch": 4},
        ]
        result = session.track("step_loss").append_batch(batch_data)
        print(f"   Appended {result['count']} data points (columnar format)")
        print(f"   Faster than {result['count']} individual appends!")

        print("\n3. Flexible schema - multiple metrics per point...")

        # Track multiple metrics in one track
        session.track("all_metrics").append(
            epoch=5,
            train_loss=0.3,
            val_loss=0.35,
            train_acc=0.85,
            val_acc=0.82,
            learning_rate=0.001
        )

        print("\n4. Reading track data...")

        # Read track data
        result = session.track("train_loss").read(start_index=0, limit=5)
        print(f"   Read {result['total']} data points:")
        for point in result['data'][:3]:
            print(f"     Index {point['index']}: {point['data']}")

        print("\n5. Getting track statistics...")

        # Get track stats
        stats = session.track("train_loss").stats()
        print(f"   Track: {stats['name']}")
        print(f"   Total points: {stats['totalDataPoints']}")

        print("\n6. Listing all tracks...")

        # List all tracks
        tracks = session.track("train_loss").list_all()
        print(f"   Found {len(tracks)} tracks:")
        for track in tracks:
            print(f"     - {track['name']}: {track['totalDataPoints']} points")

        session.log("Tracks demo complete", level="info")

    print("\n✓ All metrics tracked!")
    print("\n" + "=" * 60)
    print("View track data (msgpack-lines format):")
    print("  python -c \"import msgpack; [print(obj) for obj in msgpack.Unpacker(open('tutorial_data/.dreamlake/tutorials/tracks-demo/tracks/train/data.msgpack', 'rb'), raw=False)]\"")
    print("  cat tutorial_data/.dreamlake/tutorials/tracks-demo/tracks/train/metadata.json")
    print("=" * 60)

if __name__ == "__main__":
    main()
