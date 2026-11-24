# Assets

Text-only simulation assets used by the demos.

| Path | Contents |
|---|---|
| `scenes/stage1_cutlery_plunge.xml` | MJCF scene for the Stage 1 conflicting-goals demo, primitive geoms only |

Rules, enforced by `.github/scripts/check_file_sizes.py`:

- Scenes are MJCF, URDF or SDF text. Prefer primitive geoms (box, capsule, cylinder, ellipsoid, sphere).
- No file over 256 KB.
- No video, image, mesh, weight, bag or archive files in git. Host them elsewhere, download them
  at run time, and state their license and source in the demo README.
- Every asset is compatible with Apache-2.0.
