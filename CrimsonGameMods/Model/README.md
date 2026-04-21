# Model/ — vendored PAC/PAM/PAB parsers

Third-party Python code vendored here for reading Crimson Desert mesh + skeleton formats locally, without having to clone the upstream repos.

All files retain their original source — only `pab_skeleton_parser.py` was patched to replace its project-specific logger with stdlib `logging`.

## Provenance

| File(s) | Source | License |
| --- | --- | --- |
| `pac_parser.py`, `pac_decode.py`, `pam_parser.py`, `parser_factory.py`, `model_types.py`, `exporters/*.py` | https://github.com/Altair200333/crimson-desert-model-browser | MIT |
| `pab_skeleton_parser.py` (renamed from `skeleton_parser.py`) | https://github.com/hzeemr/crimsonforge | MIT (see `LICENSE_crimsonforge`) |

## What's in each file

| File | Purpose |
| --- | --- |
| `model_types.py` | `ParsedModel`, `SubMesh`, `VertexBuffer`, `IndexBuffer`, `BoundingBox`, `Bone`, `SourceFormat` |
| `pac_parser.py` | Parse `.pac` skinned meshes — **extracts named bones with full transforms** |
| `pac_decode.py` | Quantised vertex / index decoders (numpy) |
| `pam_parser.py` | Parse `.pam` static meshes |
| `parser_factory.py` | Dispatch by extension |
| `pab_skeleton_parser.py` | Parse `.pab` standalone skeleton files (PAR v5.1) |
| `exporters/obj_exporter.py` | Wavefront OBJ + MTL |
| `exporters/fbx_exporter.py` | FBX ASCII 7.4 with full skeleton + skin weights (Blender-ready) |
| `exporters/gltf_exporter.py` | glTF 2.0 |

## Usage

```python
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'Model'))

from pac_parser import PacParser
parser = PacParser()
model = parser.parse(open('cd_m0004_00_golemdragon.pac', 'rb').read())

for b in model.bones or []:
    print(b.name, b.position, b.parent_index)
```

Or:

```python
import Model  # sets sys.path automatically
from pac_parser import PacParser
```

## Known limitations (inherited from upstream)

- `pac_parser.py` is read-only. No mesh write-back yet (upstream says "possible, coming soon").
- `pab_skeleton_parser.py` is read-only. No skeleton write-back in the upstream project either.
- No rigging edits via these tools directly — they give us the data, modifications are on us.
