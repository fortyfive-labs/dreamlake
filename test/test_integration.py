"""Integration tests for complete workflows."""
import random
from pathlib import Path


def test_complete_ml_workflow(local_session, temp_workspace, sample_files):
    """Test complete ML experiment workflow."""
    with local_session(
        name="ml-experiment",
        workspace="experiments",
        description="Complete ML experiment",
        tags=["test", "ml"],
    ) as session:
        # 1. Track parameters
        session.parameters().set(
            learning_rate=0.001,
            batch_size=32,
            epochs=10,
            model="simple_nn",
        )

        # 2. Log start
        session.log("Experiment started", level="info")

        # 3. Track metrics over epochs
        for epoch in range(10):
            train_loss = 1.0 / (epoch + 1) + random.uniform(-0.05, 0.05)
            val_loss = 1.2 / (epoch + 1) + random.uniform(-0.05, 0.05)
            accuracy = min(0.95, 0.5 + epoch * 0.05)

            session.track("train_loss").append(value=train_loss, epoch=epoch)
            session.track("val_loss").append(value=val_loss, epoch=epoch)
            session.track("accuracy").append(value=accuracy, epoch=epoch)

            session.log(
                f"Epoch {epoch + 1}/10",
                level="info",
                metadata={"train_loss": train_loss, "val_loss": val_loss},
            )

        # 4. Upload model file
        session.file(
            file_path=sample_files["model"],
            prefix="/models",
            tags=["final"],
            description="Final model weights",
        ).save()

        # 5. Log completion
        session.log("Experiment completed", level="info")

    # Verify all data was saved
    session_dir = temp_workspace / "experiments" / "ml-experiment"
    assert session_dir.exists()
    assert (session_dir / "session.json").exists()
    assert (session_dir / "logs" / "logs.jsonl").exists()
    assert (session_dir / "parameters.json").exists()
    assert (session_dir / "tracks" / "train_loss" / "data.jsonl").exists()
    assert (session_dir / "tracks" / "val_loss" / "data.jsonl").exists()
    assert (session_dir / "tracks" / "accuracy" / "data.jsonl").exists()


def test_hyperparameter_search(local_session, temp_workspace):
    """Test hyperparameter search workflow."""
    learning_rates = [0.1, 0.01, 0.001]
    batch_sizes = [16, 32]

    results = []

    for lr in learning_rates:
        for bs in batch_sizes:
            session_name = f"search-lr{lr}-bs{bs}"

            with local_session(
                name=session_name,
                workspace="hp-search",
                description=f"Grid search: lr={lr}, batch_size={bs}",
                tags=["grid-search", f"lr-{lr}", f"bs-{bs}"],
            ) as session:
                # Track hyperparameters
                session.parameters().set(
                    learning_rate=lr,
                    batch_size=bs,
                    optimizer="sgd",
                )

                session.log(f"Starting training with lr={lr}, bs={bs}")

                # Simulate training
                final_accuracy = 0.5 + random.random() * 0.4
                session.track("accuracy").append(value=final_accuracy, epoch=0)

                session.log(f"Final accuracy: {final_accuracy:.4f}")

                results.append({"lr": lr, "bs": bs, "accuracy": final_accuracy})

    # Verify all sessions were created
    workspace_dir = temp_workspace / "hp-search"
    assert workspace_dir.exists()

    # Should have 6 sessions (3 LRs × 2 batch sizes)
    sessions = [d for d in workspace_dir.iterdir() if d.is_dir()]
    assert len(sessions) == 6


def test_multiple_sessions_workflow(local_session, temp_workspace):
    """Test workflow with multiple related sessions."""
    # Session 1: Data preprocessing
    with local_session(
        name="preprocessing",
        workspace="pipeline",
        tags=["pipeline", "preprocessing"],
    ) as session:
        session.log("Preprocessing data", level="info")
        session.parameters().set(
            data_source="data.csv",
            preprocessing_steps=["normalize", "augment"],
        )

    # Session 2: Training
    with local_session(
        name="training",
        workspace="pipeline",
        tags=["pipeline", "training"],
    ) as session:
        session.log("Training model", level="info")
        session.parameters().set(model="resnet50", epochs=10)
        for i in range(10):
            session.track("loss").append(value=1.0 / (i + 1), step=i)

    # Session 3: Evaluation
    with local_session(
        name="evaluation",
        workspace="pipeline",
        tags=["pipeline", "evaluation"],
    ) as session:
        session.log("Evaluating model", level="info")
        session.parameters().set(test_set="test.csv")
        session.track("test_accuracy").append(value=0.95, step=0)

    # Verify all sessions exist
    workspace_dir = temp_workspace / "pipeline"
    assert (workspace_dir / "preprocessing").exists()
    assert (workspace_dir / "training").exists()
    assert (workspace_dir / "evaluation").exists()


