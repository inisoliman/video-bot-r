
import psycopg as psycopg2
from psycopg import sql
from psycopg.extras import DictCursor
import threading
from contextlib import contextmanager
import logging

from config.config import Config

logger = logging.getLogger(__name__)

_connection_pool = None
_pool_lock = threading.Lock()

def get_connection_pool():
    """Initializes or returns the database connection pool."""
    global _connection_pool
    if _connection_pool is None:
        with _pool_lock:
            if _connection_pool is None:
                try:
                    _connection_pool = psycopg2.pool.ThreadedConnectionPool(
                        Config.DB_POOL_MIN, Config.DB_POOL_MAX,
                        **Config.DB_CONFIG
                    )
                    logger.info(f"Database connection pool created (min={Config.DB_POOL_MIN}, max={Config.DB_POOL_MAX})")
                except Exception as e:
                    logger.critical(f"Failed to create connection pool: {e}")
                    raise
    return _connection_pool

@contextmanager
def get_db_connection():
    """Context manager to get a connection from the pool and return it after use."""
    pool = get_connection_pool()
    conn = None
    try:
        conn = pool.getconn()
        if conn:
            yield conn
        else:
            raise psycopg2.OperationalError("Could not get connection from pool")
    except Exception as e:
        logger.error(f"Database connection error: {e}", exc_info=True)
        if conn:
            pool.putconn(conn)
        raise
    finally:
        if conn:
            pool.putconn(conn)

def execute_query(query, params=None, fetch=None, commit=False):
    """Executes an SQL query using a connection from the pool.

    Args:
        query (str): The SQL query string.
        params (tuple, optional): Parameters for the query. Defaults to None.
        fetch (str, optional): 'one' to fetch one row, 'all' to fetch all rows. Defaults to None.
        commit (bool, optional): Whether to commit the transaction. Defaults to False.

    Returns:
        Any: Query result (row, list of rows, True for success, False for failure, or None).
    """
    result = None
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=DictCursor) as c:
                c.execute(query, params)
                if fetch == "one":
                    result = c.fetchone()
                elif fetch == "all":
                    result = c.fetchall()
                if commit:
                    conn.commit()
                    if fetch is None:
                        result = True
    except psycopg2.Error as e:
        logger.error(f"Database query failed. Error: {e}", exc_info=True)
        if fetch == "all":
            return []
        return None if fetch else False
    return result


# --- Schema Management ---
EXPECTED_SCHEMA = {
    'video_archive': {
        'id': 'SERIAL PRIMARY KEY',
        'message_id': 'BIGINT UNIQUE',
        'caption': 'TEXT',
        'chat_id': 'BIGINT',
        'file_name': 'TEXT',
        'file_id': 'TEXT',
        'category_id': 'INTEGER REFERENCES categories(id) ON DELETE SET NULL',
        'metadata': 'JSONB',
        'view_count': 'INTEGER DEFAULT 0',
        'upload_date': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
        'grouping_key': 'TEXT',
        'thumbnail_file_id': 'TEXT',
        'content_type': 'TEXT DEFAULT NULL'  # VIDEO or DOCUMENT
    },
    'required_channels': {
        'channel_id': 'BIGINT PRIMARY KEY',
        'channel_name': 'TEXT'
    },
    'categories': {
        'id': 'SERIAL PRIMARY KEY',
        'name': 'TEXT NOT NULL',
        'parent_id': 'INTEGER REFERENCES categories(id) ON DELETE CASCADE',
        'full_path': 'TEXT NOT NULL'
    },
    'bot_users': {
        'user_id': 'BIGINT PRIMARY KEY',
        'username': 'TEXT',
        'first_name': 'TEXT',
        'join_date': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'
    },
    'bot_settings': {
        'setting_key': 'TEXT PRIMARY KEY',
        'setting_value': 'TEXT'
    },
    'video_ratings': {
        'id': 'SERIAL PRIMARY KEY',
        'video_id': 'INTEGER REFERENCES video_archive(id) ON DELETE CASCADE',
        'user_id': 'BIGINT',
        'rating': 'INTEGER',
        '_UNIQUE_CONSTRAINT': 'UNIQUE(video_id, user_id)'
    },
    'user_states': {
        'user_id': 'BIGINT PRIMARY KEY',
        'state': 'TEXT',
        'context': 'JSONB'
    },
    'user_favorites': {
        'id': 'SERIAL PRIMARY KEY',
        'user_id': 'BIGINT',
        'video_id': 'INTEGER REFERENCES video_archive(id) ON DELETE CASCADE',
        'date_added': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
        '_UNIQUE_CONSTRAINT': 'UNIQUE(user_id, video_id)'
    },
    'user_history': {
        'id': 'SERIAL PRIMARY KEY',
        'user_id': 'BIGINT',
        'video_id': 'INTEGER REFERENCES video_archive(id) ON DELETE CASCADE',
        'last_watched': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP',
        '_UNIQUE_CONSTRAINT': 'UNIQUE(user_id, video_id)'
    },
    'video_comments': {
        'id': 'SERIAL PRIMARY KEY',
        'video_id': 'INTEGER REFERENCES video_archive(id) ON DELETE CASCADE',
        'user_id': 'BIGINT NOT NULL',
        'username': 'TEXT',
        'comment_text': 'TEXT NOT NULL',
        'admin_reply': 'TEXT',
        'is_read': 'BOOLEAN DEFAULT FALSE',
        'replied_at': 'TIMESTAMP',
        'created_at': 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'
    }
}

