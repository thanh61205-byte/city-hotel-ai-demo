"""Run notebook 05 in this fresh Python process and save outputs, without a kernel server."""
import base64
import contextlib
import io
import os
import sys
import time
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MPLBACKEND", "Agg")
import nbformat
import matplotlib.pyplot as plt


def main():
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT / "src"))
    subprocess.run([sys.executable, "-u", str(ROOT / "src/g1_decision.py")], cwd=ROOT, check=True)
    path = ROOT / "notebooks/05_decision_business_case.ipynb"
    notebook = nbformat.read(path, as_version=4)
    namespace = {"__name__": "__main__"}
    started = time.monotonic()
    count = 0
    for cell in notebook.cells:
        if cell.cell_type == "code":
            cell.outputs = []
            cell.execution_count = None
            cell.metadata.pop("execution", None)
    for i, cell in enumerate(notebook.cells):
        if cell.cell_type != "code":
            continue
        print(f"G1 cell {i}...", flush=True)
        count += 1
        cell.execution_count = count
        stream = io.StringIO()
        def display(*values):
            for value in values:
                data = {"text/plain": repr(value)}
                if hasattr(value, "_repr_html_"):
                    html = value._repr_html_()
                    if html:
                        data["text/html"] = html
                cell.outputs.append(nbformat.v4.new_output("display_data", data=data))
        def show(*args, **kwargs):
            for number in plt.get_fignums():
                image = io.BytesIO()
                plt.figure(number).savefig(image, format="png", bbox_inches="tight")
                cell.outputs.append(nbformat.v4.new_output(
                    "display_data", data={"image/png": base64.b64encode(image.getvalue()).decode()}))
            plt.close("all")
        plt.show = show
        namespace["display"] = display
        # Cell 1 imports IPython.display; following cells use the captured display above.
        try:
            with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
                exec(compile(cell.source, f"{path.name}:cell_{i}", "exec"), namespace)
        except Exception as exc:
            cell.outputs.append(nbformat.v4.new_output("error", ename=type(exc).__name__,
                                                       evalue=str(exc), traceback=[]))
            raise
        finally:
            if stream.getvalue():
                cell.outputs.insert(0, nbformat.v4.new_output("stream", name="stdout", text=stream.getvalue()))
                print(stream.getvalue()[-1200:], flush=True)
            nbformat.write(notebook, path)
    print(f"G1 complete ({time.monotonic() - started:.1f}s); notebook outputs saved.", flush=True)
    return namespace


if __name__ == "__main__":
    main()
