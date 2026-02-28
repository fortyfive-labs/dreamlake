"""
Example: Time-range queries for track data (MCAP-like API)

This example demonstrates the read_by_time() method for querying track data
by timestamp ranges. This is especially useful for:
- Robotics applications (query pose data for last N seconds)
- Real-time monitoring (get recent metrics)
- Data analysis (extract specific time windows)
- Multi-modal data playback (synchronized by time)

The read_by_time() API is inspired by MCAP (the robotics data format) and
provides efficient time-based access to track data.
"""

import time
from dreamlake import Session


def example_time_range_query():
    """Query data within a specific time range."""

    with Session(
        prefix="tutorials/time-range-demo",
        root="./tutorial_data"
    ) as session:
        # Record robot data over 10 seconds
        base_time = time.time()

        print("Recording 10 seconds of robot data...")
        for i in range(100):
            current_time = base_time + i * 0.1

            session.track("robot/pose").append(
                position=[i * 0.1, i * 0.2, i * 0.3],
                orientation=[0.0, 0.0, 0.0, 1.0],
                _ts=current_time
            )

        print(f"✓ Recorded 100 pose samples over {time.time() - base_time:.2f} seconds\n")

        # Query last 3 seconds
        print("Querying last 3 seconds of data...")
        result = session.track("robot/pose").read_by_time(
            start_time=base_time + 7.0,
            end_time=base_time + 10.0,
            limit=1000
        )

        print(f"✓ Found {result['total']} samples in range [{7.0}s, {10.0}s)")
        print(f"  Time range: {result.get('startTime')} to {result.get('endTime')}")

        # Query middle 2 seconds
        print("\nQuerying middle 2 seconds [4.0s, 6.0s)...")
        result = session.track("robot/pose").read_by_time(
            start_time=base_time + 4.0,
            end_time=base_time + 6.0,
            limit=1000
        )

        print(f"✓ Found {result['total']} samples in middle range")


def example_reverse_iteration():
    """Query most recent data using reverse iteration."""

    with Session(
        prefix="tutorials/reverse-demo",
        root="./tutorial_data"
    ) as session:
        # Record some metric data
        base_time = time.time()

        print("Recording 50 metric samples...")
        for i in range(50):
            session.track("system/cpu").append(
                usage_percent=50 + i % 20,
                timestamp=i,
                _ts=base_time + i * 0.1
            )

        print(f"✓ Recorded 50 samples\n")

        # Get 10 most recent samples (reverse order)
        print("Fetching 10 most recent samples (reverse order)...")
        result = session.track("system/cpu").read_by_time(
            reverse=True,
            limit=10
        )

        print(f"✓ Retrieved {result['total']} most recent samples")
        print("  Latest samples (newest first):")

        for i, point in enumerate(result['data'][:5]):
            cpu_usage = point['data']['usage_percent']
            timestamp = point['data']['timestamp']
            print(f"    [{i}] timestamp={timestamp}, cpu={cpu_usage}%")


def example_open_ended_queries():
    """Query from start_time to end, or from beginning to end_time."""

    with Session(
        prefix="tutorials/open-queries-demo",
        root="./tutorial_data"
    ) as session:
        # Record data with gaps
        base_time = time.time()

        print("Recording data with time gaps...")
        timestamps = [0, 1, 2, 5, 6, 7, 10, 11, 12]  # Gaps at 3-4 and 8-9

        for t in timestamps:
            session.track("sensor/reading").append(
                value=t * 10,
                _ts=base_time + t
            )

        print(f"✓ Recorded {len(timestamps)} samples with gaps\n")

        # Query from time 5 to end
        print("Query from 5 seconds to end...")
        result = session.track("sensor/reading").read_by_time(
            start_time=base_time + 5,
            limit=100
        )
        print(f"✓ Found {result['total']} samples from t=5s onwards")

        # Query from beginning to time 7
        print("\nQuery from beginning to 7 seconds...")
        result = session.track("sensor/reading").read_by_time(
            end_time=base_time + 7,
            limit=100
        )
        print(f"✓ Found {result['total']} samples before t=7s")


