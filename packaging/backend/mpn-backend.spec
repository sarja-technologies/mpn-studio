# PyInstaller spec for the local backend. Build with scripts/build-backend.sh
import os
from PyInstaller.utils.hooks import collect_submodules

root = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
hidden = (collect_submodules("uvicorn") + collect_submodules("mpnstudio.api") + collect_submodules("mpnstudio.store")
          + collect_submodules("mpnstudio.desktop") + ["mpnstudio.runtime.procedures", "mpnstudio.runtime.budget", "mpnstudio.train.jobs",
                                                       "mpnstudio.train.gpulock", "orjson", "httpx", "psutil", "numpy"])
a = Analysis([os.path.join(SPECPATH, "entry.py")], pathex=[root], hiddenimports=hidden, datas=[],
             excludes=["torch", "transformers", "peft", "accelerate", "safetensors", "tkinter", "matplotlib", "pytest", "IPython", "scipy", "pandas", "readline"],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="mpn-backend", console=True, strip=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="mpn-backend")
