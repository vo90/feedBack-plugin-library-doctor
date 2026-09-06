"""Bounded selected-chart reads from an explicitly selected original PSARC."""
import hashlib
import importlib.util
import struct
import sys
import zlib
from pathlib import Path

MAX_ARCHIVE = 2 * 1024 ** 3
MAX_MEMBER = 32 * 1024 ** 2
MAX_TOTAL = 128 * 1024 ** 2


def _reader():
    try:
        name = "_library_doctor_source_psarc"
        if name not in sys.modules:
            directory = Path(__file__).with_name("source_psarc")
            spec = importlib.util.spec_from_file_location(name, directory / "__init__.py",
                submodule_search_locations=[str(directory)])
            package = importlib.util.module_from_spec(spec)
            sys.modules[name] = package
            spec.loader.exec_module(package)
        from _library_doctor_source_psarc.psarc import HEADER
        from _library_doctor_source_psarc.crypto import decrypt_sng, WIN_KEY, MAC_KEY
        from _library_doctor_source_psarc.sng import Song
        return HEADER, decrypt_sng, WIN_KEY, MAC_KEY, Song
    except ModuleNotFoundError as exc:
        raise ValueError("Source recovery requires the plugin's declared construct and cryptography dependencies. Reload the plugin after its dependency setup completes.") from exc


def source_path(value):
    path = Path(value)
    if path.suffix.lower() != ".psarc" or path.is_symlink() or not path.is_file():
        raise ValueError("Select an existing original .psarc file.")
    path = path.resolve(strict=True)
    if not 32 <= path.stat().st_size <= MAX_ARCHIVE:
        raise ValueError("The selected source archive exceeds the supported 2 GiB bound.")
    return path


def source_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _read_entry(stream, entry, lengths, file_size, limit):
    if entry.length > limit or entry.offset < 32 or entry.offset >= file_size:
        raise ValueError("A selected source member has invalid bounds.")
    stream.seek(entry.offset)
    result = bytearray()
    for size in lengths[entry.zindex:]:
        if len(result) == entry.length:
            break
        size = size or 65536
        if stream.tell() + size > file_size:
            raise ValueError("A source archive block is truncated.")
        block = stream.read(size)
        try:
            decompressor = zlib.decompressobj()
            expanded = decompressor.decompress(block, 65537)
            if len(expanded) > 65536 or not decompressor.eof:
                raise ValueError("A source archive block exceeds its declared expansion bound.")
            block = expanded
        except zlib.error:
            pass  # PSARC stores incompressible blocks directly.
        result.extend(block)
        if len(result) > entry.length or len(result) > limit:
            raise ValueError("A source member exceeds its declared size.")
    if len(result) != entry.length:
        raise ValueError("A source member is truncated.")
    return bytes(result)


def read_source_charts(value):
    path = source_path(value)
    HEADER, decrypt_sng, win_key, mac_key, Song = _reader()
    before = source_hash(path)
    results, total = [], 0
    with path.open("rb") as stream:
        header_bytes = stream.read(32)
        header_size, entries = struct.unpack_from(">I", header_bytes, 12)[0], struct.unpack_from(">I", header_bytes, 20)[0]
        file_size = path.stat().st_size
        if not 32 <= header_size <= min(file_size, 8 * 1024 ** 2) or not 1 <= entries <= 20000:
            raise ValueError("The source archive table exceeds supported bounds.")
        stream.seek(0)
        header = HEADER.parse_stream(stream)
        listing = _read_entry(stream, header.bom.entries[0], header.bom.zlength, file_size, 2 * 1024 ** 2).decode("utf8").splitlines()
        if len(listing) != entries - 1 or len(set(listing)) != len(listing):
            raise ValueError("The source archive has an ambiguous member listing.")
        selected = [(i + 1, name) for i, name in enumerate(listing)
                    if name.lower().endswith(".sng") and "/songs/bin/" in ("/" + name.replace("\\", "/").lower())]
        if len(selected) > 128:
            raise ValueError("Select a source archive with at most 128 chart arrangements.")
        for index, name in selected:
            entry = header.bom.entries[index]
            total += entry.length
            if total > MAX_TOTAL:
                raise ValueError("The selected source charts exceed the 128 MiB inspection bound.")
            raw = _read_entry(stream, entry, header.bom.zlength, file_size, MAX_MEMBER)
            key = mac_key if "/macos/" in name.lower() else win_key
            data = decrypt_sng(raw, key)
            if len(data) > MAX_MEMBER:
                raise ValueError("A source chart exceeds the supported expansion bound.")
            song = Song.parse(data)
            if song.levels:  # Ignore vocal/showlight-only SNGs.
                results.append({"member": name, "sha256": hashlib.sha256(data).hexdigest(), "song": song})
    if before != source_hash(path):
        raise ValueError("The selected original source changed during inspection.")
    return {"path": path, "sha256": before, "charts": results}
