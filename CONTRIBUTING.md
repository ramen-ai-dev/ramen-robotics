# Contributing to ramen robotics

ramen robotics is a thin trust and transport core (`src/ramen_robotics/gate/`) plus modules
that contributors design and own. This file covers the rules every pull request follows.

## Ground rules

1. **Every demo builds on a reusable module.** A demo under `demos/` imports its behaviour
   from a package under `src/ramen_robotics/`. If the module does not exist yet, the pull
   request adds it with tests, or an RFC proposes it first. Demo files hold scenario data,
   printing and command-line wiring only.
2. **New modules start as an RFC.** Read the matching file in `docs/rfcs/` or open a
   *Module RFC* issue before writing code. Agree on the public interface, the placement
   between planner and controller, and the test plan.
3. **Build on ramen foundry; never copy it.** Import `IndustrialAutomationAgent`,
   `ManipulationDispatchPayload`, `TrialComplianceClient`, `signed_verdict_problem` and the
   ramen-ai-core clients. CI fails on any re-definition of these symbols.
4. **Hold is the default.** Only `Outcome.RELEASED` lets a host tool reach a controller. A
   module never retries a BLOCK, never falls back from the trial to a key or back, and never
   calls the gate inside a control-rate loop.
5. **Use precise safety wording.** Under ISO 10218-2:2025, "collaborative" describes an
   application or a cell, never a bare manipulator. Write "a robot in a collaborative
   application", not "a collaborative robot". Robot identifiers use neutral prefixes such as
   `ARM-`.
6. **Brand names in prose** are lowercase: ramen ai, ramen foundry, ramen robotics. The
   hyphenated forms appear only in package names, the repository slug and URLs.

## Development setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[simulation,dev]"
python -m pytest -q
ruff check . && ruff format --check . && mypy src
python .github/scripts/check_hygiene.py
python .github/scripts/check_file_sizes.py
```

CI runs the same commands on Python 3.10, 3.11, 3.12 and 3.13.

## Tests

- Tests run offline. `tests/conftest.py` refuses every socket connection, and `tests/fakes.py`
  provides a fake ramen ai server that signs real Schema V5 receipts with a generated key. Use
  the real `RamenClient` and `TrialComplianceClient` with `FakeRamenServer.transport`.
- Cover every `Outcome` your module can produce, including the hold paths.
- Tests that call the live service are marked `@pytest.mark.live`, opt in with
  `RAMEN_LIVE_TESTS=1`, state their call count, and never run in CI. The keyless trial allows
  about 10 requests per day per network, shared by everyone on that network.
- Do not weaken, skip or delete an assertion to make a run pass.

## Assets

- Scenes are text: MJCF, URDF or SDF. Prefer primitive geoms.
- Any file over 256 KB, and any video, image, mesh, weight, bag or archive file, is rejected by
  `check_file_sizes.py`. Host large assets elsewhere and download them at run time, with their
  license and source stated in the demo README.
- Contributed assets must be compatible with Apache-2.0.

## Secrets and paths

Never commit API keys, provider keys, `.env` files or absolute machine paths. Read credentials
from the environment only. `check_hygiene.py` scans for common key formats and home-directory
paths.

## Commits and pull requests

- Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`), describing
  what the code now does.
- One topic per pull request. Fill in the pull request template.
- Pull requests are reviewed after CI is green and merged with a linear history.

## License

By contributing you agree that your contribution is licensed under Apache-2.0.
