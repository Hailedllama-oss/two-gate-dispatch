"""Console entry points for the two-gate-dispatch command line tools."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def _script(name: str) -> Path:
    local_script = Path(__file__).resolve().parent / name
    if local_script.is_file():
        return local_script
    installed_script = shutil.which(name)
    if installed_script:
        return Path(installed_script)
    raise RuntimeError(f"Unable to locate {name}")


def form_main() -> int:
    return subprocess.call([sys.executable, str(_script("gate_form.py")), *sys.argv[1:]])


def substance_main() -> int:
    return subprocess.call([sys.executable, str(_script("gate_substance.py")), *sys.argv[1:]])


def dispatch_main() -> int:
    return subprocess.call(["bash", str(_script("dispatch-gate.sh")), *sys.argv[1:]])
