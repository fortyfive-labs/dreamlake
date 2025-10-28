"""Tests for parameter tracking."""
import json
from pathlib import Path


def test_simple_parameters(local_session, temp_workspace):
    """Test setting simple parameters."""
    with local_session(name="params-simple", workspace="test") as session:
        session.parameters().set(
            learning_rate=0.001,
            batch_size=32,
            epochs=100,
        )

    # Verify parameters were saved
    params_file = temp_workspace / "test" / "params-simple" / "parameters.json"
    assert params_file.exists()

    with open(params_file) as f:
        params_data = json.load(f)

    params = params_data["data"]
    assert params["learning_rate"] == 0.001
    assert params["batch_size"] == 32
    assert params["epochs"] == 100


def test_nested_parameters_flattening(local_session, temp_workspace):
    """Test that nested parameters are automatically flattened."""
    with local_session(name="params-nested", workspace="test") as session:
        session.parameters().set(
            **{
                "model": {
                    "architecture": "resnet50",
                    "pretrained": True,
                    "num_layers": 50,
                },
                "optimizer": {
                    "type": "adam",
                    "beta1": 0.9,
                    "beta2": 0.999,
                },
            }
        )

    # Verify nested parameters are flattened
    params_file = temp_workspace / "test" / "params-nested" / "parameters.json"
    with open(params_file) as f:
        params_data = json.load(f)

    params = params_data["data"]
    assert params["model.architecture"] == "resnet50"
    assert params["model.pretrained"] is True
    assert params["model.num_layers"] == 50
    assert params["optimizer.type"] == "adam"
    assert params["optimizer.beta1"] == 0.9
    assert params["optimizer.beta2"] == 0.999


def test_parameter_updates(local_session, temp_workspace):
    """Test updating existing parameters."""
    with local_session(name="params-update", workspace="test") as session:
        # Set initial parameter
        session.parameters().set(learning_rate=0.01)

        # Update the parameter
        session.parameters().set(learning_rate=0.0001)

        # Add more parameters
        session.parameters().set(
            use_mixed_precision=True,
            gradient_clipping=1.0,
        )

    # Verify updated parameters
    params_file = temp_workspace / "test" / "params-update" / "parameters.json"
    with open(params_file) as f:
        params_data = json.load(f)

    params = params_data["data"]
    assert params["learning_rate"] == 0.0001  # Updated value
    assert params["use_mixed_precision"] is True
    assert params["gradient_clipping"] == 1.0


def test_mixed_simple_and_nested_parameters(local_session, temp_workspace):
    """Test setting both simple and nested parameters."""
    with local_session(name="params-mixed", workspace="test") as session:
        session.parameters().set(
            learning_rate=0.001,
            batch_size=32,
            **{
                "model": {"name": "resnet", "layers": 50},
                "data": {"augmentation": True, "workers": 4},
            },
        )

    # Verify all parameters
    params_file = temp_workspace / "test" / "params-mixed" / "parameters.json"
    with open(params_file) as f:
        params_data = json.load(f)

    params = params_data["data"]
    assert params["learning_rate"] == 0.001
    assert params["batch_size"] == 32
    assert params["model.name"] == "resnet"
    assert params["model.layers"] == 50
    assert params["data.augmentation"] is True
    assert params["data.workers"] == 4


def test_parameter_types(local_session, temp_workspace):
    """Test different parameter value types."""
    with local_session(name="params-types", workspace="test") as session:
        session.parameters().set(
            int_param=42,
            float_param=3.14,
            str_param="hello",
            bool_param=True,
            none_param=None,
            list_param=[1, 2, 3],
            dict_param={"key": "value"},
        )

    # Verify parameter types
    params_file = temp_workspace / "test" / "params-types" / "parameters.json"
    with open(params_file) as f:
        params_data = json.load(f)

    params = params_data["data"]
    assert params["int_param"] == 42
    assert params["float_param"] == 3.14
    assert params["str_param"] == "hello"
    assert params["bool_param"] is True
    assert params["none_param"] is None
    assert params["list_param"] == [1, 2, 3]
    # Dict params are also flattened
    assert params["dict_param.key"] == "value"


def test_deep_nested_parameters(local_session, temp_workspace):
    """Test deeply nested parameter structures."""
    with local_session(name="params-deep", workspace="test") as session:
        session.parameters().set(
            **{
                "config": {
                    "model": {"encoder": {"layers": 12, "heads": 8}, "decoder": {"layers": 6}},
                    "training": {"optimizer": {"name": "adam", "lr": 0.001}},
                }
            }
        )

    # Verify deep nesting is flattened
    params_file = temp_workspace / "test" / "params-deep" / "parameters.json"
    with open(params_file) as f:
        params_data = json.load(f)

    params = params_data["data"]
    assert params["config.model.encoder.layers"] == 12
    assert params["config.model.encoder.heads"] == 8
    assert params["config.model.decoder.layers"] == 6
    assert params["config.training.optimizer.name"] == "adam"
    assert params["config.training.optimizer.lr"] == 0.001


def test_empty_parameters(local_session, temp_workspace):
    """Test session with no parameters set."""
    with local_session(name="params-empty", workspace="test") as session:
        session.log("No parameters set")

    # Verify parameters file exists but is empty or has default structure
    params_file = temp_workspace / "test" / "params-empty" / "parameters.json"
    # File may or may not exist depending on implementation
    # If it exists, it should be empty or have empty dict
    if params_file.exists():
        with open(params_file) as f:
            params_data = json.load(f)
            params = params_data.get("data", {})
            assert params == {} or len(params) == 0
