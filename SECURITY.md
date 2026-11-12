# Security

MPN Studio runs on your computer and listens only on `127.0.0.1` (a random port, protected by a per-launch token). It makes
no outbound connections of its own; the optional remote compute link goes over SSH to a machine you name.

If you find a vulnerability, please report it privately through
[GitHub's private vulnerability reporting](https://github.com/sarja-technologies/mpn-studio/security/advisories/new)
rather than a public issue. Include the version (the package file name), what you did and what happened.