def example_synchronized_multimodal():
    """Query synchronized multi-modal data by time."""

    with Session(
        prefix="tutorials/multimodal-time-demo",
        root="./tutorial_data"
    ) as session:
        # Record synchronized multi-modal data
        base_time = time.time()

        print("Recording synchronized multi-modal data...")

        # For better performance, collect data and batch append
        # This uses columnar storage format internally
        pose_batch = []
        velocity_batch = []
        image_batch = []

        for i in range(50):
            current_time = base_time + i * 0.1

            pose_batch.append({
                "position": [i * 0.1, i * 0.2, i * 0.3],
                "_ts": current_time
            })

            velocity_batch.append({
                "linear": [0.1, 0.0, 0.0],
                "angular": [0.0, 0.0, 0.05],
                "_ts": current_time
            })

            image_batch.append({
                "width": 640,
                "height": 480,
                "frame_id": i,
                "_ts": current_time
            })

        # Batch append for efficiency (uses columnar format)
        session.track("robot/pose").append_batch(pose_batch)
        session.track("robot/velocity").append_batch(velocity_batch)
        session.track("camera/left/image").append_batch(image_batch)

        print(f"✓ Recorded 50 synchronized timesteps (batch columnar format)\n")

        # Query all tracks for same time window
        query_start = base_time + 2.0
        query_end = base_time + 4.0

        print(f"Querying time range [{2.0}s, {4.0}s) across all tracks...")

        pose_result = session.track("robot/pose").read_by_time(
            start_time=query_start,
            end_time=query_end,
            limit=100
        )

        velocity_result = session.track("robot/velocity").read_by_time(
            start_time=query_start,
            end_time=query_end,
            limit=100
        )

        image_result = session.track("camera/left/image").read_by_time(
            start_time=query_start,
            end_time=query_end,
            limit=100
        )

        print(f"✓ Found synchronized data:")
        print(f"  Pose:     {pose_result['total']} samples")
        print(f"  Velocity: {velocity_result['total']} samples")
        print(f"  Images:   {image_result['total']} samples")

        # Verify timestamps match
        if pose_result['total'] > 0 and velocity_result['total'] > 0:
            pose_ts = pose_result['data'][0]['data']['_ts']
            vel_ts = velocity_result['data'][0]['data']['_ts']
            img_ts = image_result['data'][0]['data']['_ts']

            print(f"\n✓ All tracks synchronized:")
            print(f"  First timestamp: {pose_ts}")
            assert pose_ts == vel_ts == img_ts, "Timestamps should match!"


def example_comparison_index_vs_time():
    """Compare index-based vs time-based queries."""

    with Session(
        prefix="tutorials/index-vs-time-demo",
        root="./tutorial_data"
    ) as session:
        # Record data
        base_time = time.time()

        print("Recording 30 samples...")
        for i in range(30):
            session.track("metric").append(
                value=i * 10,
                step=i,
                _ts=base_time + i * 0.5
            )

        print("✓ Recorded 30 samples\n")

        # Index-based query
        print("Index-based query (indices 10-20):")
        result_index = session.track("metric").read(
            start_index=10,
            limit=10
        )
        print(f"  Retrieved {len(result_index['data'])} samples")
        print(f"  First sample: step={result_index['data'][0]['data']['step']}")

        # Time-based query
        print("\nTime-based query (5s to 10s):")
        result_time = session.track("metric").read_by_time(
            start_time=base_time + 5.0,
            end_time=base_time + 10.0,
            limit=100
        )
        print(f"  Retrieved {result_time['total']} samples")
        if result_time['total'] > 0:
            print(f"  First sample: step={result_time['data'][0]['data']['step']}")


if __name__ == "__main__":
    print("=" * 60)
    print("DreamLake Time-Range Queries Examples")
    print("=" * 60)
    print()

    print("Example 1: Time Range Queries")
    print("-" * 60)
    example_time_range_query()
    print()

    print("Example 2: Reverse Iteration (Most Recent First)")
    print("-" * 60)
    example_reverse_iteration()
    print()

    print("Example 3: Open-Ended Time Queries")
    print("-" * 60)
    example_open_ended_queries()
    print()

    print("Example 4: Synchronized Multi-Modal Data")
    print("-" * 60)
    example_synchronized_multimodal()
    print()

    print("Example 5: Index vs Time Comparison")
    print("-" * 60)
    example_comparison_index_vs_time()
    print()

    print("=" * 60)
    print("✓ All time-range query examples completed successfully!")
    print("=" * 60)
