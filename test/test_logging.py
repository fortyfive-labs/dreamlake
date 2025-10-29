"""Comprehensive tests for logging functionality in both local and remote modes."""
import json
import pytest
from pathlib import Path


class TestBasicLogging:
    """Tests for basic logging operations."""

    def test_simple_log_local(self, local_session, temp_workspace):
        """Test basic log message in local mode."""
        with local_session(name="log-test", workspace="test") as session:
            session.log("Test message")

        logs_file = temp_workspace / "test" / "log-test" / "logs" / "logs.jsonl"
        assert logs_file.exists()

        with open(logs_file) as f:
            log_entry = json.loads(f.readline())
            assert log_entry["message"] == "Test message"
            assert "timestamp" in log_entry
            assert log_entry["level"] == "info"  # Default level

    @pytest.mark.remote
    def test_simple_log_remote(self, remote_session):
        """Test basic log message in remote mode."""
        with remote_session(name="log-test-remote", workspace="test") as session:
            session.log("Test message from remote")
            # Remote mode sends to server

    def test_multiple_logs_local(self, local_session, temp_workspace):
        """Test logging multiple messages."""
        with local_session(name="multi-log", workspace="test") as session:
            session.log("Message 1")
            session.log("Message 2")
            session.log("Message 3")
            session.log("Message 4")
            session.log("Message 5")

        logs_file = temp_workspace / "test" / "multi-log" / "logs" / "logs.jsonl"
        with open(logs_file) as f:
            logs = [json.loads(line) for line in f]

        assert len(logs) == 5
        assert logs[0]["message"] == "Message 1"
        assert logs[4]["message"] == "Message 5"

    @pytest.mark.remote
    def test_multiple_logs_remote(self, remote_session):
        """Test logging multiple messages in remote mode."""
        with remote_session(name="multi-log-remote", workspace="test") as session:
            for i in range(10):
                session.log(f"Remote message {i}")


class TestLogLevels:
    """Tests for different log levels."""

    def test_all_log_levels_local(self, local_session, temp_workspace):
        """Test all available log levels."""
        with local_session(name="log-levels", workspace="test") as session:
            session.log("Debug message", level="debug")
            session.log("Info message", level="info")
            session.log("Warning message", level="warn")
            session.log("Error message", level="error")
            session.log("Fatal message", level="fatal")

        logs_file = temp_workspace / "test" / "log-levels" / "logs" / "logs.jsonl"
        with open(logs_file) as f:
            logs = [json.loads(line) for line in f]

        assert len(logs) == 5
        assert logs[0]["level"] == "debug"
        assert logs[1]["level"] == "info"
        assert logs[2]["level"] == "warn"
        assert logs[3]["level"] == "error"
        assert logs[4]["level"] == "fatal"

    @pytest.mark.remote
    def test_all_log_levels_remote(self, remote_session):
        """Test all log levels in remote mode."""
        with remote_session(name="log-levels-remote", workspace="test") as session:
            session.log("Debug message", level="debug")
            session.log("Info message", level="info")
            session.log("Warning message", level="warn")
            session.log("Error message", level="error")
            session.log("Fatal message", level="fatal")

    def test_default_log_level_local(self, local_session, temp_workspace):
        """Test that default log level is 'info'."""
        with local_session(name="default-level", workspace="test") as session:
            session.log("Default level message")

        logs_file = temp_workspace / "test" / "default-level" / "logs" / "logs.jsonl"
        with open(logs_file) as f:
            log_entry = json.loads(f.readline())
            assert log_entry["level"] == "info"

    def test_debug_level_local(self, local_session, temp_workspace):
        """Test debug level logging."""
        with local_session(name="debug-test", workspace="test") as session:
            session.log("Debug info: Variable x = 42", level="debug")

        logs_file = temp_workspace / "test" / "debug-test" / "logs" / "logs.jsonl"
        with open(logs_file) as f:
            log_entry = json.loads(f.readline())
            assert log_entry["level"] == "debug"
            assert "Variable x = 42" in log_entry["message"]

    def test_error_level_local(self, local_session, temp_workspace):
        """Test error level logging."""
        with local_session(name="error-test", workspace="test") as session:
            session.log("An error occurred during processing", level="error")

        logs_file = temp_workspace / "test" / "error-test" / "logs" / "logs.jsonl"
        with open(logs_file) as f:
            log_entry = json.loads(f.readline())
            assert log_entry["level"] == "error"


