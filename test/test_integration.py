"""Comprehensive integration tests for complete workflows in both local and remote modes."""
import json
import random
import pytest
from pathlib import Path


class TestCompleteWorkflows:
    """Integration tests for complete ML experiment workflows."""

    def test_complete_ml_workflow_local(self, local_session, temp_workspace, sample_files):
        """Test complete ML experiment workflow in local mode."""
        with local_session(
            name="ml-experiment",
            workspace="experiments",
            description="Complete ML training experiment",
            tags=["ml", "training", "test"],
            folder="/experiments/2024"
        ) as session:
            # 1. Set hyperparameters
            session.parameters().set(
                learning_rate=0.001,
                batch_size=32,
                epochs=10,
                model="resnet50",
                optimizer="adam"
            )

            session.log("Experiment started", level="info")

            # 2. Track training metrics over epochs
            for epoch in range(10):
                train_loss = 1.0 / (epoch + 1) + random.uniform(-0.05, 0.05)
                val_loss = 1.2 / (epoch + 1) + random.uniform(-0.05, 0.05)
                accuracy = min(0.95, 0.5 + epoch * 0.05)

                session.track("train_loss").append(value=train_loss, epoch=epoch, step=epoch * 100)
                session.track("val_loss").append(value=val_loss, epoch=epoch, step=epoch * 100)
                session.track("accuracy").append(value=accuracy, epoch=epoch, step=epoch * 100)

                session.log(
                    f"Epoch {epoch + 1}/10 complete",
                    level="info",
                    metadata={
                        "train_loss": train_loss,
                        "val_loss": val_loss,
                        "accuracy": accuracy
                    }
                )

            # 3. Upload artifacts
            session.file(
                file_path=sample_files["model"],
                prefix="/models/final",
                tags=["final", "best"],
                description="Final trained model"
            ).save()

            session.file(
                file_path=sample_files["config"],
                prefix="/configs",
                tags=["config"],
                description="Training configuration"
            ).save()

            session.log("Experiment completed successfully", level="info")

        # Verify everything was created
        session_dir = temp_workspace / "experiments" / "ml-experiment"
        assert (session_dir / "session.json").exists()
        assert (session_dir / "parameters.json").exists()
        assert (session_dir / "logs" / "logs.jsonl").exists()
        assert (session_dir / "tracks" / "train_loss" / "data.jsonl").exists()
        assert (session_dir / "tracks" / "val_loss" / "data.jsonl").exists()
        assert (session_dir / "tracks" / "accuracy" / "data.jsonl").exists()

    @pytest.mark.remote
    def test_complete_ml_workflow_remote(self, remote_session, sample_files):
        """Test complete ML workflow in remote mode."""
        with remote_session(
            name="ml-experiment-remote",
            workspace="experiments",
            description="Remote ML training experiment",
            tags=["ml", "remote"]
        ) as session:
            session.parameters().set(
                learning_rate=0.001,
                batch_size=64,
                epochs=5,
                model="transformer"
            )

            session.log("Remote experiment started", level="info")

            for epoch in range(5):
                loss = 1.0 / (epoch + 1)
                session.track("loss").append(value=loss, epoch=epoch)
                session.log(f"Epoch {epoch + 1}/5", metadata={"loss": loss})

            session.file(file_path=sample_files["model"], prefix="/models").save()
            session.log("Remote experiment completed", level="info")


