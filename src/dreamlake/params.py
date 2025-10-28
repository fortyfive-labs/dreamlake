"""
Parameters module for Dreamlake SDK.

Provides fluent API for parameter management with automatic dict flattening.
Nested dicts are flattened to dot-notation: {"model": {"lr": 0.001}} → {"model.lr": 0.001}
"""

from typing import Dict, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .session import Session


class ParametersBuilder:
    """
    Fluent interface for parameter operations.

    Usage:
        session.parameters().set(model={"lr": 0.001}, optimizer="adam")
        params = session.parameters().get()
        params_nested = session.parameters().get(flatten=False)
    """

    def __init__(self, session: 'Session'):
        """
        Initialize parameters builder.

        Args:
            session: Parent session instance
        """
        self._session = session

    def set(self, **kwargs) -> 'ParametersBuilder':
        """
        Set/merge parameters. Always merges with existing parameters (upsert behavior).

        Nested dicts are automatically flattened:
            set(model={"lr": 0.001, "batch_size": 32})
            → {"model.lr": 0.001, "model.batch_size": 32}

        Args:
            **kwargs: Parameters to set (can be nested dicts)

        Returns:
            Self for potential chaining

        Raises:
            RuntimeError: If session is not open
            RuntimeError: If session is write-protected

        Examples:
            # Set nested parameters
            session.parameters().set(
                model={"lr": 0.001, "batch_size": 32},
                optimizer="adam"
            )

            # Merge/update specific parameters
            session.parameters().set(model={"lr": 0.0001})  # Only updates model.lr

            # Set flat parameters with dot notation
            session.parameters().set(**{"model.lr": 0.001, "model.batch_size": 32})
        """
        if not self._session._is_open:
            raise RuntimeError("Session not open. Use session.open() or context manager.")

        if self._session.write_protected:
            raise RuntimeError("Session is write-protected and cannot be modified.")

        # Flatten the kwargs
        flattened = self.flatten_dict(kwargs)

        if not flattened:
            # No parameters to set, just return
            return self

        # Write parameters through session
        self._session._write_params(flattened)

        return self

    def get(self, flatten: bool = True) -> Dict[str, Any]:
        """
        Get parameters from the session.

        Args:
            flatten: If True, returns flattened dict with dot notation.
                    If False, returns nested dict structure.

        Returns:
            Parameters dict (flattened or nested based on flatten arg)

        Raises:
            RuntimeError: If session is not open

        Examples:
            # Get flattened parameters
            params = session.parameters().get()
            # → {"model.lr": 0.001, "model.batch_size": 32, "optimizer": "adam"}

            # Get nested parameters
            params = session.parameters().get(flatten=False)
            # → {"model": {"lr": 0.001, "batch_size": 32}, "optimizer": "adam"}
        """
        if not self._session._is_open:
            raise RuntimeError("Session not open. Use session.open() or context manager.")

        # Read parameters through session
        params = self._session._read_params()

        if params is None:
            return {}

        # Return as-is if flatten=True (stored flattened), or unflatten if needed
        if flatten:
            return params
        else:
            return self.unflatten_dict(params)

    @staticmethod
    def flatten_dict(d: Dict[str, Any], parent_key: str = '', sep: str = '.') -> Dict[str, Any]:
        """
        Flatten a nested dictionary into dot-notation keys.

        Args:
            d: Dictionary to flatten (can contain nested dicts)
            parent_key: Prefix for keys (used in recursion)
            sep: Separator character (default: '.')

        Returns:
            Flattened dictionary with dot-notation keys

        Examples:
            >>> flatten_dict({"a": {"b": 1, "c": 2}, "d": 3})
            {"a.b": 1, "a.c": 2, "d": 3}

            >>> flatten_dict({"model": {"lr": 0.001, "layers": {"hidden": 128}}})
            {"model.lr": 0.001, "model.layers.hidden": 128}
        """
        items = []

        for k, v in d.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else k

            if isinstance(v, dict):
                # Recursively flatten nested dicts
                items.extend(ParametersBuilder.flatten_dict(v, new_key, sep=sep).items())
            else:
                # Keep non-dict values as-is
                items.append((new_key, v))

        return dict(items)

    @staticmethod
    def unflatten_dict(d: Dict[str, Any], sep: str = '.') -> Dict[str, Any]:
        """
        Unflatten a dot-notation dictionary into nested structure.

        Args:
            d: Flattened dictionary with dot-notation keys
            sep: Separator character (default: '.')

        Returns:
            Nested dictionary structure

        Examples:
            >>> unflatten_dict({"a.b": 1, "a.c": 2, "d": 3})
            {"a": {"b": 1, "c": 2}, "d": 3}

            >>> unflatten_dict({"model.lr": 0.001, "model.layers.hidden": 128})
            {"model": {"lr": 0.001, "layers": {"hidden": 128}}}
        """
        result = {}

        for key, value in d.items():
            parts = key.split(sep)
            current = result

            # Navigate/create nested structure
            for part in parts[:-1]:
                if part not in current:
                    current[part] = {}
                current = current[part]

            # Set the final value
            current[parts[-1]] = value

        return result
