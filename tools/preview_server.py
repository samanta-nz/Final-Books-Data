#!/usr/bin/env python3
"""Tiny static file server with HTTP Range support (needed for <audio> seeking).

    python tools/preview_server.py G09/G09-Math/F01/mobile 8099
"""
import os
import re
import sys
import http.server
import socketserver
import mimetypes

mimetypes.add_type('audio/ogg', '.ogg')


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path in ('/', '/index.html') and os.path.exists('index.html'):
            pass
        rng = self.headers.get('Range')
        path = self.translate_path(self.path)
        if not rng or not os.path.isfile(path):
            return super().do_GET()
        size = os.path.getsize(path)
        m = re.match(r'bytes=(\d*)-(\d*)', rng)
        if not m:
            return super().do_GET()
        a = int(m.group(1)) if m.group(1) else 0
        b = int(m.group(2)) if m.group(2) else size - 1
        b = min(b, size - 1)
        self.send_response(206)
        self.send_header('Content-Type', self.guess_type(path))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Range', f'bytes {a}-{b}/{size}')
        self.send_header('Content-Length', str(b - a + 1))
        self.end_headers()
        with open(path, 'rb') as f:
            f.seek(a)
            left = b - a + 1
            while left > 0:
                chunk = f.read(min(65536, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)


if __name__ == '__main__':
    root = sys.argv[1] if len(sys.argv) > 1 else '.'
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8099
    os.chdir(root)
    Handler.extensions_map.update({'.json': 'application/json', '.vtt': 'text/vtt'})
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(('0.0.0.0', port), Handler) as s:
        print(f'serving {os.getcwd()} on http://0.0.0.0:{port}')
        s.serve_forever()
