"""Clean run of the pipeline: executes the notebooks in order, in place, with saved outputs.

Usage (from the repository root, inside the environment of requirements.txt):
    python src/run_notebooks.py            # A-D2: 01 -> 02 -> 03
    python src/run_notebooks.py --stage F  # F: 04 (Optuna tuning, ~10-20 min; writes artifacts/modeling/)
    python src/run_notebooks.py --stage G1 # 05: rolling policy selection, schedule and business case
    python src/run_notebooks.py --all      # 01 -> 02 -> 03 -> 04 -> 05
Each notebook starts in a fresh kernel, so the run is equivalent to "Restart & Run All" in order, and that
kernel is shut down after the notebook (also when it fails), so memory is not accumulated across notebooks.
The kernel is pinned to the interpreter running this script (a temporary kernelspec), so a "python3"
kernel registered elsewhere on the machine can never be picked up by mistake.
"""
import json
import sys
import tempfile
import time
from importlib.metadata import version
from pathlib import Path

import nbformat
from jupyter_client.kernelspec import KernelSpecManager
from jupyter_client.manager import KernelManager
from nbclient import NotebookClient

ROOT = Path(__file__).resolve().parents[1]
STAGE_AD = ["01_data_audit_eda.ipynb", "02_data_architecture.ipynb", "03_preparation_eda_features.ipynb"]
STAGE_F = ["04_modeling_evaluation.ipynb"]
STAGE_G1 = ["05_decision_business_case.ipynb"]
if "--all" in sys.argv:
    ORDER = STAGE_AD + STAGE_F + STAGE_G1
elif "--stage" in sys.argv and sys.argv[sys.argv.index("--stage") + 1].upper() == "F":
    ORDER = STAGE_F
elif "--stage" in sys.argv and sys.argv[sys.argv.index("--stage") + 1].upper() == "G1":
    ORDER = STAGE_G1
else:
    ORDER = STAGE_AD
PACKAGES = ["pandas", "numpy", "scikit-learn", "scipy", "matplotlib", "seaborn", "PyYAML"]
if any(n in STAGE_F for n in ORDER):
    PACKAGES += ["xgboost", "optuna"]
elif any(n in STAGE_G1 for n in ORDER):
    PACKAGES += ["xgboost"]

if sys.platform.startswith("win"):   # avoid the zmq / Proactor event-loop warning on Windows
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

print(f"Python {sys.version.split()[0]} at {sys.executable}")
print("  " + ", ".join(f"{p} {version(p)}" for p in PACKAGES))

if ORDER == STAGE_G1:
    # A separate fresh process also works in environments that disallow kernel sockets.
    import subprocess
    subprocess.run([sys.executable, str(ROOT / "src/run_g1.py")], check=True, cwd=ROOT)
    sys.exit(0)

with tempfile.TemporaryDirectory() as kdir:
    spec_dir = Path(kdir) / "ba-project"
    spec_dir.mkdir()
    (spec_dir / "kernel.json").write_text(json.dumps({
        "argv": [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"],
        "display_name": "BA project (pinned)", "language": "python"}))
    ksm = KernelSpecManager(kernel_dirs=[kdir])
    for name in ORDER:
        path = ROOT / "notebooks" / name
        start = time.time()
        nb = nbformat.read(path, as_version=4)
        km = KernelManager(kernel_name="ba-project", kernel_spec_manager=ksm)
        try:
            # cleanup_kc=True: with an external km, nbclient (owns_km=False) would otherwise leave the kernel
            # running after the notebook, so the RAM of 01-03 would still be held while 04 runs.
            NotebookClient(nb, km=km, timeout=3600, resources={"metadata": {"path": str(path.parent)}}).execute(cleanup_kc=True)
        finally:   # also save a failed run, so the error and the outputs before it can be inspected
            try:   # make sure the kernel is gone, also when the notebook failed
                if km.has_kernel:
                    km.shutdown_kernel(now=True)
            except Exception as exc:   # never hide the notebook error behind a shutdown error
                print(f"  warning: kernel shutdown after {name} failed: {exc}")
            nb.metadata["kernelspec"] = {"display_name": "Python 3", "language": "python", "name": "python3"}
            nbformat.write(nb, path)
        assert not km.has_kernel, f"kernel of {name} is still running"
        print(f"{name}: OK ({time.time() - start:.0f}s, kernel stopped)")
