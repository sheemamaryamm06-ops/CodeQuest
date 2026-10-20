                    connection.execute(
                        "INSERT INTO players (username, password_hash, password_salt, created_at) "
                        "VALUES (?, ?, ?, ?)",
                        (username, password_hash, salt, int(time.time())),
                    )
            except sqlite3.IntegrityError:
                self.respond_json(409, {"error": "That username is already taken."})
                return
            self.respond_json(201, {"message": "Account created."})
            return

        if path == "/api/login":
            username = str(data.get("username", "")).strip()
            password = data.get("password", "")
            with connect_database() as connection:
                player = connection.execute(
                    "SELECT id, password_hash, password_salt FROM players WHERE username = ?",
                    (username,),
                ).fetchone()
                if not player or not isinstance(password, str):
                    self.respond_json(401, {"error": "Incorrect username or password."})
                    return
                _, password_hash = hash_password(password, player["password_salt"])
                if not secrets.compare_digest(password_hash, player["password_hash"]):
                    self.respond_json(401, {"error": "Incorrect username or password."})
                    return
                cookie = self.issue_session(player["id"], connection)
                state = player_state(connection, player["id"])
            self.respond_json(200, state, [cookie])
            return

        if path == "/api/language":
            with connect_database() as connection:
                player_id = self.session_player_id(connection)
                if player_id is None:
                    self.respond_json(401, {"error": "Please log in to continue."})
                    return
                language = data.get("language")
                if language not in LANGUAGES:
                    self.respond_json(400, {"error": "Choose a supported language."})
                    return
                connection.execute(
                    "UPDATE players SET language = ? WHERE id = ?", (language, player_id)
                )
                state = player_state(connection, player_id, language=language)
            self.respond_json(200, state)
            return

        if path == "/api/complete-level":
            with connect_database() as connection:
                player_id = self.session_player_id(connection)
                if player_id is None:
                    self.respond_json(401, {"error": "Please log in to continue."})
                    return
                player = connection.execute(
                    "SELECT language FROM players WHERE id = ?", (player_id,)
                ).fetchone()
                selected_language = data.get("language") or (player["language"] if player else None) or "Python"
                if selected_language not in LANGUAGES:
                    self.respond_json(400, {"error": "Choose a supported language."})
                    return
                level = data.get("level")
                if not isinstance(level, int) or isinstance(level, bool) or not 1 <= level <= 6:
                    self.respond_json(400, {"error": "Level must be between 1 and 6."})
                    return
                completed = {
                    row["level"]
                    for row in connection.execute(
                        "SELECT level FROM language_progress WHERE player_id = ? AND language = ? ORDER BY level",
                        (player_id, selected_language),
                    )
                }
                next_level = min(len(completed) + 1, 6)
                if level not in completed and level != next_level:
                    self.respond_json(403, {"error": "Complete the previous level first."})
                    return
                connection.execute(
                    "INSERT OR IGNORE INTO language_progress (player_id, language, level, completed_at) VALUES (?, ?, ?, ?)",
                    (player_id, selected_language, level, int(time.time())),
                )
                state = player_state(connection, player_id, language=selected_language)
            self.respond_json(200, state)
            return

        self.respond_json(404, {"error": "Endpoint not found."})

    def log_message(self, format, *args):
        if os.environ.get("CODEQUEST_ACCESS_LOG") == "1":
            super().log_message(format, *args)



def main():
    initialize_database()
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), CodeQuestHandler)
    print(f"CodeQuest server running on port {port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping CodeQuest.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