class TestHyperparameterSearch:
    """Integration tests for hyperparameter search workflows."""

    def test_grid_search_local(self, local_session, temp_workspace):
        """Test grid search hyperparameter optimization."""
        learning_rates = [0.1, 0.01, 0.001]
        batch_sizes = [16, 32, 64]

        for lr in learning_rates:
            for bs in batch_sizes:
                session_name = f"grid-lr{lr}-bs{bs}".replace(".", "_")

                with local_session(
                    name=session_name,
                    workspace="hyperparam-search",
                    description=f"Grid search: lr={lr}, bs={bs}",
                    tags=["grid-search", f"lr-{lr}", f"bs-{bs}"]
                ) as session:
                    # Track hyperparameters
                    session.parameters().set(
                        learning_rate=lr,
                        batch_size=bs,
                        epochs=10
                    )

                    # Simulate training
                    final_acc = 0.5 + random.random() * 0.4
                    final_loss = 0.5 - random.random() * 0.3

                    session.track("accuracy").append(value=final_acc, epoch=9)
                    session.track("loss").append(value=final_loss, epoch=9)

                    session.log(f"Grid search run complete: acc={final_acc:.4f}")

        # Verify all sessions were created
        workspace_dir = temp_workspace / "hyperparam-search"
        sessions = [d for d in workspace_dir.iterdir() if d.is_dir()]
        assert len(sessions) == 9  # 3 LRs × 3 batch sizes

    @pytest.mark.remote
    def test_random_search_remote(self, remote_session):
        """Test random search in remote mode."""
        for run in range(5):
            lr = random.choice([0.1, 0.01, 0.001, 0.0001])
            bs = random.choice([16, 32, 64])

            with remote_session(
                name=f"random-search-run-{run}",
                workspace="random-search",
                tags=["random-search"]
            ) as session:
                session.parameters().set(learning_rate=lr, batch_size=bs)
                acc = 0.6 + random.random() * 0.3
                session.track("accuracy").append(value=acc, run=run)
                session.log(f"Run {run} complete")


class TestIterativeExperimentation:
    """Integration tests for iterative experimentation."""

    def test_iterative_improvements_local(self, local_session, temp_workspace):
        """Test iterative model improvements."""
        experiments = [
            {
                "name": "baseline",
                "description": "Baseline model",
                "params": {"lr": 0.01, "layers": 3},
                "expected_acc": 0.75
            },
            {
                "name": "deeper",
                "description": "Deeper network",
                "params": {"lr": 0.01, "layers": 5},
                "expected_acc": 0.82
            },
            {
                "name": "lower-lr",
                "description": "Lower learning rate",
                "params": {"lr": 0.001, "layers": 5},
                "expected_acc": 0.85
            },
            {
                "name": "best",
                "description": "Best configuration",
                "params": {"lr": 0.001, "layers": 7},
                "expected_acc": 0.90
            }
        ]

        for exp in experiments:
            with local_session(
                name=f"exp-{exp['name']}",
                workspace="iterative",
                description=exp["description"],
                tags=["iterative", exp["name"]]
            ) as session:
                session.parameters().set(**exp["params"])
                session.track("val_accuracy").append(value=exp["expected_acc"], step=0)
                session.log(f"{exp['name']} experiment complete", level="info")

        # Verify progression
        workspace_dir = temp_workspace / "iterative"
        assert (workspace_dir / "exp-baseline").exists()
        assert (workspace_dir / "exp-deeper").exists()
        assert (workspace_dir / "exp-lower-lr").exists()
        assert (workspace_dir / "exp-best").exists()


class TestMultiSessionPipeline:
    """Integration tests for multi-session pipelines."""

    def test_ml_pipeline_local(self, local_session, temp_workspace, sample_files):
        """Test complete ML pipeline with multiple stages."""
        # Stage 1: Data preprocessing
        with local_session(
            name="01-preprocessing",
            workspace="pipeline",
            tags=["pipeline", "preprocessing"],
            folder="/pipeline/stage-1"
        ) as session:
            session.log("Starting data preprocessing", level="info")
            session.parameters().set(
                data_source="raw_data.csv",
                preprocessing_steps=["normalize", "augment", "split"]
            )
            session.track("samples_processed").append(value=10000, step=0)
            session.file(file_path=sample_files["results"], prefix="/data").save()
            session.log("Preprocessing complete", level="info")

        # Stage 2: Training
        with local_session(
            name="02-training",
            workspace="pipeline",
            tags=["pipeline", "training"],
            folder="/pipeline/stage-2"
        ) as session:
            session.log("Starting model training", level="info")
            session.parameters().set(
                model="resnet50",
                epochs=10,
                batch_size=32
            )
            for i in range(10):
                session.track("loss").append(value=1.0 / (i + 1), epoch=i)
            session.file(file_path=sample_files["model"], prefix="/models").save()
            session.log("Training complete", level="info")

        # Stage 3: Evaluation
        with local_session(
            name="03-evaluation",
            workspace="pipeline",
            tags=["pipeline", "evaluation"],
            folder="/pipeline/stage-3"
        ) as session:
            session.log("Starting model evaluation", level="info")
            session.parameters().set(test_set="test.csv")
            session.track("test_accuracy").append(value=0.95, step=0)
            session.track("test_loss").append(value=0.15, step=0)
            session.log("Evaluation complete", level="info")

        # Verify all stages
        workspace_dir = temp_workspace / "pipeline"
        assert (workspace_dir / "01-preprocessing").exists()
        assert (workspace_dir / "02-training").exists()
        assert (workspace_dir / "03-evaluation").exists()

    @pytest.mark.remote
    def test_pipeline_remote(self, remote_session, sample_files):
        """Test pipeline in remote mode."""
        stages = ["preprocessing", "training", "evaluation"]

        for i, stage in enumerate(stages):
            with remote_session(
                name=f"stage-{i+1}-{stage}",
                workspace="pipeline-remote",
                tags=["pipeline", stage]
            ) as session:
                session.log(f"Starting {stage}")
                session.parameters().set(stage=stage, order=i+1)
                session.track("progress").append(value=(i+1)/len(stages)*100, step=i)
                session.log(f"{stage} complete")


