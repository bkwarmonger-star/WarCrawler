Drop a Tor binary here to enable .onion crawling.
=================================================

warcrawler launches Tor as a child process from this folder so the whole
setup stays self-contained on the USB stick.

1. Download the "Tor Expert Bundle" for each OS you deploy to:
      https://www.torproject.org/download/tor/

2. Extract the `tor` executable and place it per-OS:
      tor/linux/tor          (Linux)
      tor/darwin/tor         (macOS)
      tor/windows/tor.exe    (Windows)

   (A single tor/tor or tor/tor.exe also works if you only target one OS.)

3. Install the control-port library once:  pip install stem
   (already included if you used requirements-full.txt)

4. Verify connectivity:
      ./start.sh tor-test

Windows notes
-------------
* Copy the ENTIRE contents of the Expert Bundle's `tor` folder into
  tor/windows/ (tor.exe AND any *.dll / data/ next to it). Copying tor.exe
  alone can fail to launch with missing-DLL errors.
* If `tor-test` says "Tor exited during startup", the error output is now shown
  beneath it — read the last lines (e.g. "Could not bind to 127.0.0.1:9050"
  means the port is busy; close other Tor instances or change tor.socks_port).
* Allow tor.exe through Windows Defender / your firewall; AV sometimes blocks or
  quarantines it, which looks like a bootstrap timeout.
* Run from the project folder via start.bat so tor/windows/tor.exe is found.

Notes
-----
* Circuit isolation: workers are spread across `num_circuits` distinct SOCKS
  identities, so Tor runs up to that many parallel circuits (IsolateSOCKSAuth).
* Circuit rotation: on a block (403/429/503/timeout) with tor.rotate_on_block,
  the crawler signals NEWNYM (rate-limited to ~10s) AND switches the retry to a
  brand-new, never-reused SOCKS identity — forcing a fresh circuit regardless of
  num_circuits — then drops the stale client so its circuit is not reused.
* If you already run a system Tor, set tor.auto_start: false in the job and
  point socks_port/control_port at it.
* .onion name resolution happens inside Tor (remote DNS), so no DNS leaks.
