"""Rule loader for fraud detection rules configuration."""

from __future__ import annotations

import yaml
from pydantic import ValidationError

from pipelines.scoring.rules.models import RuleDefinition


class RuleConfigError(RuntimeError):
    """Raised when the rules YAML is missing, malformed, or invalid."""

    pass


class RuleLoader:
    """Loader for fraud detection rules from YAML configuration files."""

    @staticmethod
    def load(path: str) -> list[RuleDefinition]:
        """Load and validate rules from a YAML file.

        Returns only rules where enabled=True.

        Args:
            path: Path to the YAML rules configuration file.

        Returns:
            List of enabled RuleDefinition instances.

        Raises:
            RuleConfigError: If the file does not exist, YAML is malformed,
                the top-level is not a list, or any rule entry fails
                pydantic validation.

        Examples:
            >>> rules = RuleLoader.load("rules.yaml")
            >>> len(rules)  # Only enabled rules
            3
        """
        # Read file
        try:
            with open(path) as f:
                content = f.read()
        except FileNotFoundError as exc:
            raise RuleConfigError(f"Rules file not found: {path}") from exc

        # Parse YAML
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise RuleConfigError(f"Malformed YAML in {path}: {exc}") from exc

        # Validate top-level is a list
        if not isinstance(data, list):
            raise RuleConfigError("Rules config must be a YAML list")

        # Validate each rule and collect enabled ones
        all_rules: list[RuleDefinition] = []
        for i, item in enumerate(data):
            try:
                rule = RuleDefinition.model_validate(item)
                all_rules.append(rule)
            except ValidationError as exc:
                raise RuleConfigError(f"Invalid rule config at index {i}: {exc}") from exc

        # Return only enabled rules
        return [r for r in all_rules if r.enabled]
