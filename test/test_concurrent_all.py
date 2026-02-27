"""
Comprehensive tests for all concurrency fixes.

Tests concurrent operations for:
- Logging (sequence numbers)
- Parameters (merging)
- Tracks (index allocation)
- Session metadata
"""

import threading
import tempfile
from pathlib import Path

from dreamlake import Session


def test_concurrent_logging():
    """Test that concurrent log operations maintain correct sequence numbers."""
    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = Path(tmpdir) / ".dreamlake"

        with Session(
            prefix="test-workspace/log-test",
            root=str(local_path)
        ) as session:
            num_logs = 100
            errors = []

            def log_message(index):
                """Log a message."""
                try:
                    session.log(f"Log message {index}")
                except Exception as e:
                    errors.append(f"Log {index} failed: {e}")

            # Create and run threads
            threads = [threading.Thread(target=log_message, args=(i,)) for i in range(num_logs)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            # Check for errors
            assert len(errors) == 0, f"Logging errors: {errors}"

            # Verify all logs were written with unique sequence numbers
            logs_file = local_path / "test-workspace" / "log-test" / "logs" / "logs.jsonl"
            assert logs_file.exists(), "Logs file not found"

            import json
            sequence_numbers = []
            with open(logs_file, "r") as f:
                for line in f:
                    if line.strip():
                        log_entry = json.loads(line)
                        sequence_numbers.append(log_entry["sequenceNumber"])

            # Verify we have all logs
            assert len(sequence_numbers) == num_logs, \
                f"Expected {num_logs} logs, found {len(sequence_numbers)}"

            # Verify sequence numbers are unique and consecutive
            assert sorted(sequence_numbers) == list(range(num_logs)), \
                "Sequence numbers are not unique or not consecutive"


def test_concurrent_parameters():
    """Test that concurrent parameter updates don't lose data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = Path(tmpdir) / ".dreamlake"

        with Session(
            prefix="test-workspace/param-test",
            root=str(local_path)
        ) as session:
            num_threads = 20
            params_per_thread = 5
            errors = []

            def set_parameters(thread_id):
                """Set parameters unique to this thread."""
                try:
                    for i in range(params_per_thread):
                        session.parameters().set(**{
                            f"thread_{thread_id}_param_{i}": thread_id * 100 + i
                        })
                except Exception as e:
                    errors.append(f"Thread {thread_id} failed: {e}")

            # Create and run threads
            threads = [threading.Thread(target=set_parameters, args=(i,)) for i in range(num_threads)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            # Check for errors
            assert len(errors) == 0, f"Parameter errors: {errors}"

            # Verify all parameters were saved
            params = session.parameters().get()
            expected_count = num_threads * params_per_thread
            assert len(params) == expected_count, \
                f"Expected {expected_count} parameters, found {len(params)}"

            # Verify all parameter values are correct
            for thread_id in range(num_threads):
                for param_id in range(params_per_thread):
                    key = f"thread_{thread_id}_param_{param_id}"
                    expected_value = thread_id * 100 + param_id
                    assert key in params, f"Parameter {key} not found"
                    assert params[key] == expected_value, \
                        f"Parameter {key} has wrong value: {params[key]} != {expected_value}"


def test_concurrent_track_append():
    """Test that concurrent track appends maintain correct indices."""
    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = Path(tmpdir) / ".dreamlake"

        with Session(
            prefix="test-workspace/track-test",
            root=str(local_path)
        ) as session:
            num_appends = 100
            errors = []
            results = [None] * num_appends

            def append_data(index):
                """Append data to track."""
                try:
                    result = session.track("metrics").append(
                        value=index,
                        step=index
                    )
                    results[index] = result
                except Exception as e:
                    errors.append(f"Append {index} failed: {e}")

            # Create and run threads
            threads = [threading.Thread(target=append_data, args=(i,)) for i in range(num_appends)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            # Check for errors
            assert len(errors) == 0, f"Track append errors: {errors}"

            # Verify all appends succeeded (returns TrackBuilder for chaining)
            assert all(r is not None for r in results), "Some appends failed"

            # Flush and read back to verify indices
            session.track("metrics").flush()
            data = session.track("metrics").read(start_index=0, limit=num_appends)

            # Verify all indices are unique
            indices = [int(point["index"]) for point in data["data"]]
            assert len(set(indices)) == num_appends, "Duplicate track indices detected"
            assert sorted(indices) == list(range(num_appends)), "Indices are not consecutive"

            # Verify track stats
            stats = session.track("metrics").stats()
            assert int(stats["totalDataPoints"]) == num_appends, \
                f"Expected {num_appends} data points, found {stats['totalDataPoints']}"


def test_concurrent_batch_track_append():
    """Test that concurrent batch track appends maintain correct indices."""
    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = Path(tmpdir) / ".dreamlake"

        with Session(
            prefix="test-workspace/batch-track-test",
            root=str(local_path)
        ) as session:
            num_batches = 20
            batch_size = 10
            errors = []
            results = [None] * num_batches

            def append_batch(batch_id):
                """Append batch of data to track."""
                try:
                    data_points = [
                        {"value": batch_id * batch_size + i, "step": batch_id * batch_size + i}
                        for i in range(batch_size)
                    ]
                    result = session.track("batch_metrics").append_batch(data_points)
                    results[batch_id] = result
                except Exception as e:
                    errors.append(f"Batch {batch_id} failed: {e}")

            # Create and run threads
            threads = [threading.Thread(target=append_batch, args=(i,)) for i in range(num_batches)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            # Check for errors
            assert len(errors) == 0, f"Batch append errors: {errors}"

            # Verify all batches succeeded
            assert all(r is not None for r in results), "Some batch appends failed"

            # Collect all index ranges
            all_indices = set()
            for result in results:
                start_idx = int(result["startIndex"])
                end_idx = int(result["endIndex"])
                # Add all indices in this range to the set
                for idx in range(start_idx, end_idx + 1):
                    assert idx not in all_indices, f"Duplicate index {idx} detected"
                    all_indices.add(idx)

            # Verify we have all expected indices
            expected_total = num_batches * batch_size
            assert len(all_indices) == expected_total, \
                f"Expected {expected_total} unique indices, found {len(all_indices)}"
            assert all_indices == set(range(expected_total)), "Index ranges are not consecutive"

            # Verify track stats
            stats = session.track("batch_metrics").stats()
            assert int(stats["totalDataPoints"]) == expected_total, \
                f"Expected {expected_total} data points, found {stats['totalDataPoints']}"


def test_concurrent_session_updates():
    """Test that concurrent session metadata updates don't lose data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = Path(tmpdir) / ".dreamlake"

        # Create initial session
        session1 = Session(
            prefix="test-workspace/session-update-test",
            root=str(local_path),
            readme="Initial description"
        )
        session1.open()
        session1.close()

        # Now try concurrent updates
        num_threads = 10
        errors = []

        def update_session(thread_id):
            """Update session metadata."""
            try:
                session = Session(
            prefix="test-workspace/session-update-test",
                    root=str(local_path),
                    tags=[f"tag_{thread_id}"]
                )
                session.open()
                session.close()
            except Exception as e:
                errors.append(f"Thread {thread_id} failed: {e}")

        # Create and run threads
        threads = [threading.Thread(target=update_session, args=(i,)) for i in range(num_threads)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        # Check for errors
        assert len(errors) == 0, f"Session update errors: {errors}"

        # Verify session file is not corrupted
        import json
        session_file = local_path / "test-workspace" / "session-update-test" / "session.json"
        assert session_file.exists(), "Session file not found"

        with open(session_file, "r") as f:
            session_data = json.load(f)

        # Should have valid JSON structure
        assert "name" in session_data
        assert "workspace" in session_data
        assert "tags" in session_data
        # Tags should be from one of the threads
        assert len(session_data["tags"]) > 0


def test_mixed_concurrent_operations():
    """Test all operations happening concurrently."""
    with tempfile.TemporaryDirectory() as tmpdir:
        local_path = Path(tmpdir) / ".dreamlake"

        with Session(
            prefix="test-workspace/mixed-test",
            root=str(local_path)
        ) as session:
            errors = []

            def do_logging(count):
                for i in range(count):
                    try:
                        session.log(f"Log {i}")
                    except Exception as e:
                        errors.append(f"Log failed: {e}")

            def do_parameters(count):
                for i in range(count):
                    try:
                        session.parameters().set(**{f"param_{i}": i})
                    except Exception as e:
                        errors.append(f"Param failed: {e}")

            def do_tracking(count):
                for i in range(count):
                    try:
                        session.track("mixed_metrics").append(value=i, step=i)
                    except Exception as e:
                        errors.append(f"Track failed: {e}")

            ops_per_type = 20

            # Create threads for different operation types
            threads = []
            threads.append(threading.Thread(target=do_logging, args=(ops_per_type,)))
            threads.append(threading.Thread(target=do_parameters, args=(ops_per_type,)))
            threads.append(threading.Thread(target=do_tracking, args=(ops_per_type,)))

            # Start all threads
            for thread in threads:
                thread.start()

            # Wait for completion
            for thread in threads:
                thread.join()

            # Check for errors
            assert len(errors) == 0, f"Mixed operations errors: {errors}"

            # Verify all operations completed successfully
            # Check logs
            logs_file = local_path / "test-workspace" / "mixed-test" / "logs" / "logs.jsonl"
            import json
            log_count = sum(1 for _ in open(logs_file) if _.strip())
            assert log_count == ops_per_type, f"Expected {ops_per_type} logs, found {log_count}"

            # Check parameters
            params = session.parameters().get()
            assert len(params) == ops_per_type, \
                f"Expected {ops_per_type} params, found {len(params)}"

            # Check track
            stats = session.track("mixed_metrics").stats()
            assert int(stats["totalDataPoints"]) == ops_per_type, \
                f"Expected {ops_per_type} track points, found {stats['totalDataPoints']}"


if __name__ == "__main__":
    test_concurrent_logging()
    test_concurrent_parameters()
    test_concurrent_track_append()
    test_concurrent_batch_track_append()
    test_concurrent_session_updates()
    test_mixed_concurrent_operations()
    print("All comprehensive concurrency tests passed!")
