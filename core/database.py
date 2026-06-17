import sqlite3
import time
from typing import List, Dict, Optional

class Database:
    def __init__(self, db_path="translation.db"):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        c = conn.cursor()

        c.execute('''CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            source_lang TEXT, target_lang TEXT,
            input_srt TEXT, output_srt TEXT,
            status TEXT DEFAULT 'pending',
            total_lines INTEGER DEFAULT 0,
            window_size INTEGER DEFAULT 8,
            history_size INTEGER DEFAULT 8,
            future_size INTEGER DEFAULT 2,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

        c.execute('''CREATE TABLE IF NOT EXISTS subtitle_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER, sub_index INTEGER,
            start_time TEXT, end_time TEXT,
            original_text TEXT, translated_text TEXT,
            status TEXT DEFAULT 'pending',
            FOREIGN KEY(project_id) REFERENCES projects(id))''')

        c.execute('''CREATE TABLE IF NOT EXISTS windows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER, window_index INTEGER,
            start_sub_index INTEGER, end_sub_index INTEGER,
            start_pos INTEGER, end_pos INTEGER,
            original_text TEXT, translated_text TEXT,
            status TEXT DEFAULT 'pending',
            retry_count INTEGER DEFAULT 0,
            error_message TEXT,
            processing_started_at REAL,
            UNIQUE(project_id, window_index))''')

        c.execute('''CREATE TABLE IF NOT EXISTS translation_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_lang TEXT, target_lang TEXT,
            source_text TEXT, target_text TEXT,
            usage_count INTEGER DEFAULT 1,
            confidence REAL DEFAULT 1.0,
            char_count INTEGER DEFAULT 0,
            domain TEXT DEFAULT 'global',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_lang, target_lang, source_text, domain))''')

        c.execute('''CREATE TABLE IF NOT EXISTS dead_letter_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER, window_id INTEGER, window_index INTEGER,
            error_message TEXT, original_text TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(project_id) REFERENCES projects(id))''')

        c.execute('''CREATE TABLE IF NOT EXISTS glossary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER, source_term TEXT, target_term TEXT, context_hint TEXT,
            FOREIGN KEY(project_id) REFERENCES projects(id))''')

        c.execute('CREATE INDEX IF NOT EXISTS idx_subtitle_project ON subtitle_items(project_id, sub_index)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_window_project ON windows(project_id, window_index)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_tm_lookup ON translation_memory(source_lang, target_lang, source_text, domain)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_window_status ON windows(project_id, status)')
        conn.commit()
        conn.close()

    # --- Projects ---
    def create_project(self, name, source_lang, target_lang, input_srt, output_srt,
                       window_size=8, history_size=8, future_size=2):
        with self._get_connection() as conn:
            c = conn.cursor()
            c.execute('''INSERT INTO projects (name, source_lang, target_lang, input_srt, output_srt,
                          window_size, history_size, future_size) VALUES (?,?,?,?,?,?,?,?)''',
                      (name, source_lang, target_lang, input_srt, output_srt, window_size, history_size, future_size))
            conn.commit()
            return c.lastrowid

    def get_project(self, project_id):
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            return dict(row) if row else None

    def get_project_by_name(self, name):
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM projects WHERE name=?", (name,)).fetchone()
            return dict(row) if row else None

    def update_project_status(self, project_id, status, total_lines=None):
        with self._get_connection() as conn:
            if total_lines is not None:
                conn.execute("UPDATE projects SET status=?, total_lines=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                             (status, total_lines, project_id))
            else:
                conn.execute("UPDATE projects SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, project_id))
            conn.commit()

    # --- Subtitle items ---
    def save_subtitle_items(self, project_id, items):
        with self._get_connection() as conn:
            c = conn.cursor()
            for it in items:
                c.execute("INSERT INTO subtitle_items (project_id, sub_index, start_time, end_time, original_text, status) VALUES (?,?,?,?,?,'pending')",
                          (project_id, it['index'], it['start'], it['end'], it['text']))
            conn.commit()

    def get_all_items(self, project_id):
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM subtitle_items WHERE project_id=? ORDER BY sub_index", (project_id,)).fetchall()
            return [dict(r) for r in rows]

    def get_items_by_sub_range(self, project_id, start_sub, end_sub):
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM subtitle_items WHERE project_id=? AND sub_index BETWEEN ? AND ? ORDER BY sub_index",
                                (project_id, start_sub, end_sub)).fetchall()
            return [dict(r) for r in rows]

    # --- Windows ---
    def save_window(self, project_id, window_index, start_sub, end_sub, start_pos, end_pos, original_text):
        with self._get_connection() as conn:
            conn.execute('''INSERT INTO windows (project_id, window_index, start_sub_index, end_sub_index,
                            start_pos, end_pos, original_text) VALUES (?,?,?,?,?,?,?)
                            ON CONFLICT(project_id, window_index) DO UPDATE SET
                            start_sub_index=excluded.start_sub_index, end_sub_index=excluded.end_sub_index,
                            start_pos=excluded.start_pos, end_pos=excluded.end_pos,
                            original_text=excluded.original_text''',
                         (project_id, window_index, start_sub, end_sub, start_pos, end_pos, original_text))
            conn.commit()
            return conn.execute("SELECT id FROM windows WHERE project_id=? AND window_index=?", (project_id, window_index)).fetchone()['id']

    def claim_task(self, project_id):
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute('''SELECT * FROM windows WHERE project_id=? AND status IN ('pending','failed')
                                      AND retry_count < 3 ORDER BY window_index LIMIT 1''', (project_id,)).fetchone()
                if not row:
                    conn.rollback()
                    return None
                now = time.time()
                conn.execute("UPDATE windows SET status='processing', processing_started_at=? WHERE id=?", (now, row['id']))
                conn.commit()
                return dict(row)
            except:
                conn.rollback()
                raise

    def commit_window(self, project_id, window_id, start_sub, end_sub, translations):
        """
        Gộp update_item_translation_bulk và mark_task_done trong 1 transaction.
        """
        with self._get_connection() as conn:
            conn.execute("BEGIN")
            try:
                c = conn.cursor()
                # update items
                rows = c.execute('''SELECT id, sub_index FROM subtitle_items
                                    WHERE project_id=? AND sub_index BETWEEN ? AND ? ORDER BY sub_index''',
                                 (project_id, start_sub, end_sub)).fetchall()
                if len(rows) != len(translations):
                    raise ValueError("Translation count mismatch")
                updates = [(trans, 'translated', r['id']) for r, trans in zip(rows, translations)]
                c.executemany("UPDATE subtitle_items SET translated_text=?, status=? WHERE id=?", updates)
                # mark window done
                c.execute("UPDATE windows SET translated_text=?, status='completed', processing_started_at=NULL WHERE id=?",
                          ("\n".join(translations), window_id))
                conn.commit()
            except:
                conn.rollback()
                raise

    def mark_task_failed(self, window_id, error_message, retry_count_increment=True):
        with self._get_connection() as conn:
            if retry_count_increment:
                conn.execute('''UPDATE windows SET status='failed', error_message=?, retry_count=retry_count+1,
                                processing_started_at=NULL WHERE id=?''', (error_message, window_id))
            else:
                conn.execute('''UPDATE windows SET status='failed', error_message=?, processing_started_at=NULL WHERE id=?''',
                             (error_message, window_id))
            conn.commit()

    def mark_task_dead(self, window_id, error_message):
        with self._get_connection() as conn:
            row = conn.execute("SELECT project_id, window_index, original_text FROM windows WHERE id=?", (window_id,)).fetchone()
            if row:
                conn.execute('''INSERT INTO dead_letter_queue (project_id, window_id, window_index, error_message, original_text)
                                VALUES (?,?,?,?,?)''', (row['project_id'], window_id, row['window_index'], error_message, row['original_text']))
                conn.execute("UPDATE windows SET status='dead_letter', processing_started_at=NULL WHERE id=?", (window_id,))
                conn.commit()

    def count_pending_windows(self, project_id):
        with self._get_connection() as conn:
            return conn.execute('''SELECT COUNT(*) FROM windows WHERE project_id=? AND status IN ('pending','processing','failed') AND retry_count<3''',
                                (project_id,)).fetchone()[0]

    def count_dead_letter(self, project_id):
        with self._get_connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM dead_letter_queue WHERE project_id=?", (project_id,)).fetchone()[0]

    def recover_stuck_tasks(self, project_id, timeout_seconds=600):
        threshold = time.time() - timeout_seconds
        with self._get_connection() as conn:
            conn.execute('''UPDATE windows SET status='pending', processing_started_at=NULL
                            WHERE project_id=? AND status='processing'
                            AND processing_started_at IS NOT NULL AND processing_started_at < ?''',
                         (project_id, threshold))
            conn.commit()

    # --- Translation Memory (ngưỡng thấp để bắt câu ngắn) ---
    def get_translation_memory(self, source_lang, target_lang, source_text, domain=None, min_char_count=4):
        if len(source_text) < min_char_count:
            return None
        with self._get_connection() as conn:
            c = conn.cursor()
            if domain:
                row = c.execute('''SELECT target_text, usage_count FROM translation_memory
                                   WHERE source_lang=? AND target_lang=? AND source_text=? AND domain=?''',
                                (source_lang, target_lang, source_text, domain)).fetchone()
                if row:
                    c.execute('''UPDATE translation_memory SET usage_count=?, last_used=CURRENT_TIMESTAMP
                                 WHERE source_lang=? AND target_lang=? AND source_text=? AND domain=?''',
                              (row['usage_count']+1, source_lang, target_lang, source_text, domain))
                    conn.commit()
                    return row['target_text']
            # fallback global
            row = c.execute('''SELECT target_text, usage_count FROM translation_memory
                               WHERE source_lang=? AND target_lang=? AND source_text=? AND domain='global' ''',
                            (source_lang, target_lang, source_text)).fetchone()
            if row:
                c.execute('''UPDATE translation_memory SET usage_count=?, last_used=CURRENT_TIMESTAMP
                             WHERE source_lang=? AND target_lang=? AND source_text=? AND domain='global' ''',
                          (row['usage_count']+1, source_lang, target_lang, source_text))
                conn.commit()
                return row['target_text']
            return None

    def save_translation_memory(self, source_lang, target_lang, source_text, target_text, domain='global', confidence=1.0, min_char_count=4):
        if len(source_text) < min_char_count:
            return
        with self._get_connection() as conn:
            conn.execute('''INSERT INTO translation_memory (source_lang, target_lang, source_text, target_text, confidence, char_count, domain)
                            VALUES (?,?,?,?,?,?,?)
                            ON CONFLICT(source_lang, target_lang, source_text, domain) DO UPDATE SET
                            target_text=excluded.target_text,
                            confidence=(confidence+excluded.confidence)/2,
                            usage_count=usage_count+1,
                            last_used=CURRENT_TIMESTAMP''',
                         (source_lang, target_lang, source_text, target_text, confidence, len(source_text), domain))
            conn.commit()

    # --- Glossary ---
    def get_glossary(self, project_id):
        with self._get_connection() as conn:
            return [dict(r) for r in conn.execute("SELECT source_term, target_term, context_hint FROM glossary WHERE project_id=?", (project_id,)).fetchall()]

    def add_glossary_term(self, project_id, source, target, hint=''):
        with self._get_connection() as conn:
            conn.execute("INSERT INTO glossary (project_id, source_term, target_term, context_hint) VALUES (?,?,?,?)",
                         (project_id, source, target, hint))
            conn.commit()