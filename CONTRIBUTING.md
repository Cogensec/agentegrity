# Contributing to the Agentegrity Framework

Thank you for your interest in contributing to the Agentegrity Framework. This project is an open standard — contributions from the community are how it gets better.

## Ways to Contribute

### Specification
- Propose refinements to property definitions or layer architecture
- Identify gaps in control coverage
- Suggest new conformance requirements
- Improve clarity of existing spec language

### Reference Implementation
- Add validators for common agent frameworks (LangChain, CrewAI, AutoGen, etc.)
- Improve threat detection in the adversarial layer
- Extend behavioral drift detection in the cortical layer
- Add policy rule templates for the governance layer
- Port the SDK to other languages (Go, TypeScript, Rust)

### Testing & Benchmarks
- Add test cases for edge conditions
- Build property evaluation benchmarks
- Create adversarial test suites for coherence scoring

### Documentation
- Improve examples and usage guides
- Write integration tutorials
- Translate documentation

## Development Setup

```bash
# Clone the repo
git clone https://github.com/cogensec/agentegrity.git
cd agentegrity

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate

# Install in development mode with dev dependencies
pip install -e ".[dev,crypto]"

# Run tests
pytest

# Run linter
ruff check src/ tests/

# Run type checker
mypy src/agentegrity/
```

## Pull Request Process

1. Fork the repository and create a feature branch from `main`
2. Write tests for any new functionality
3. Ensure all tests pass and linting is clean
4. Update documentation if your change affects the public API or spec
5. Submit a pull request with a clear description of the change

### Commit Messages

Use clear, descriptive commit messages:
- `feat: add LangChain adapter for cortical layer`
- `fix: correct coherence scoring when no threats detected`
- `spec: clarify environmental portability scoring methodology`
- `docs: add example for custom validator registration`
- `test: add edge cases for attestation chain verification`

## Specification Changes

Changes to the specification (files under `spec/`) require additional review. When proposing spec changes:

1. Open an issue first to discuss the proposed change
2. Explain the motivation and any backward compatibility implications
3. Include examples showing how the change affects evaluation behavior
4. Spec changes are versioned per semantic versioning rules in the specification

## Code Style

- Python 3.10+ with type annotations
- Format with `ruff format`
- Lint with `ruff check`
- Type check with `mypy --strict`
- Docstrings in NumPy style

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.
