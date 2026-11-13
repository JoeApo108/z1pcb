"""Log file and a last-resort handler: in the packaged app there is no console, so errors must land somewhere."""

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __version__

log = logging.getLogger('z1pcb')


def log_dir():
    if os.environ.get('Z1PCB_LOG_DIR'):
        return Path(os.environ['Z1PCB_LOG_DIR'])
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'Z1 PCB' / 'logs'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Logs' / 'Z1 PCB'
    return Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local' / 'state')) / 'z1pcb'


def setup(on_error=None):
    """Start logging to a file; uncaught errors are logged and passed to `on_error(text)` (shown to the user)."""
    d = log_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(d / 'z1pcb.log', maxBytes=512_000, backupCount=2, encoding='utf-8')
    except OSError:
        handler = logging.NullHandler()
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.info('start %s on %s, python %s', __version__, sys.platform, sys.version.split()[0])

    def hook(kind, value, tb):
        log.error('uncaught error', exc_info=(kind, value, tb))
        if on_error:
            on_error('%s: %s' % (kind.__name__, value))

    sys.excepthook = hook
    threading.excepthook = lambda a: hook(a.exc_type, a.exc_value, a.exc_traceback)
    return d / 'z1pcb.log'