class TestLogMetadata:
    """Tests for logging with metadata."""

    def test_log_with_simple_metadata_local(self, local_session, temp_workspace):
        """Test logging with simple metadata."""
        with local_session(name="meta-log", workspace="test") as session:
            session.log(
                "Training epoch complete",
                level="info",
                metadata={"epoch": 5, "loss": 0.234, "accuracy": 0.89}
            )

        logs_file = temp_workspace / "test" / "meta-log" / "logs" / "logs.jsonl"
        with open(logs_file) as f:
            log_entry = json.loads(f.readline())

        assert log_entry["message"] == "Training epoch complete"
        assert log_entry["metadata"]["epoch"] == 5
        assert log_entry["metadata"]["loss"] == 0.234
        assert log_entry["metadata"]["accuracy"] == 0.89

    @pytest.mark.remote
    def test_log_with_metadata_remote(self, remote_session):
        """Test logging with metadata in remote mode."""
        with remote_session(name="meta-log-remote", workspace="test") as session:
            session.log(
                "Remote training epoch complete",
                level="info",
                metadata={"epoch": 10, "loss": 0.15, "accuracy": 0.95}
            )

    def test_log_with_nested_metadata_local(self, local_session, temp_workspace):
        """Test logging with nested metadata structures."""
        with local_session(name="nested-meta", workspace="test") as session:
            session.log(
                "Complex operation complete",
                level="info",
                metadata={
                    "model": {
                        "name": "resnet50",
                        "layers": 50
                    },
                    "performance": {
                        "train_acc": 0.95,
                        "val_acc": 0.92
                    }
                }
            )

        logs_file = temp_workspace / "test" / "nested-meta" / "logs" / "logs.jsonl"
        with open(logs_file) as f:
            log_entry = json.loads(f.readline())

        assert log_entry["metadata"]["model"]["name"] == "resnet50"
        assert log_entry["metadata"]["performance"]["val_acc"] == 0.92

    def test_log_with_various_types_local(self, local_session, temp_workspace):
        """Test logging metadata with various data types."""
        with local_session(name="types-meta", workspace="test") as session:
            session.log(
                "Various types test",
                level="info",
                metadata={
                    "int_value": 42,
                    "float_value": 3.14159,
                    "string_value": "hello",
                    "bool_value": True,
                    "none_value": None,
                    "list_value": [1, 2, 3],
                    "dict_value": {"key": "value"}
                }
            )

        logs_file = temp_workspace / "test" / "types-meta" / "logs" / "logs.jsonl"
        with open(logs_file) as f:
            log_entry = json.loads(f.readline())

        meta = log_entry["metadata"]
        assert meta["int_value"] == 42
        assert meta["float_value"] == 3.14159
        assert meta["string_value"] == "hello"
        assert meta["bool_value"] is True
        assert meta["none_value"] is None
        assert meta["list_value"] == [1, 2, 3]
        assert meta["dict_value"]["key"] == "value"


class TestLogSequencing:
    """Tests for log sequencing and ordering."""

    def test_log_sequence_numbers_local(self, local_session, temp_workspace):
        """Test that logs have sequential sequence numbers."""
        with local_session(name="sequence", workspace="test") as session:
            for i in range(10):
                session.log(f"Message {i}")

        logs_file = temp_workspace / "test" / "sequence" / "logs" / "logs.jsonl"
        with open(logs_file) as f:
            logs = [json.loads(line) for line in f]

        assert len(logs) == 10
        for i, log in enumerate(logs):
            assert log["sequenceNumber"] == i

    @pytest.mark.remote
    def test_log_sequence_numbers_remote(self, remote_session):
        """Test log sequencing in remote mode."""
        with remote_session(name="sequence-remote", workspace="test") as session:
            for i in range(20):
                session.log(f"Remote message {i}")

    def test_log_timestamps_local(self, local_session, temp_workspace):
        """Test that all logs have timestamps."""
        with local_session(name="timestamps", workspace="test") as session:
            for i in range(5):
                session.log(f"Message {i}")

        logs_file = temp_workspace / "test" / "timestamps" / "logs" / "logs.jsonl"
        with open(logs_file) as f:
            logs = [json.loads(line) for line in f]

        for log in logs:
            assert "timestamp" in log
            assert log["timestamp"] is not None


