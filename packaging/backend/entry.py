"""Entry point of the bundled local backend (PyInstaller). Equivalent to `python -m mpnstudio.api.app`.

The backend holds no model: it owns the project store, board geometry and the optional runtime link. The Yaob
runtime and training code need torch and are not part of this bundle; they are used only through an external
Python environment that the user configures (Settings, Compute).
"""
import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from mpnstudio.api.app import main
    main()