def verify_and_repair_schema():
    logger.info("Verifying and repairing database schema...")
    try:
        with get_db_connection() as conn:
            with conn.cursor() as c:
                for table_name, columns in EXPECTED_SCHEMA.items():
                    # Create table if not exists (minimal, then add columns)
                    create_table_query = sql.SQL("CREATE TABLE IF NOT EXISTS {table_name} (id SERIAL PRIMARY KEY)").format(
                        table_name=sql.Identifier(table_name)
                    )
                    try:
                        c.execute(create_table_query)
                    except psycopg2.ProgrammingError as e:
                        logger.warning(f"Could not create table {table_name} (might already exist or other issue): {e}")
                        
                    logger.info(f"Checking table: {table_name}")
                    for column_name, column_definition in columns.items():
                        if column_name.startswith("_") or column_name == "id":
                            continue
                            
                        c.execute("""
                            SELECT 1 FROM information_schema.columns 
                            WHERE table_name = %s AND column_name = %s
                        """, (table_name, column_name))
                        
                        if c.fetchone() is None:
                            logger.warning(f"Column '{column_name}' not found in table '{table_name}'. Adding it now.")
                            alter_query = sql.SQL("ALTER TABLE {} ADD COLUMN {} {}").format(
                                sql.Identifier(table_name),
                                sql.Identifier(column_name),
                                sql.SQL(column_definition)
                            )
                            try:
                                c.execute(alter_query)
                                logger.info(f"Successfully added column '{column_name}' to '{table_name}'.")
                            except Exception as add_err:
                                logger.error(f"Error adding column {column_name} to {table_name}: {add_err}")
                    
                    # Add UNIQUE constraints if specified
                    if '_UNIQUE_CONSTRAINT' in columns:
                        constraint_definition = columns['_UNIQUE_CONSTRAINT']
                        constraint_name = f"{table_name}_unique_constraint"
                        
                        c.execute("""
                            SELECT 1 FROM information_schema.table_constraints 
                            WHERE table_name = %s AND constraint_name = %s
                        """, (table_name, constraint_name))
                        if c.fetchone() is None:
                            logger.info(f"Adding UNIQUE constraint {constraint_name} to {table_name}")
                            try:
                                c.execute(sql.SQL(f"ALTER TABLE {table_name} ADD CONSTRAINT {constraint_name} {constraint_definition}"))
                            except Exception as const_err:
                                logger.error(f"Error adding constraint {constraint_name} to {table_name}: {const_err}")

                conn.commit()
                logger.info("Schema verification and repair process completed successfully.")
    except psycopg2.Error as e:
        logger.error(f"Schema verification error: {e}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Unexpected error in schema verification: {e}", exc_info=True)
        raise
