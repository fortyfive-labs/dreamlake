"""Logging example - Structured logging with different levels."""
import sys
sys.path.insert(0, '../../src')

from dreamlake import Session
import time

def main():
    print("=" * 60)
    print("Logging Example")
    print("=" * 60)

    with Session(
        name="logging-demo",
        workspace="tutorials",
        local_path="./tutorial_data"
    ) as session:
        # Different log levels //
        session.log("Debug information", level="debug")
        session.log("Training started", level="info")
        session.log("GPU memory usage high", level="warn")
        session.log("Failed to load checkpoint", level="error")

        print("\n1. Testing different log levels...")

        # Log with metadata
        session.log(
            "Epoch completed",
            level="info",
            metadata={
                "epoch": 5,
                "train_loss": 0.234,
                "val_loss": 0.456,
                "learning_rate": 0.001
            }
        )

        print("2. Logging with structured metadata...")

        # Simulate progress logging
        total = 100
        for i in range(0, total + 1, 10):
            percent = i
            session.log(
                f"Progress: {percent}%",
                level="info",
                metadata={
                    "processed": i,
                    "total": total,
                    "percent": percent
                }
            )
            time.sleep(0.1)

        print("3. Progress logging complete...")

        # Error logging
        try:
            raise ValueError("Simulated error")
        except Exception as e:
            session.log(
                f"Error occurred: {str(e)}",
                level="error",
                metadata={
                    "error_type": type(e).__name__,
                    "error_message": str(e)
                }
            )

        print("4. Error logging complete...")

        session.log("Logging demo complete!", level="info")

    print("\n✓ All logs saved!")
    print("\n" + "=" * 60)
    print("View logs:")
    print("  cat tutorial_data/.dreamlake/tutorials/logging-demo/logs.jsonl")
    print("=" * 60)

if __name__ == "__main__":
    main()
