# PyInstaller spec for the local backend. Build with scripts/build-backend.sh
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
hidden = (collect_submodules("uvicorn") + collect_submodules("mpnstudio.api") + collect_submodules("mpnstudio.store")
          + collect_submodules("mpnstudio.desktop") + collect_submodules("mpnstudio.engine") + collect_submodules("mpnstudio.library") + collect_submodules("mpnstudio.catalogue") + collect_submodules("mpnstudio.mpnnet")
          + ["mpnstudio.runtime.procedures", "mpnstudio.runtime.budget", "mpnstudio.runtime.conversation", "mpnstudio.cli", "mpnstudio.localcli", "mpnstudio.train.jobs", "mpnstudio.train.gpulock", "orjson", "httpx", "psutil", "numpy"])
datas = [(os.path.join(root, "mpnstudio", "library", "data"), os.path.join("mpnstudio", "library", "data"))]
# The FG-MPN runtime (the public wheel for this machine, installed into the build environment by scripts/build-backend.sh):
# the Python package, its search table, the native reader where the platform wheel has one, and its licence notices.
hidden += collect_submodules("fgmpn")
datas += collect_data_files("fgmpn")
a = Analysis([os.path.join(SPECPATH, "entry.py")], pathex=[root], hiddenimports=hidden, datas=datas,
             excludes=["torch", "transformers", "peft", "accelerate", "safetensors", "tkinter", "matplotlib", "pytest", "IPython", "scipy", "pandas", "readline"],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="mpn-backend", console=True, strip=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="mpn-backend")
