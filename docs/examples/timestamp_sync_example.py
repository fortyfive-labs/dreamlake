"""
Example: Syncing multi-modal data with timestamps

This example shows how to use _ts=-1 to synchronize data across multiple tracks,
useful for robotics applications where you want to log pose, images, and sensor
data with the same timestamp.

The _ts=-1 inheritance works:
- Across ALL tracks within a episode (not per-track)
- Across different Python files/modules that share the same episode object
- For both track data and file uploads (files can be logged alongside tracks)

Usage pattern:
1. First append: Don't set _ts (auto-generated)
2. Following appends: Use _ts=-1 to inherit that timestamp
"""

import time
from dreamlake import Episode


def log_robot_data_synchronized():
    """Example: Log robot pose, camera image, and sensor data with synchronized timestamps."""

    with Episode(
        name="robot-sync-demo",
        workspace="robotics",
        local_path=".dreamlake"
    ) as episode:
        # Simulate 10 timesteps of robot operation
        for step in range(10):
            # First log: robot pose (auto-generates timestamp)
            episode.track("robot/pose").append(
                position=[1.0 + step * 0.1, 2.0, 3.0],
                orientation=[0.0, 0.0, 0.0, 1.0]
            )

            # Following logs: inherit the same timestamp using _ts=-1
            episode.track("camera/left/image").append(
                width=640,
                height=480,
                frame_id=step,
                _ts=-1  # Inherits timestamp from robot/pose
            )

            episode.track("camera/right/image").append(
                width=640,
                height=480,
                frame_id=step,
                _ts=-1  # Same timestamp
            )

            episode.track("robot/velocity").append(
                linear=[0.1, 0.0, 0.0],
                angular=[0.0, 0.0, 0.05],
                _ts=-1  # Same timestamp
            )

            episode.track("sensors/lidar").append(
                ranges=[1.5, 2.0, 2.5],  # Simplified lidar data
                _ts=-1  # Same timestamp
            )

            # Simulate processing time
            time.sleep(0.01)

        # Verify all tracks have matching timestamps
        pose_data = episode.track("robot/pose").read()
        left_cam_data = episode.track("camera/left/image").read()
        velocity_data = episode.track("robot/velocity").read()

        print(f"✓ Logged {len(pose_data['data'])} synchronized timesteps")

        # Check first timestep - all should have same _ts
        first_ts = pose_data['data'][0]['data']['_ts']
        assert left_cam_data['data'][0]['data']['_ts'] == first_ts
        assert velocity_data['data'][0]['data']['_ts'] == first_ts

        print(f"✓ All tracks synchronized at _ts={first_ts}")


def log_with_explicit_timestamps():
    """Example: Use explicit timestamps for precise control."""

    with Episode(
        name="explicit-ts-demo",
        workspace="robotics",
        local_path=".dreamlake"
    ) as episode:
        # Use explicit timestamp
        ts = 1234567890.123

        episode.track("robot/pose").append(
            position=[1.0, 2.0, 3.0],
            _ts=ts
        )

        # Inherit explicit timestamp
        episode.track("camera/image").append(
            width=640,
            _ts=-1  # Uses ts=1234567890.123
        )

        print(f"✓ Logged with explicit timestamp: {ts}")


def log_multi_field_merging():
    """Example: Merge multiple fields into single timestamped entry."""

    with Episode(
        name="merge-demo",
        workspace="robotics",
        local_path=".dreamlake"
    ) as episode:
        # Log different fields separately but merge into single entry
        ts = time.time()

        episode.track("robot/state").append(position=[1.0, 2.0, 3.0], _ts=ts)
        episode.track("robot/state").append(velocity=[0.1, 0.0, 0.0], _ts=ts)
        episode.track("robot/state").append(acceleration=[0.01, 0.0, 0.0], _ts=ts)

        # Read back - should be single merged entry
        data = episode.track("robot/state").read()

        assert len(data['data']) == 1, "Should merge into single entry"

        merged = data['data'][0]['data']
        assert 'position' in merged
        assert 'velocity' in merged
        assert 'acceleration' in merged

        print(f"✓ Merged 3 appends into single entry at _ts={ts}")


if __name__ == "__main__":
    print("=== Synchronized Multi-Modal Logging ===\n")

    print("1. Synchronized robot data logging:")
    log_robot_data_synchronized()

    print("\n2. Explicit timestamp control:")
    log_with_explicit_timestamps()

    print("\n3. Multi-field merging:")
    log_multi_field_merging()

    print("\n✓ All examples completed successfully!")
