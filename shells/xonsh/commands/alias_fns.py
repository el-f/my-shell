"""Xonsh alias helper functions."""

import os
import platform
import shutil
import subprocess


def _clear_screen(args, stdin=None):
    if os.name == "nt":
        subprocess.run("cls", shell=True, check=False)
    else:
        subprocess.run(["clear"], check=False)


def _platform_meminfo(args, stdin=None):
    try:
        import psutil

        mem = psutil.virtual_memory()
        print(f"Total: {mem.total / (1024**3):.1f} GB")
        print(f"Available: {mem.available / (1024**3):.1f} GB")
        print(f"Used: {mem.percent}%")
    except ImportError:
        if platform.system() == "Windows":
            subprocess.run(["systeminfo"], check=False)
        elif platform.system() == "Darwin":
            subprocess.run(["vm_stat"], check=False)
        else:
            subprocess.run(["free", "-h"], check=False)


def _platform_cpuinfo(args, stdin=None):
    try:
        import psutil

        print(f"Cores: {psutil.cpu_count(logical=False)}")
        print(f"Threads: {psutil.cpu_count(logical=True)}")
        print(f"Usage: {psutil.cpu_percent(interval=1)}%")
    except ImportError:
        if platform.system() == "Windows":
            subprocess.run(["wmic", "cpu", "get", "caption"], check=False)
        elif platform.system() == "Darwin":
            subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], check=False)
        else:
            subprocess.run(["lscpu"], check=False)


def make_wrapper(preferred: str, fallback: str, error_msg: str):
    """Create a wrapper alias that resolves preferred/fallback at runtime."""

    def wrapper(args, stdin=None):
        if shutil.which(preferred):
            cmd = [preferred, *list(args)]
        elif shutil.which(fallback):
            cmd = [fallback, *list(args)]
        else:
            print(error_msg)
            return
        subprocess.run(cmd)

    return wrapper
