# IDriveReader

Read-only ENET/HSFZ capture tool for the owner's own BMW F30 (NBT EVO). One session at the car
records everything needed to develop offline: VIN, ECU inventory, SVK + identification DIDs, DTCs,
head-unit coding backup, full ReadDataByIdentifier scans (DME, HU, gateway), live DME samples and
every raw frame. End-user guide (Turkish): [KULLANIM.txt](KULLANIM.txt).

## Read-only guarantee

[`transport/read_only_policy.py`](src/idrivereader/transport/read_only_policy.py) is checked in the
UDS client and again in `HsfzConnection.send`, before every transmission. Allowed: `22` (any DID),
`19 02`, `3E 00/80`. Everything else raises `ReadOnlyViolation` and is never sent. Tests cover the
allow-list, both enforcement points and the layer rules (steps and GUI cannot import the socket layer).

## Layout

| Package | Role |
|---|---|
| `transport/` | HSFZ framing, read-only UDS client, discovery (ident broadcast + ARP fallback), frame recorder |
| `domain/` | ECU names and pure decoders (SVK, DTC, module IP) |
| `capture/` | steps, session runner, output store, report, `CaptureService` facade |
| `simulator/` | localhost HSFZ gateway: `ScriptedVehicle` (demo/tests) or `RecordedVehicle` (replays a capture) |
| `gui/` | tkinter window and thread-safe bridges |
| `launch.py` | the two ways a capture starts (real car / demo car), shared by the window and `--self-test` |
| `host.py` | output folder next to the exe, keep-awake during a capture |
| `third_party/nbtevo` | vendored subset of nbt-evo-toolkit (MIT): S-record codec + SVK decoding |

## macOS

`packaging/build_mac_bundle.py` (also run by `build.bat`) assembles `dist/IDriveReader-mac`: the
sources, checksum-verified relocatable CPython 3.12 builds for Apple Silicon and Intel
(astral-sh/python-build-standalone, Tk included) and `IDriveReader.command`, which unpacks the
matching Python into `~/Library/Application Support/IDriveReader` on first run and starts the
same GUI. Guide: [KULLANIM-MAC.txt](KULLANIM-MAC.txt). macOS-specific code: `ifconfig` address
discovery and `IP_BOUND_IF` broadcast pinning (`transport/discovery.py`), `caffeinate`
(`host.py`), Finder (`gui/system.py`). Tests pass on Python 3.12; not yet run on a real Mac.

## Develop

```bat
build.bat                                   :: venv + tests + exe + packaged self-test
.venv\Scripts\python -m unittest discover -s tests -v
.venv\Scripts\python -m idrivereader        :: run the GUI from source (with src on PYTHONPATH)
```

Replay a real capture as a fake car on `127.0.0.1:6801` / `:6811`:

```bat
set PYTHONPATH=src
.venv\Scripts\python -m idrivereader.simulator --capture kayitlar\kayit_<VIN>_<tarih>
```
