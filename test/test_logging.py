"""Tests for logging functionality."""
import json
from pathlib import Path


def test_basic_logging(local_session, temp_workspace):
    """Test basic log message."""
    with local_session(name="log-test", workspace="test") as session:
        session.log("Training started")

    # Verify log file exists
    logs_file = temp_workspace / "test" / "log-test" / "logs" / "logs.jsonl"
    assert logs_file.exists()

    # Read and verify log entry
    with open(logs_file) as f:
        log_entry = json.loads(f.readline())
        assert log_entry["message"] == "Training started"
        assert "timestamp" in log_entry
        assert log_entry["level"] == "info"  # Default level


def test_log_levels(local_session, temp_workspace):
    """Test different log levels."""
    with local_session(name="log-levels", workspace="test") as session:
        session.log("Debug info", level="debug")
        session.log("General info", level="info")
        session.log("Warning message", level="warn")
        session.log("Error occurred", level="error")
        session.log("Fatal error", level="fatal")

    # Verify all log levels were recorded
    logs_file = temp_workspace / "test" / "log-levels" / "logs" / "logs.jsonl"
    with open(logs_file) as f:
        logs = [json.loads(line) for line in f]

    assert len(logs) == 5
    assert logs[0]["level"] == "debug"
    assert logs[1]["level"] == "info"
    assert logs[2]["level"] == "warn"
    assert logs[3]["level"] == "error"
    assert logs[4]["level"] == "fatal"


def test_logging_with_metadata(local_session, temp_workspace):
    """Test logging with structured metadata."""
    with local_session(name="log-metadata", workspace="test") as session:
        session.log(
            "Epoch completed",
            level="info",
            metadata={
                "epoch": 5,
                "train_loss": 0.234,
                "val_loss": 0.456,
                "learning_rate": 0.001,
            },
        )

    # Verify metadata was saved
    logs_file = temp_workspace / "test" / "log-metadata" / "logs" / "logs.jsonl"
    with open(logs_file) as f:
        log_entry = json.loads(f.readline())

    assert log_entry["message"] == "Epoch completed"
    assert log_entry["metadata"]["epoch"] == 5
    assert log_entry["metadata"]["train_loss"] == 0.234
    assert log_entry["metadata"]["val_loss"] == 0.456
    assert log_entry["metadata"]["learning_rate"] == 0.001


def test_progress_logging(local_session, temp_workspace):
    """Test logging progress over time."""
    with local_session(name="progress", workspace="test") as session:
        total = 100
        for i in range(0, total + 1, 25):
            session.log(
                f"Progress: {i}%",
                level="info",
                metadata={"processed": i, "total": total, "percent": i},
            )

    # Verify progress logs
    logs_file = temp_workspace / "test" / "progress" / "logs" / "logs.jsonl"
    with open(logs_file) as f:
        logs = [json.loads(line) for line in f]

    assert len(logs) == 5  # 0, 25, 50, 75, 100
    assert logs[0]["metadata"]["percent"] == 0
    assert logs[2]["metadata"]["percent"] == 50
    assert logs[4]["metadata"]["percent"] == 100


def test_error_logging(local_session, temp_workspace):
    """Test error logging with exception information."""
    with local_session(name="error-log", workspace="test") as session:
        try:
            raise ValueError("Simulated error")
        except Exception as e:
            session.log(
                f"Error occurred: {str(e)}",
                level="error",
                metadata={
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                },
            )

    # Verify error was logged
    logs_file = temp_workspace / "test" / "error-log" / "logs" / "logs.jsonl"
    with open(logs_file) as f:
        log_entry = json.loads(f.readline())

    assert log_entry["level"] == "error"
    assert "Simulated error" in log_entry["message"]
    assert log_entry["metadata"]["error_type"] == "ValueError"
    assert log_entry["metadata"]["error_message"] == "Simulated error"


def test_multiple_log_entries(local_session, temp_workspace):
    """Test logging multiple entries in sequence."""
    with local_session(name="multi-log", workspace="test") as session:
        session.log("Step 1: Initialize")
        session.log("Step 2: Load data")
        session.log("Step 3: Train model")
        session.log("Step 4: Evaluate")
        session.log("Step 5: Complete")

    # Verify all logs were saved
    logs_file = temp_workspace / "test" / "multi-log" / "logs" / "logs.jsonl"
    with open(logs_file) as f:
        logs = [json.loads(line) for line in f]

    assert len(logs) == 5
    assert logs[0]["message"] == "Step 1: Initialize"
    assert logs[4]["message"] == "Step 5: Complete"


def test_log_sequence_numbers(local_session, temp_workspace):
    """Test that logs have sequential sequence numbers."""
    with local_session(name="sequence", workspace="test") as session:
        for i in range(5):
            session.log(f"Message {i}")

    # Verify sequence numbers
    logs_file = temp_workspace / "test" / "sequence" / "logs" / "logs.jsonl"
    with open(logs_file) as f:
        logs = [json.loads(line) for line in f]

    for i, log in enumerate(logs):
        assert log["sequenceNumber"] == i
