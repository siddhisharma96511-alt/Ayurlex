-- Optional reference. The app creates all of this automatically on start-up.
CREATE DATABASE IF NOT EXISTS ayurlex CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE ayurlex;
-- tables: users, chats, messages (+ FULLTEXT index on content), feedback  -> see db.py (SCHEMA)
