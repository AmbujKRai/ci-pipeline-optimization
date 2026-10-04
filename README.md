# CI Pipeline Optimization Using Parallel Test Execution and Build Caching

A complete DevOps delivery pipeline for **ShopLite**, a small inventory and order management
service, built to measure how much **parallel test execution** and **build caching** speed up
continuous integration.

## Quick start (no Docker needed)

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
python -m app                      # http://localhost:8000  (API docs at /docs)
pytest -n auto                     # run the test suite on every CPU core
```

Full documentation is being written.
