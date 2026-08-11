import logging
from pathlib import Path

from privacy import PrivacySafeLog


class _CaptureLogger:
    def __init__(self):
        self.records = []

    def _capture(self, level, message, *args, **kwargs):
        self.records.append((level, message % args if args else str(message), kwargs))

    def debug(self, message, *args, **kwargs):
        self._capture("debug", message, *args, **kwargs)

    def info(self, message, *args, **kwargs):
        self._capture("info", message, *args, **kwargs)

    def warning(self, message, *args, **kwargs):
        self._capture("warning", message, *args, **kwargs)

    def error(self, message, *args, **kwargs):
        self._capture("error", message, *args, **kwargs)

    def critical(self, message, *args, **kwargs):
        self._capture("critical", message, *args, **kwargs)

    def log(self, level, message, *args, **kwargs):
        self._capture(str(level), message, *args, **kwargs)

    def isEnabledFor(self, _level):  # noqa: N802 - mirrors logging.Logger
        return True


def _support_bundle_text(capture):
    return "\n".join(message for _level, message, _kwargs in capture.records)


def test_support_facing_logs_hide_song_package_and_path_identities():
    capture = _CaptureLogger()
    log = PrivacySafeLog(capture, salt=b"deterministic-phase-2-salt")
    package = "Private Artist/Unreleased Song.feedpak"
    local_path = Path("C:/Users/private/Music/Private Artist/Unreleased Song.feedpak")

    log.warning("Library Doctor validation failed for %s at %s", package, local_path)
    log.warning("The same package is %s", package)
    bundle = _support_bundle_text(capture)

    assert "Private Artist" not in bundle
    assert "Unreleased Song" not in bundle
    assert ".feedpak" not in bundle.lower()
    assert "C:/Users/private" not in bundle
    assert "[local-path]" in bundle
    package_tokens = [word for word in bundle.split() if word.startswith("package:")]
    assert len(package_tokens) == 2
    assert package_tokens[0] == package_tokens[1]


def test_support_facing_exception_logs_keep_type_without_message_or_traceback():
    capture = _CaptureLogger()
    log = PrivacySafeLog(capture, salt=b"deterministic-phase-2-salt")
    private_message = "C:/Users/private/Music/Private Artist/Unreleased Song.feedpak"

    try:
        raise OSError(private_message)
    except OSError as exc:
        log.exception("Library Doctor repair failed safely: %s", exc)

    level, message, kwargs = capture.records[0]
    assert level == "error"
    assert message == "Library Doctor repair failed safely: OSError"
    assert private_message not in _support_bundle_text(capture)
    assert kwargs["exc_info"] is False


def test_privacy_adapter_remains_compatible_with_standard_logger(caplog):
    logger = logging.getLogger("library-doctor-privacy-contract")
    logger.setLevel(logging.INFO)
    log = PrivacySafeLog(logger, salt=b"deterministic-phase-2-salt")

    with caplog.at_level(logging.INFO, logger=logger.name):
        log.info("Package %s finished with %d change", "Artist/Song.feedpak", 1)

    assert "Artist" not in caplog.text
    assert ".feedpak" not in caplog.text.lower()
    assert "package:" in caplog.text
    assert "1 change" in caplog.text


def test_routes_install_the_privacy_boundary_before_migration_and_services():
    source = (Path(__file__).parents[1] / "routes.py").read_text(encoding="utf-8")

    privacy = source.index('load_sibling("privacy")')
    wrapping = source.index("PrivacySafeLog(host_log)")
    migration = source.index('load_sibling("migration")')
    scanner = source.index('load_sibling("scanner")')
    assert privacy < wrapping < migration < scanner
