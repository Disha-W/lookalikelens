"""Demo fixture: a fake "lookalike bakery" site that exists only inside Docker.

Serves a static page (a pretend butterpaperbakes look-alike) plus one helper
endpoint used by the safety tests:

  /redirect?to=<url>   replies 302 to <url>, so tests can prove the collector
                       refuses redirects that point at private addresses.

This container sits on the collector's private compose network and is never
published to the host or the internet. It is a controlled target, the same
idea as the marked sample data everywhere else in this project.
"""
import http.server
import urllib.parse

PORT = 8000


class FixtureHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/redirect"):
            target = urllib.parse.parse_qs(
                urllib.parse.urlparse(self.path).query).get("to", [""])[0]
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()
            return
        super().do_GET()

    def log_message(self, *args):
        pass  # keep test output readable


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("0.0.0.0", PORT), FixtureHandler).serve_forever()
