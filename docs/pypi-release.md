# Releasing RAGLite to (Test)PyPI

## Prereqs

- A PyPI/TestPyPI account
- An API token
  - Set env vars:
    ```bash
    export TWINE_USERNAME="__token__"
    export TWINE_PASSWORD="pypi-..."
    ```

## Build

```bash
python3 -m pip install -U build twine
python3 -m build
python3 -m twine check dist/*
```

## Upload to TestPyPI (recommended first)

```bash
python3 -m twine upload --repository testpypi dist/*
```

## Install from TestPyPI

```bash
python3 -m pip install -i https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple raglite
raglite --help
```

## Upload to PyPI

```bash
python3 -m twine upload dist/*
```
