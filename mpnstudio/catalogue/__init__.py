"""Official releases (`fylume-release/1`): the catalogue Studio reads, downloads and installs from.

manifest.py   parse and validate a release manifest; is it usable by this Studio?
source.py     where the catalogue comes from: a configurable URL, a cached copy, manifests imported from files
download.py   resumable, size- and SHA-256-checked file downloads from the hosting a manifest names
install.py    install a release atomically (mpn, core, or a prebuilt pairing of both), list and verify what is installed

Nothing here knows a Hugging Face organisation or any file name: hosting always comes from the manifest.
"""