class TestProgressLogging:
    """Tests for progress tracking via logs."""

    def test_training_progress_local(self, local_session, temp_workspace):
        """Test logging training progress."""
        with local_session(name="progress", workspace="test") as session:
            total_epochs = 10
            for epoch in range(total_epochs):
                session.log(
                    f"Epoch {epoch + 1}/{total_epochs}",
                    level="info",
                    metadata={
                        "epoch": epoch,
                        "progress": (epoch + 1) / total_epochs * 100
                    }
                )

        logs_file = temp_workspace / "test" / "progress" / "logs" / "logs.jsonl"
        with open(logs_file) as f:
            logs = [json.loads(line) for line in f]

        assert len(logs) == 10
        assert logs[0]["metadata"]["progress"] == 10.0
        assert logs[9]["metadata"]["progress"] == 100.0

    @pytest.mark.remote
    def test_training_progress_remote(self, remote_session):
        """Test logging training progress in remote mode."""
        with remote_session(name="progress-remote", workspace="test") as session:
            for epoch in range(5):
                session.log(
                    f"Remote epoch {epoch + 1}/5",
                    metadata={"epoch": epoch, "progress": (epoch + 1) * 20}
                )

    def test_batch_progress_local(self, local_session, temp_workspace):
        """Test logging batch-level progress."""
        with local_session(name="batch-progress", workspace="test") as session:
            total_batches = 100
            for batch in range(0, total_batches + 1, 20):
                session.log(
                    f"Processed {batch}/{total_batches} batches",
                    metadata={"batch": batch, "total": total_batches}
                )

        logs_file = temp_workspace / "test" / "batch-progress" / "logs" / "logs.jsonl"
        with open(logs_file) as f:
            logs = [json.loads(line) for line in f]

        assert len(logs) == 6  # 0, 20, 40, 60, 80, 100


class TestErrorLogging:
    """Tests for error logging scenarios."""

    def test_exception_logging_local(self, local_session, temp_workspace):
        """Test logging exceptions with details."""
        with local_session(name="exception-log", workspace="test") as session:
            try:
                raise ValueError("Simulated error for testing")
            except Exception as e:
                session.log(
                    f"Exception occurred: {str(e)}",
                    level="error",
                    metadata={
                        "error_type": type(e).__name__,
                        "error_message": str(e)
                    }
                )

        logs_file = temp_workspace / "test" / "exception-log" / "logs" / "logs.jsonl"
        with open(logs_file) as f:
            log_entry = json.loads(f.readline())

        assert log_entry["level"] == "error"
        assert "Simulated error" in log_entry["message"]
        assert log_entry["metadata"]["error_type"] == "ValueError"

    @pytest.mark.remote
    def test_exception_logging_remote(self, remote_session):
        """Test logging exceptions in remote mode."""
        with remote_session(name="exception-log-remote", workspace="test") as session:
            try:
                raise RuntimeError("Remote error for testing")
            except Exception as e:
                session.log(
                    f"Remote exception: {str(e)}",
                    level="error",
                    metadata={"error_type": type(e).__name__}
                )

    def test_multiple_errors_local(self, local_session, temp_workspace):
        """Test logging multiple errors."""
        with local_session(name="multi-error", workspace="test") as session:
            errors = [
                ValueError("Error 1"),
                RuntimeError("Error 2"),
                KeyError("Error 3")
            ]

            for i, error in enumerate(errors):
                session.log(
                    f"Error {i + 1}: {str(error)}",
                    level="error",
                    metadata={"error_number": i + 1, "error_type": type(error).__name__}
                )

        logs_file = temp_workspace / "test" / "multi-error" / "logs" / "logs.jsonl"
        with open(logs_file) as f:
            logs = [json.loads(line) for line in f]

        assert len(logs) == 3
        assert all(log["level"] == "error" for log in logs)

    def test_warning_logging_local(self, local_session, temp_workspace):
        """Test warning level logging."""
        with local_session(name="warning-log", workspace="test") as session:
            session.log("Learning rate might be too high", level="warn")
            session.log("Gradient clipping applied", level="warn")
            session.log("Memory usage above threshold", level="warn")

        logs_file = temp_workspace / "test" / "warning-log" / "logs" / "logs.jsonl"
        with open(logs_file) as f:
            logs = [json.loads(line) for line in f]

        assert len(logs) == 3
        assert all(log["level"] == "warn" for log in logs)