class TestDebuggingWorkflow:
    """Integration tests for debugging workflows."""

    def test_debug_session_local(self, local_session, temp_workspace):
        """Test comprehensive debugging workflow."""
        with local_session(
            name="debug-training",
            workspace="debugging",
            description="Training with debug logging",
            tags=["debug", "verbose"]
        ) as session:
            session.parameters().set(
                learning_rate=0.001,
                batch_size=32,
                debug_mode=True
            )

            session.log("Debug session started", level="debug")
            session.log("Initializing model", level="debug")

            for epoch in range(5):
                session.log(f"Starting epoch {epoch + 1}", level="debug")

                loss = 1.0 / (epoch + 1)

                if epoch == 2:
                    session.log(
                        "Learning rate may be too high",
                        level="warn",
                        metadata={"current_lr": 0.001, "suggested_lr": 0.0001}
                    )

                if random.random() < 0.5:
                    session.log(
                        "Gradient clipping applied",
                        level="warn",
                        metadata={"gradient_norm": 15.5, "max_norm": 10.0}
                    )

                session.track("loss").append(value=loss, epoch=epoch)
                session.log(
                    f"Epoch {epoch + 1} complete",
                    level="info",
                    metadata={"loss": loss}
                )

            session.log("Debug session complete", level="info")

        # Verify comprehensive logs
        logs_file = temp_workspace / "debugging" / "debug-training" / "logs" / "logs.jsonl"
        with open(logs_file) as f:
            logs = [json.loads(line) for line in f]

        assert len(logs) >= 10  # Multiple log levels


