
import sqlite3

DB_PATH = "memory.db"


def initialize_memory():
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                value TEXT NOT NULL
            )
        """)


def save_memory(key, value):
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute("""
            INSERT INTO memories (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (key, value))


def get_memories():
    with sqlite3.connect(DB_PATH) as connection:
        rows = connection.execute(
            "SELECT key, value FROM memories ORDER BY id"
        ).fetchall()
    return dict(rows)


def forget_memory(key):
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            "DELETE FROM memories WHERE key = ?", (key,)
        )


if __name__ == "__main__":
    initialize_memory()
    print("SQLite memory database initialized.")
    print("Saved memories:", get_memories())
