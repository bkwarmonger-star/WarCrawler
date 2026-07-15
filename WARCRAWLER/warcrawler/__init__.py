"""
warcrawler — a portable, cross-platform, high-capability web crawler for the
standard web and Tor/.onion services.

Design goals:
  * Runs from a USB stick with no install (PyInstaller one-file binaries) or as
    a plain pip package.
  * Unrestricted and content-neutral by default; every "polite" behaviour is a
    configurable knob.
  * Async worker fleet with a resumable SQLite-backed frontier.
  * Pluggable transports: clearnet (httpx), Tor (httpx-socks + stem), and an
    optional TLS-impersonating backend (curl_cffi).
  * Rich content pipeline: main-content extraction, language detection,
    near-duplicate detection, CSS/XPath rules, and regex watchlists.

Works on Windows, macOS and Linux (Python 3.9+).
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
