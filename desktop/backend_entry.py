"""Frozen hosting desktop entry; explicitly permit the installed client package."""
from argus.desktop_backend_entry import main

if __name__ == "__main__":
    raise SystemExit(main(application_package="argus_hosting"))