class TestAllFeaturesCombined:
    """Integration test using all features together."""

    def test_kitchen_sink_local(self, local_session, temp_workspace, sample_files):
        """Test session using every available feature."""
        with local_session(
            name="kitchen-sink",
            workspace="full-test",
            description="Test of all features combined",
            tags=["test", "comprehensive", "all-features"],
            folder="/tests/comprehensive"
        ) as session:
            # Parameters (simple and nested)
            session.parameters().set(
                learning_rate=0.001,
                batch_size=32,
                epochs=5,
                **{
                    "model": {
                        "architecture": "resnet50",
                        "pretrained": True,
                        "layers": 50
                    },
                    "optimizer": {
                        "type": "adam",
                        "beta1": 0.9,
                        "beta2": 0.999
                    }
                }
            )

            # Logging at multiple levels
            session.log("Starting comprehensive test", level="info")
            session.log("Debug info: all systems go", level="debug")

            # Track multiple metrics
            for i in range(5):
                session.track("train_loss").append(value=0.5 - i * 0.1, epoch=i)
                session.track("val_loss").append(value=0.6 - i * 0.1, epoch=i)
                session.track("accuracy").append(value=0.7 + i * 0.05, epoch=i)
                session.track("learning_rate").append(value=0.001 * (0.9 ** i), epoch=i)

                session.log(f"Epoch {i+1} metrics tracked", level="info")

            # Upload multiple files
            session.file(file_path=sample_files["model"], prefix="/models").save()
            session.file(file_path=sample_files["config"], prefix="/configs").save()
            session.file(file_path=sample_files["results"], prefix="/results").save()

            # Warnings and errors
            session.log("Simulated warning", level="warn")
            session.log("Test error handling", level="error", metadata={"error": "test"})

            session.log("Comprehensive test complete", level="info")

        # Verify everything exists
        session_dir = temp_workspace / "full-test" / "kitchen-sink"
        assert (session_dir / "session.json").exists()
        assert (session_dir / "parameters.json").exists()
        assert (session_dir / "logs" / "logs.jsonl").exists()
        assert (session_dir / "tracks" / "train_loss" / "data.jsonl").exists()
        assert (session_dir / "tracks" / "val_loss" / "data.jsonl").exists()
        assert (session_dir / "tracks" / "accuracy" / "data.jsonl").exists()
        assert (session_dir / "tracks" / "learning_rate" / "data.jsonl").exists()

        # Verify parameters
        with open(session_dir / "parameters.json") as f:
            params = json.load(f)["data"]
            assert params["learning_rate"] == 0.001
            assert params["model.architecture"] == "resnet50"
            assert params["optimizer.type"] == "adam"

    @pytest.mark.remote
    def test_kitchen_sink_remote(self, remote_session, sample_files):
        """Test all features combined in remote mode."""
        with remote_session(
            name="kitchen-sink-remote",
            workspace="full-test-remote",
            description="Remote test of all features",
            tags=["test", "remote", "comprehensive"]
        ) as session:
            # Parameters
            session.parameters().set(
                learning_rate=0.001,
                batch_size=64,
                **{"model": {"type": "transformer", "layers": 12}}
            )

            # Logging
            session.log("Starting remote comprehensive test", level="info")

            # Tracks
            for i in range(3):
                session.track("loss").append(value=0.5 - i * 0.1, epoch=i)
                session.track("accuracy").append(value=0.8 + i * 0.05, epoch=i)

            # Files
            session.file(file_path=sample_files["model"], prefix="/models").save()
            session.file(file_path=sample_files["config"], prefix="/configs").save()

            session.log("Remote comprehensive test complete", level="info")


class TestRealWorldScenarios:
    """Integration tests for real-world scenarios."""

    def test_failed_experiment_recovery_local(self, local_session, temp_workspace):
        """Test recovering from failed experiment."""
        # First attempt (fails)
        try:
            with local_session(name="recovery-test", workspace="recovery") as session:
                session.parameters().set(attempt=1)
                session.log("Starting experiment attempt 1")
                session.track("loss").append(value=0.5, epoch=0)
                raise RuntimeError("Simulated failure")
        except RuntimeError:
            pass

        # Recovery attempt
        with local_session(name="recovery-test", workspace="recovery") as session:
            session.parameters().set(attempt=2, recovered=True)
            session.log("Recovered and restarting")
            session.track("loss").append(value=0.4, epoch=1)
            session.track("loss").append(value=0.3, epoch=2)
            session.log("Recovery successful")

        # Verify both attempts are recorded
        session_dir = temp_workspace / "recovery" / "recovery-test"
        assert session_dir.exists()

    def test_comparison_experiments_local(self, local_session, temp_workspace):
        """Test running comparison experiments."""
        models = ["resnet18", "resnet50", "vit-base"]

        for model_name in models:
            with local_session(
                name=f"comparison-{model_name}",
                workspace="comparisons",
                tags=["comparison", model_name]
            ) as session:
                session.parameters().set(model=model_name, epochs=10)

                # Simulate different performance
                base_acc = {"resnet18": 0.75, "resnet50": 0.85, "vit-base": 0.90}
                final_acc = base_acc[model_name] + random.uniform(-0.02, 0.02)

                session.track("accuracy").append(value=final_acc, epoch=9)
                session.log(f"{model_name} training complete", metadata={"final_acc": final_acc})

        # Verify all comparison runs
        workspace_dir = temp_workspace / "comparisons"
        assert len([d for d in workspace_dir.iterdir() if d.is_dir()]) == 3

    @pytest.mark.slow
    def test_long_running_experiment_local(self, local_session):
        """Test long-running experiment with many data points."""
        with local_session(name="long-run", workspace="longtest") as session:
            session.parameters().set(total_steps=1000)

            # Track many data points
            for step in range(100):
                session.track("loss").append(value=1.0 / (step + 1), step=step)

                if step % 10 == 0:
                    session.log(f"Progress: {step}/100 steps", metadata={"step": step})

            session.log("Long-running experiment complete")
