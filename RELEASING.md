# Releasing beav3r-sdk-langchain

This document is for package maintainers.

## Release order

Publish `beav3r-sdk` before `beav3r-sdk-langchain`.

The LangChain adapter depends on `beav3r-sdk>=1.0.0b1`, so public installation can fail if the
adapter is published before the base SDK version it requires.

## Pre-release checks

```bash
python3 -m pip install -U build twine
python3 -m build
python3 -m twine check dist/*
python3 -m unittest discover -s tests -v
```

## Publish

```bash
python3 -m twine upload dist/*
```

## Post-publish smoke test

```bash
python3 -m venv /tmp/beav3r-langchain-smoke
source /tmp/beav3r-langchain-smoke/bin/activate
python -m pip install -U pip
python -m pip install beav3r-sdk beav3r-sdk-langchain langchain-openai
python -c "import beav3r_sdk, langchain_beav3r; print('ok')"
```
