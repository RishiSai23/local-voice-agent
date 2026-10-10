
import platform
import shutil
import os
import subprocess
from pathlib import Path

def get_system_info():
    return {
        "operating_system": platform.system(),
        "os_version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor() or "Unknown",
        "python_version": platform.python_version(),
        "cpu_cores": os.cpu_count(),
        "disk_free_gb": round(
            shutil.disk_usage(os.getcwd()).free / (1024 ** 3), 2
        ),
    }



APP_ALLOWLIST = {
    "notepad": ["notepad.exe"],
    "calculator": ["calc.exe"],
    "paint": ["mspaint.exe"],
}


def open_application(app_name):
    app_name = app_name.lower().strip()

    if app_name not in APP_ALLOWLIST:
        return f"Application '{app_name}' is not allowed."

    try:
        subprocess.Popen(APP_ALLOWLIST[app_name])
        return f"Opening {app_name}."
    except OSError as error:
        return f"Could not open {app_name}: {error}"


WORKSPACE = Path(__file__).resolve().parent / "assistant_workspace"


def get_workspace_path(filename="."):
    path = (WORKSPACE / filename).resolve()

    if path != WORKSPACE and WORKSPACE not in path.parents:
        raise ValueError("Access outside assistant workspace is not allowed.")

    return path


def list_files():
    WORKSPACE.mkdir(parents=True, exist_ok=True)

    return [
        item.name + ("/" if item.is_dir() else "")
        for item in sorted(WORKSPACE.iterdir())
    ]


def read_file(filename):
    path = get_workspace_path(filename)

    if not path.is_file():
        return "File not found."

    if path.suffix.lower() not in {".txt", ".md", ".csv", ".json", ".py"}:
        return "This file type is not allowed for reading."

    return path.read_text(encoding="utf-8")


def create_file(filename, content):
    path = get_workspace_path(filename)

    if path.suffix.lower() not in {".txt", ".md", ".csv", ".json", ".py"}:
        return "This file type is not allowed."

    if path.exists():
        return "File already exists. Nothing was overwritten."

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

    return f"Created {path.name} successfully."



if __name__ == "__main__":
    print("Workspace:", WORKSPACE)

    print("\nCreating test file:")
    print(create_file("hello.txt", "Hello! My local voice agent is working."))

    print("\nListing files:")
    print(list_files())

    print("\nReading test file:")
    print(read_file("hello.txt"))

