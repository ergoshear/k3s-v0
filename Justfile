default:
    @just --list

# Run the configuration test suite
test:
    uv run --locked python -m unittest discover -s tests -v

# Install the repository's pre-commit hook
install-hooks:
    uv run --locked pre-commit install
