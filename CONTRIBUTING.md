# Contributing

Issues and pull requests are welcome. By contributing you agree that your work is licensed under the Apache License 2.0
(see `LICENSE`).

## Set up

    python3 -m venv .venv-build && . .venv-build/bin/activate
    pip install "fastapi>=0.115" "uvicorn[standard]>=0.30" "httpx>=0.27" "pydantic>=2.7" "orjson>=3.10" "numpy>=2.0" "psutil>=6.0" pytest
    pytest tests -q                       # the tests that need torch are skipped without it
    cd web && pnpm install --frozen-lockfile && pnpm build

`docs/desktop/BUILD.md` covers running the desktop shell and building the packages.

## Before you open a pull request

* `pytest tests -q` passes and `pnpm build` in `web/` succeeds.
* A change to the interface was looked at in the running app, not only compiled. Every visible control must do what
  it says.
* No secrets, personal paths or addresses in code, docs or screenshots.
* Keep names as they are: `mpnstudio`, `MPN_*` and `x-mpn-token` are public identifiers; see `docs/desktop/TERMINOLOGY.md`.
