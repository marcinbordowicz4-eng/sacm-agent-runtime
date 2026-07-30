# Contributing

Use a focused branch and include tests for behavior changes. Before opening a
pull request, run:

```bash
ruff check sacm apps cli tests
mypy sacm apps cli
pytest -q
python -m build
```

Production-affecting changes must also preserve migration compatibility and
document rollback behavior. Do not commit secrets, evidence packs, generated
databases, or local deployment environment files.
