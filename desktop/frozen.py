"""Build-time additions to Argus's existing frozen application hook."""
import configparser
import shutil
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


def extend(argus_root: Path):
    data = collect_data_files("argus_hosting", include_py_files=True, includes=["trial/**"])
    # The desktop ships the client, not the tenant server. Keep distribution
    # metadata for real plugin discovery, with only the client it actually
    # contains registered. Never modify the installed distribution's metadata.
    for source, target in copy_metadata("argus-hosting"):
        staging = argus_root / "desktop-tauri/build/hosting-metadata" / target
        shutil.copytree(source, staging, dirs_exist_ok=True)
        entries = configparser.ConfigParser(interpolation=None)
        entries.optionxform = str
        entries.read(staging / "entry_points.txt", encoding="utf-8")
        if not entries.has_section("argus.hosting.client"):
            raise RuntimeError("Install argus-hosting with its client entry point before freezing")
        for section in entries.sections():
            if section != "argus.hosting.client":
                entries.remove_section(section)
        with (staging / "entry_points.txt").open("w", encoding="utf-8") as stream:
            entries.write(stream)
        data.append((str(staging), target))
    imports = collect_submodules("argus_hosting.trial", on_error="raise")
    if "argus_hosting.trial.desktop" not in imports:
        raise RuntimeError("The installed hosting client is missing; install Argus and argus-hosting in the build environment")
    return str(Path(__file__).with_name("backend_entry.py")), data, imports
