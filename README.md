# Sigevent

## Installation

You can install the Poetry environment by running the following command:

```bash
poetry install
```



## Build

Building produces the Lambda deployment package at `dist/cloud-sigevent-<version>.zip`:

```bash
./build.sh
```

The zip bundles the `podaac.sigevent` package, its dependencies, and the HTML
templates in `podaac/sigevent/resources/`.

Terraform reads this zip directly (`filename` + `filebase64sha256` in
`terraform/lambdas.tf`) and `dist/` is gitignored, so **build before running
`terraform plan` or `apply`** or you will deploy a stale package.

To deploy the package without Terraform:

```bash
aws lambda update-function-code --region us-west-2 \
  --function-name service-sigevent-<venue>-event-handler \
  --zip-file fileb://$(pwd)/dist/cloud-sigevent-2.0.0.zip
```

### If build.sh fails at the pydantic step

`poetry run -C build` cannot find a `pyproject.toml` under `build/`. Use the
bundled pip directly and finish the remaining steps by hand:

```bash
ROOT="$PWD"
poetry bundle venv --clear --without=dev --python=$(which python3.11) build

./build/bin/pip3 install \
  --platform manylinux2014_x86_64 \
  --target="$ROOT/build/lib/python3.11/site-packages" \
  --implementation cp --python-version 3.11 \
  --only-binary=:all: --upgrade pydantic

cd "$ROOT/build/lib/python3.11/site-packages"
touch podaac/__init__.py
find . -maxdepth 1 \( -name '*.dist-info' -o -name '_virtualenv.*' \) -exec rm -rf {} +
find . -type d -name __pycache__ -exec rm -rf {} +

mkdir -p "$ROOT/dist"
rm -f "$ROOT/dist/cloud-sigevent-2.0.0.zip"
zip -qr9 "$ROOT/dist/cloud-sigevent-2.0.0.zip" .
cd "$ROOT"
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
