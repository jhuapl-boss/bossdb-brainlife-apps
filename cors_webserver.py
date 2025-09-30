#!/usr/bin/env python
# @license
# Copyright 2017 Google Inc.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Simple web server serving local files that permits cross-origin requests.

This can be used to view local data with Neuroglancer.

WARNING: Because this web server permits cross-origin requests, it exposes any
data in the directory that is served to any web page running on a machine that
can connect to the web server.
"""

import argparse
import os
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
import mimetypes


class RequestHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        SimpleHTTPRequestHandler.end_headers(self)

    def send_head(self):
        """Serve .gz transparently if available."""
        path = self.translate_path(self.path)

        # If normal path doesn’t exist, try path.gz
        if not os.path.exists(path) and os.path.exists(path + ".gz"):
            gz_path = path + ".gz"
            ctype = self.guess_type(path)  # Use original path for MIME type
            try:
                f = open(gz_path, "rb")
            except OSError:
                self.send_error(404, "File not found")
                return None

            fs = os.fstat(f.fileno())
            self.send_response(200)
            self.send_header("Content-type", ctype)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(fs[6]))
            self.send_header("Last-Modified", self.date_time_string(fs.st_mtime))
            self.end_headers()
            return f

        # Otherwise, normal behavior
        return super().send_head()


class Server(HTTPServer):
    protocol_version = "HTTP/1.1"

    def __init__(self, server_address):
        HTTPServer.__init__(self, server_address, RequestHandler)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "-p", "--port", type=int, default=10000, help="TCP port to listen on"
    )
    ap.add_argument("-a", "--bind", default="127.0.0.1", help="Bind address")
    ap.add_argument("-d", "--directory", default=".", help="Directory to serve")

    args = ap.parse_args()
    os.chdir(args.directory)
    server = Server((args.bind, args.port))
    sa = server.socket.getsockname()
    print("Serving directory %s at http://%s:%d" % (os.getcwd(), sa[0], sa[1]))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
        sys.exit(0)