def test_iterative_experimentation(local_session, temp_workspace):
    """Test iterative experimentation with parameter updates."""
    experiments = [
        {"name": "baseline", "lr": 0.01, "bs": 32, "expected_acc": 0.75},
        {"name": "lr-001", "lr": 0.001, "bs": 32, "expected_acc": 0.82},
        {"name": "bs-64", "lr": 0.001, "bs": 64, "expected_acc": 0.85},
    ]

    for exp in experiments:
        with local_session(
            name=f"experiment-{exp['name']}",
            workspace="iterations",
            tags=["experiment", exp["name"]],
        ) as session:
            session.parameters().set(
                learning_rate=exp["lr"],
                batch_size=exp["bs"],
            )
            session.track("val_accuracy").append(value=exp["expected_acc"], step=0)
            session.log(f"{exp['name']} experiment complete", level="info")

    # Verify all experiments exist
    workspace_dir = temp_workspace / "iterations"
    assert (workspace_dir / "experiment-baseline").exists()
    assert (workspace_dir / "experiment-lr-001").exists()
    assert (workspace_dir / "experiment-bs-64").exists()


def test_debugging_workflow(local_session, temp_workspace):
    """Test debugging workflow with comprehensive logging."""
    with local_session(
        name="debug-training",
        workspace="debugging",
        description="Training with debug logging",
        tags=["debug"],
    ) as session:
        session.parameters().set(
            learning_rate=0.001,
            batch_size=32,
            model="debug_net",
        )

        session.log("Training session started", level="info")
        session.log("Initializing model", level="debug")

        for epoch in range(5):
            session.log(f"Starting epoch {epoch + 1}", level="debug")

            loss = 1.0 / (epoch + 1)

            if epoch == 2:
                session.log(
                    "Learning rate may be too high",
                    level="warn",
                    metadata={"current_lr": 0.001, "suggested_lr": 0.0001},
                )

            if random.random() < 0.3:
                session.log(
                    "Gradient clipping applied",
                    level="warn",
                    metadata={"gradient_norm": 15.5, "max_norm": 10.0},
                )

            session.track("loss").append(value=loss, epoch=epoch)

            session.log(
                f"Epoch {epoch + 1} complete",
                level="info",
                metadata={"loss": loss},
            )

        session.log("Training complete", level="info")

    # Verify session has comprehensive logs
    session_dir = temp_workspace / "debugging" / "debug-training"
    logs_file = session_dir / "logs" / "logs.jsonl"
    assert logs_file.exists()

    # Count log entries
    with open(logs_file) as f:
        log_count = sum(1 for _ in f)

    assert log_count >= 5  # At least one log per epoch


def test_session_with_all_features(local_session, temp_workspace, sample_files):
    """Test session using all features together."""
    with local_session(
        name="full-featured",
        workspace="complete",
        description="Session with all features",
        tags=["complete", "test"],
        folder="/experiments/full",
    ) as session:
        # Parameters
        session.parameters().set(
            learning_rate=0.001,
            batch_size=32,
            **{"model": {"name": "resnet", "layers": 50}},
        )

        # Logging
        session.log("Starting comprehensive test", level="info")

        # Tracks
        for i in range(5):
            session.track("metric1").append(value=i * 0.1, step=i)
            session.track("metric2").append(value=i * 0.2, step=i)

        # Files
        session.file(file_path=sample_files["model"], prefix="/models").save()
        session.file(file_path=sample_files["config"], prefix="/config").save()

        # More logging
        session.log("All features tested", level="info")

    # Verify everything exists
    session_dir = temp_workspace / "complete" / "full-featured"
    assert (session_dir / "session.json").exists()
    assert (session_dir / "parameters.json").exists()
    assert (session_dir / "logs" / "logs.jsonl").exists()
    assert (session_dir / "tracks" / "metric1" / "data.jsonl").exists()
    assert (session_dir / "tracks" / "metric2" / "data.jsonl").exists()
    assert (session_dir / "files" / ".files_metadata.json").exists()
