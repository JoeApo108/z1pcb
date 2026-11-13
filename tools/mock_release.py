"""Serve one installer and its .sig the way GitHub Releases does, for the update end-to-end test.
Usage: mock_release.py <Z1PCB-x.y.z-setup.exe> <port>   (the .sig must lie next to the installer)"""

import http.server
import json
import os
import sys

exe, port = sys.argv[1], int(sys.argv[2])
name = os.path.basename(exe)
version = name.split('-')[1]
base = 'http://127.0.0.1:%d/' % port
files = {'/' + name: open(exe, 'rb').read(), '/' + name + '.sig': open(exe + '.sig', 'rb').read()}
release = {
    'tag_name': 'v' + version,
    'draft': False,
    'prerelease': False,
    'body': 'Test release %s' % version,
    'html_url': base + 'release',
    'assets': [{'name': n[1:], 'browser_download_url': base + n[1:]} for n in files],
}
files['/latest'] = json.dumps(release).encode()


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        data = files.get(self.path)
        if data is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


http.server.HTTPServer(('127.0.0.1', port), Handler).serve_forever()
