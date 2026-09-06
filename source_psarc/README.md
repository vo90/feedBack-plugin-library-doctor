# Pinned original-chart reader

`psarc.py`, `crypto.py`, `sng.py` and the sibling `source_bend_curves.py` originate
from FeedForge commit `23bfe86` under the included MIT license. No audio converter
or private FeedBack module is imported. Library Doctor adds a 32 MiB expansion
bound to the SNG decompressor; `source_archive.py` validates archive/table sizes
and reads only selected chart blocks with bounded decompression.

The plugin declares the exact tested construct 2.10.70 and cryptography 50.0.0
dependencies. The host's normal plugin requirements mechanism installs these
into the selected profile. Reader imports are lazy and report missing
dependencies without preventing ordinary Library Doctor validation.