class TestLogEdgeCases:
    """Tests for edge cases in logging."""

    def test_empty_log_message_local(self, local_session, temp_workspace):
        """Test logging empty message."""
        with local_session(name="empty-log", workspace="test") as session:
            session.log("")

        logs_file = temp_workspace / "test" / "empty-log" / "logs" / "logs.jsonl"
        with open(logs_file) as f:
            log_entry = json.loads(f.readline())
            assert log_entry["message"] == ""

    def test_very_long_log_message_local(self, local_session, temp_workspace):
        """Test logging very long message."""
        long_message = "A" * 10000
        with local_session(name="long-log", workspace="test") as session:
            session.log(long_message)

        logs_file = temp_workspace / "test" / "long-log" / "logs" / "logs.jsonl"
        with open(logs_file) as f:
            log_entry = json.loads(f.readline())
            assert len(log_entry["message"]) == 10000

    def test_log_with_special_characters_local(self, local_session, temp_workspace):
        """Test logging messages with special characters."""
        special_message = "Special chars: \n\t\r\\ \"quotes\" 'apostrophes' 日本語 emoji 🚀"
        with local_session(name="special-log", workspace="test") as session:
            session.log(special_message)

        logs_file = temp_workspace / "test" / "special-log" / "logs" / "logs.jsonl"
        with open(logs_file) as f:
            log_entry = json.loads(f.readline())
            assert "emoji 🚀" in log_entry["message"]

    def test_log_with_empty_metadata_local(self, local_session, temp_workspace):
        """Test logging with empty metadata dict."""
        with local_session(name="empty-meta", workspace="test") as session:
            session.log("Message with empty metadata", metadata={})

        logs_file = temp_workspace / "test" / "empty-meta" / "logs" / "logs.jsonl"
        with open(logs_file) as f:
            log_entry = json.loads(f.readline())
            # Empty metadata may or may not be included depending on implementation
            if "metadata" in log_entry:
                assert log_entry["metadata"] == {}
            assert log_entry["message"] == "Message with empty metadata"

    def test_log_with_large_metadata_local(self, local_session, temp_workspace):
        """Test logging with large metadata object."""
        large_metadata = {f"key_{i}": f"value_{i}" for i in range(100)}

        with local_session(name="large-meta", workspace="test") as session:
            session.log("Message with large metadata", metadata=large_metadata)

        logs_file = temp_workspace / "test" / "large-meta" / "logs" / "logs.jsonl"
        with open(logs_file) as f:
            log_entry = json.loads(f.readline())
            assert len(log_entry["metadata"]) == 100

    def test_rapid_logging_local(self, local_session, temp_workspace):
        """Test rapid sequential logging."""
        with local_session(name="rapid-log", workspace="test") as session:
            for i in range(1000):
                session.log(f"Rapid message {i}")

        logs_file = temp_workspace / "test" / "rapid-log" / "logs" / "logs.jsonl"
        with open(logs_file) as f:
            logs = [json.loads(line) for line in f]

        assert len(logs) == 1000

    @pytest.mark.remote
    def test_rapid_logging_remote(self, remote_session):
        """Test rapid logging in remote mode."""
        with remote_session(name="rapid-log-remote", workspace="test") as session:
            for i in range(100):
                session.log(f"Rapid remote message {i}")
