# Sigevent

## Installation

You can install the Poetry environment by running the following command:

```bash
poetry install
```



## Tests

Tests can be run using Poetry with the following command:

```bash
poetry run pytest tests/
```

Tests can be run with test coverage with the following command:

```bash
poetry run pytest --cov=sigevent tests/
```

## Linting

Cloud Sigevent uses Pylint. You can run Pylint like so:

```bash
poetry run pylint sigevent/
```

We maintain 10.00/10 score on this repo, so output should look like:

```
--------------------------------------------------------------------
Your code has been rated at 10.00/10 (previous run: 10.00/10, +0.00)
```

## Authors

Stepheny Perez: Version 1 Developer

Joshua Garde: Version 2 Developer
