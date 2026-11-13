"""New-version check against GitHub Releases. An installer is run only if our release key signed its hash:
access to the GitHub repository alone is not enough to push code to the users."""

import base64
import hashlib
import json
import os
import re
import tempfile
import urllib.request
from dataclasses import dataclass

from . import __version__
from .errors import UserError

REPO = 'JoeApo108/z1pcb-releases'
API = 'https://api.github.com/repos/%s/releases/latest' % REPO
DOWNLOADS = 'https://github.com/%s/releases/download/' % REPO
PUBLIC_KEY = 'c73020442ce697f8af26bba7d18d0b7d0ac48cba3a7829fcf31889b64c8f593c'  # ed25519 public key (hex); the private key stays on the release machine (csec)
NAME = re.compile(r'^Z1PCB-(\d+\.\d+\.\d+)-setup\.exe$')
MAX_INSTALLER = 400 * 2**20
UA = {'User-Agent': 'z1pcb-updater', 'Accept': 'application/vnd.github+json'}

_source = {'api': API, 'downloads': DOWNLOADS, 'scheme': 'https://'}


def use_test_server(api_url):
    """End-to-end test only (--update-test): take releases from a server on this machine. Signatures still apply."""
    m = re.match(r'^(http://127\.0\.0\.1:\d+/)', api_url)
    if not m:
        raise UserError('Testovací server aktualizácií musí bežať na http://127.0.0.1.')
    _source.update(api=api_url, downloads=m.group(1), scheme='http://127.0.0.1:')


def vtuple(v):
    return tuple(int(x) for x in re.findall(r'\d+', v)[:3]) or (0,)


@dataclass
class Release:
    version: str
    notes: str
    url: str  # installer download URL
    name: str
    size: int  # signed
    sha256: str  # signed
    page: str


def manifest(name, version, sha256, size):
    """The bytes that get signed: everything that identifies one installer."""
    doc = {'name': name, 'version': version, 'sha256': sha256.lower(), 'size': int(size)}
    return json.dumps(doc, sort_keys=True, separators=(',', ':')).encode()


def sign(private_key_hex, path):
    """Signature document for an installer (release machine only)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    name = os.path.basename(path)
    m = NAME.match(name)
    if not m:
        raise UserError('Inštalačka sa musí volať Z1PCB-x.y.z-setup.exe.')
    with open(path, 'rb') as f:
        data = f.read()
    doc = {'name': name, 'version': m.group(1), 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex.strip()))
    doc['sig'] = base64.b64encode(key.sign(manifest(**doc))).decode()
    return doc


def verify(raw, public_key_hex=None):
    """Check a signature document against the release key; returns its fields."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        doc = json.loads(raw)
        sig = base64.b64decode(doc['sig'], validate=True)
        body = manifest(doc['name'], doc['version'], doc['sha256'], doc['size'])
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex or PUBLIC_KEY)).verify(sig, body)
    except (InvalidSignature, KeyError, TypeError, ValueError):
        raise UserError('Podpis vydania nie je platný, aktualizácia sa neinštaluje.') from None
    return doc


def fetch(url, limit, headers=UA, timeout=8):
    if not url.startswith(_source['scheme']):
        raise UserError('Aktualizácia odkazuje na nepovolenú adresu.')
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise UserError('Odpoveď servera aktualizácií je neočakávane veľká.')
    return data


def latest():
    """Newest published release with a signed installer, or None if this build is current."""
    d = json.loads(fetch(_source['api'], 2**20))
    if d.get('draft') or d.get('prerelease'):
        return None
    version = str(d.get('tag_name', '')).lstrip('vV')
    if vtuple(version) <= vtuple(__version__):
        return None
    assets = {a.get('name'): a.get('browser_download_url', '') for a in d.get('assets', [])}
    name = 'Z1PCB-%s-setup.exe' % version
    if not NAME.match(name) or name not in assets or name + '.sig' not in assets:
        return None
    urls = (assets[name], assets[name + '.sig'])
    if not all(u.startswith(_source['downloads']) for u in urls):
        raise UserError('Aktualizácia odkazuje na nepovolenú adresu.')
    doc = verify(fetch(urls[1], 4096))
    if doc['name'] != name or doc['version'] != version or not 0 < doc['size'] <= MAX_INSTALLER:
        raise UserError('Podpis vydania patrí inému súboru, aktualizácia sa neinštaluje.')
    return Release(version, d.get('body') or '', urls[0], name, doc['size'], doc['sha256'], d.get('html_url', ''))


def download(rel, progress=None, cancelled=lambda: False):
    """Download the installer into a fresh private folder and compare it with the signed hash and size."""
    path = os.path.join(tempfile.mkdtemp(prefix='z1pcb-update-'), rel.name)
    h, done = hashlib.sha256(), 0
    req = urllib.request.Request(rel.url, headers={'User-Agent': UA['User-Agent']})
    with urllib.request.urlopen(req, timeout=30) as r, open(path, 'wb') as f:
        while done <= rel.size:
            block = r.read(1 << 16)
            if not block or cancelled():
                break
            f.write(block)
            h.update(block)
            done += len(block)
            if progress:
                progress(min(100, int(100 * done / rel.size)))
    if done != rel.size or h.hexdigest() != rel.sha256.lower():
        os.remove(path)
        raise UserError('Stiahnutá inštalačka nesedí s podpísaným odtlačkom, inštalácia zrušená.')
    return path
