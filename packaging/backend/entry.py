"""Entry point of the bundled backend (PyInstaller). One executable, three roles:

    mpn-backend                      the local service of the desktop window (python -m mpnstudio.api.app)
    mpn-backend serve-root ...       the runtime of one model, started by the service for each running model
    mpn-backend mpn <command> ...    the `mpn` command line (also when the executable is called through a link named mpn)
    mpn-backend fgmpn-serve ...      the read-only reader of one FG-MPN store (started by the service when you open one)

Models run in llama.cpp (runtime/llama, next to this executable). Training is not part of this bundle; it needs torch
and is used only through an external Python environment that the user configures (Settings, Compute).
"""
import multiprocessing
import os
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    args = sys.argv[1:]
    as_mpn = os.path.basename(sys.argv[0]) == "mpn"
    if args and args[0] == "mpn":
        args, as_mpn = args[1:], True
    if args and args[0] == "fgmpn-serve":
        from mpnstudio.mpnnet.fgmpn_serve import main as fg_main
        fg_main(args[1:])
    elif as_mpn or (args and args[0] == "serve-root"):
        from mpnstudio.cli import main as cli_main
        cli_main(args)
    else:
        from mpnstudio.api.app import main
        main()
