## What this changes

<!-- One or two sentences on behaviour. Link the issue or RFC. -->

## Checklist

- [ ] Every new demo builds on a module under `src/ramen_robotics/`.
- [ ] ramen foundry and ramen-ai-core classes are imported, not copied.
- [ ] Every non-`RELEASED` outcome leaves the controller holding; no gate call runs inside a control loop.
- [ ] Tests run offline against the fake server; `python -m pytest -q` passes.
- [ ] `ruff check .`, `ruff format --check .` and `mypy src` pass.
- [ ] `python .github/scripts/check_hygiene.py` and `python .github/scripts/check_file_sizes.py` pass.
- [ ] No keys, `.env` files, machine paths, video or binary assets.
- [ ] Commit messages follow Conventional Commits.

## Live calls used while developing

<!-- For example: "2 calls with --trial". Never paste keys. -->
