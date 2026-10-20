import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import server


class BackendApiTests(unittest.TestCase):
    def setUp(self):
        self.database_directory = tempfile.TemporaryDirectory()
        self.original_database_path = server.DATABASE_PATH
        server.DATABASE_PATH = Path(self.database_directory.name) / "test.sqlite3"
        server.initialize_database()
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.CodeQuestHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.connection = http.client.HTTPConnection(*self.httpd.server_address)
        self.cookie = None

    def tearDown(self):
        self.connection.close()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        server.DATABASE_PATH = self.original_database_path
        self.database_directory.cleanup()

    def request(self, method, path, payload=None):
        headers = {}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload)
        if self.cookie:
            headers["Cookie"] = self.cookie
        self.connection.request(method, path, body=body, headers=headers)
        response = self.connection.getresponse()
        content = response.read()
        data = json.loads(content) if content else None
        return response.status, data, response.getheader("Set-Cookie")

    def test_account_progress_and_session_lifecycle(self):
        status, _, _ = self.request(
            "POST", "/api/register", {"username": "PlayerOne", "password": "questpass123"}
        )
        self.assertEqual(status, 201)

        status, state, cookie = self.request(
            "POST", "/api/login", {"username": "playerone", "password": "questpass123"}
        )
        self.assertEqual(status, 200)
        self.cookie = cookie.split(";", 1)[0]
        self.assertEqual(state["xp"], 0)

        status, _, _ = self.request("POST", "/api/complete-level", {"level": 2})
        self.assertEqual(status, 403)
        status, state, _ = self.request("POST", "/api/complete-level", {"level": 1})
        self.assertEqual(status, 200)
        self.assertEqual(state["completed_levels"], [1])
        self.assertEqual(state["xp"], 100)

        status, state, _ = self.request("GET", "/api/me")
        self.assertEqual(status, 200)
        self.assertEqual(state["xp"], 100)

        status, _, _ = self.request("GET", "/logout")
        self.assertEqual(status, 303)
        self.cookie = None
        status, _, _ = self.request("GET", "/api/me")
        self.assertEqual(status, 401)

    def test_language_specific_progress(self):
        self.request("POST", "/api/register", {"username": "LangPlayer", "password": "questpass123"})
        status, state, cookie = self.request(
            "POST", "/api/login", {"username": "langplayer", "password": "questpass123"}
        )
        self.assertEqual(status, 200)
        self.cookie = cookie.split(";", 1)[0]

        status, state, _ = self.request("POST", "/api/language", {"language": "Python"})
        self.assertEqual(status, 200)
        status, state, _ = self.request("POST", "/api/complete-level", {"level": 1})
        self.assertEqual(status, 200)
        self.assertEqual(state["completed_levels"], [1])
        self.assertEqual(state["language_progress"]["Python"], [1])
        self.assertEqual(state["language_progress"]["C"], [])

        status, state, _ = self.request("POST", "/api/language", {"language": "C"})
        self.assertEqual(status, 200)
        self.assertEqual(state["completed_levels"], [])
        self.assertEqual(state["language_progress"]["Python"], [1])
        self.assertEqual(state["language_progress"]["C"], [])

        status, state, _ = self.request("POST", "/api/complete-level", {"level": 1})
        self.assertEqual(status, 200)
        self.assertEqual(state["completed_levels"], [1])
        self.assertEqual(state["language_progress"]["C"], [1])
        self.assertEqual(state["language_progress"]["Python"], [1])


if __name__ == "__main__":
    unittest.main()
