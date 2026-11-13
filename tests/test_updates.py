"""Updates: only an installer whose name, version, size and hash carry our signature is accepted."""

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from cryptography.hazmat.primitives import serialization as ser
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from z1pcb import updates
from z1pcb.errors import UserError

NEW = '99.0.0'
NAME = 'Z1PCB-%s-setup.exe' % NEW
BODY = b'MZ' + bytes(range(256)) * 40


@pytest.fixture
def key(monkeypatch):
    k = Ed25519PrivateKey.generate()
    priv = k.private_bytes(ser.Encoding.Raw, ser.PrivateFormat.Raw, ser.NoEncryption()).hex()
    pub = k.public_key().public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw).hex()
    monkeypatch.setattr(updates, 'PUBLIC_KEY', pub)
    return priv


@pytest.fixture
def installer(tmp_path):
    p = tmp_path / NAME
    p.write_bytes(BODY)
    return p


def api(sig_doc, name=NAME, tag='v' + NEW, base=updates.DOWNLOADS, **extra):
    assets = [{'name': name, 'browser_download_url': base + tag + '/' + name}]
    if sig_doc is not None:
        assets.append({'name': name + '.sig', 'browser_download_url': base + tag + '/' + name + '.sig'})
    return dict({'tag_name': tag, 'assets': assets, 'body': 'notes', 'html_url': 'https://github.com/x'}, **extra)


def serve(monkeypatch, release, sig_doc):
    def fetch(url, limit, **_):
        return json.dumps(sig_doc if url.endswith('.sig') else release).encode()

    monkeypatch.setattr(updates, 'fetch', fetch)


def test_version_order():
    assert updates.vtuple('v0.10.0') > updates.vtuple('0.9.9') and updates.vtuple('1.2') < updates.vtuple('1.2.1')


def test_signed_release_is_offered(monkeypatch, key, installer):
    doc = updates.sign(key, installer)
    serve(monkeypatch, api(doc), doc)
    rel = updates.latest()
    assert (rel.version, rel.name, rel.size) == (NEW, NAME, len(BODY))
    assert rel.sha256 == hashlib.sha256(BODY).hexdigest() and rel.url.startswith(updates.DOWNLOADS)


def test_release_signed_with_another_key_is_refused(monkeypatch, key, installer):
    other = Ed25519PrivateKey.generate().private_bytes(ser.Encoding.Raw, ser.PrivateFormat.Raw, ser.NoEncryption())
    doc = updates.sign(other.hex(), installer)
    serve(monkeypatch, api(doc), doc)
    with pytest.raises(UserError, match='Podpis vydania nie je platný'):
        updates.latest()


@pytest.mark.parametrize('field, value', [('sha256', '0' * 64), ('size', 1), ('version', '99.0.1'), ('name', 'x.exe')])
def test_changed_signed_field_is_refused(monkeypatch, key, installer, field, value):
    doc = dict(updates.sign(key, installer), **{field: value})
    serve(monkeypatch, api(doc), doc)
    with pytest.raises(UserError):
        updates.latest()


def test_signature_of_an_older_installer_cannot_be_replayed(monkeypatch, key, tmp_path):
    old = tmp_path / 'Z1PCB-98.0.0-setup.exe'
    old.write_bytes(BODY)
    doc = updates.sign(key, old)  # valid signature, but for another version
    serve(monkeypatch, api(doc), doc)
    with pytest.raises(UserError, match='inému súboru'):
        updates.latest()


def test_download_from_another_site_is_refused(monkeypatch, key, installer):
    doc = updates.sign(key, installer)
    serve(monkeypatch, api(doc, base='https://evil.example/'), doc)
    with pytest.raises(UserError, match='nepovolenú adresu'):
        updates.latest()


def test_nothing_offered(monkeypatch, key, installer):
    doc = updates.sign(key, installer)
    serve(monkeypatch, api(doc, tag='v0.0.1', name='Z1PCB-0.0.1-setup.exe'), doc)
    assert updates.latest() is None  # not newer
    serve(monkeypatch, api(None), doc)
    assert updates.latest() is None  # no signature published
    serve(monkeypatch, api(doc, prerelease=True), doc)
    assert updates.latest() is None


def test_embedded_key_is_the_release_key():
    assert len(bytes.fromhex(updates.PUBLIC_KEY)) == 32
    stranger = Ed25519PrivateKey.generate().private_bytes(ser.Encoding.Raw, ser.PrivateFormat.Raw, ser.NoEncryption())
    body = updates.manifest(NAME, NEW, '0' * 64, 1)
    doc = {'name': NAME, 'version': NEW, 'sha256': '0' * 64, 'size': 1}
    import base64

    doc['sig'] = base64.b64encode(Ed25519PrivateKey.from_private_bytes(stranger).sign(body)).decode()
    with pytest.raises(UserError):
        updates.verify(json.dumps(doc))


def test_only_a_local_test_server_is_allowed():
    with pytest.raises(UserError):
        updates.use_test_server('http://example.com/latest')
    with pytest.raises(UserError):
        updates.use_test_server('https://127.0.0.1.evil.example/latest')


@pytest.fixture
def local_server(monkeypatch):
    served = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            data = served.get(self.path)
            self.send_response(200 if data is not None else 404)
            self.send_header('Content-Length', str(len(data or b'')))
            self.end_headers()
            self.wfile.write(data or b'')

        def log_message(self, *a):
            pass

    srv = HTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(updates, '_source', dict(updates._source))
    updates.use_test_server('http://127.0.0.1:%d/latest' % srv.server_port)
    yield served, 'http://127.0.0.1:%d/' % srv.server_port
    srv.shutdown()


def test_download_checks_signed_hash_and_size(key, installer, local_server):
    served, base = local_server
    doc = updates.sign(key, installer)
    served['/latest'] = json.dumps(api(doc, base=base)).encode()
    served['/v%s/%s.sig' % (NEW, NAME)] = json.dumps(doc).encode()
    served['/v%s/%s' % (NEW, NAME)] = BODY
    rel = updates.latest()
    path = updates.download(rel)
    assert open(path, 'rb').read() == BODY
    for tampered in (BODY[:-1] + b'X', BODY + b'extra', BODY[:100]):
        served['/v%s/%s' % (NEW, NAME)] = tampered
        with pytest.raises(UserError, match='nesedí'):
            updates.download(rel)
