"""
Hybrid Storage Architecture - Separate optimized tables for each content type.

This module implements a hybrid approach where different content types 
(Gmail, Notion databases, etc.) have their own optimized table schemas
while maintaining a unified query interface.
"""
import sqlite3
import os
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any, Union
from pathlib import Path

logger = logging.getLogger(__name__)

class HybridContentRegistry:
    """Hybrid storage system with separate tables for each content type."""
    
    def __init__(self, db_path: str = None):
        if db_path is None:
            from promaia.utils.env_writer import get_db_path
            db_path = str(get_db_path())
        self.db_path = db_path
        self.init_database()
        self._migrate_add_cc_recipients()
        self._migrate_add_attachments()
        self._migrate_add_property_ids()
        self._migrate_add_select_options_table()
        self._migrate_add_relations_table()
    
    def init_database(self):
        """Initialize the hybrid database with separate tables for each content type."""
        # Ensure directory exists
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Create Gmail-specific table with optimized schema for individual messages
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS gmail_content (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    page_id TEXT UNIQUE NOT NULL,  -- Individual message ID
                    workspace TEXT NOT NULL,
                    database_id TEXT NOT NULL,  -- Immutable database identifier
                    file_path TEXT NOT NULL,
                    
                    -- Gmail-specific fields for individual messages
                    subject TEXT,
                    sender_email TEXT,
                    sender_name TEXT,
                    recipient_emails TEXT, -- JSON array
                    cc_recipients TEXT, -- JSON array of CC recipients
                    gmail_labels TEXT, -- JSON array
                    thread_id TEXT NOT NULL,  -- Links messages in same conversation
                    message_id TEXT UNIQUE NOT NULL,  -- Gmail's unique message identifier
                    has_attachments BOOLEAN DEFAULT FALSE,
                    is_unread BOOLEAN DEFAULT FALSE,
                    body_snippet TEXT,
                    message_content TEXT,  -- Full message content (extracted, not quoted)
                    
                    -- Message position in thread
                    thread_position INTEGER DEFAULT 0,  -- 0 = first message, 1 = second, etc.
                    is_latest_in_thread BOOLEAN DEFAULT FALSE,  -- TRUE for the most recent message in thread
                    
                    -- Common timestamp fields (properly typed)
                    email_date TEXT, -- Gmail's original message date
                    created_time TEXT,
                    last_edited_time TEXT,  -- For threads, this is the latest message date
                    synced_time TEXT NOT NULL,
                    
                    -- File metadata
                    file_size INTEGER,
                    checksum TEXT,
                    
                    UNIQUE(page_id),
                    UNIQUE(message_id)
                )
            """)
            
            # Create Notion Journal table with optimized schema
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS notion_journal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    page_id TEXT UNIQUE NOT NULL,
                    workspace TEXT NOT NULL,
                    database_id TEXT NOT NULL,  -- Immutable database identifier
                    database_name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    
                    -- Journal-specific fields
                    title TEXT,
                    status TEXT, -- Published, Draft, etc.
                    date_value TEXT, -- The "Date" property
                    tags TEXT, -- JSON array
                    featured BOOLEAN DEFAULT FALSE,
                    author_name TEXT,
                    
                    -- Common timestamp fields
                    created_time TEXT,
                    last_edited_time TEXT,
                    synced_time TEXT NOT NULL,
                    
                    -- File metadata
                    file_size INTEGER,
                    checksum TEXT,
                    
                    UNIQUE(page_id)
                )
            """)
            
            # Create Notion Stories table with optimized schema
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS notion_stories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    page_id TEXT UNIQUE NOT NULL,
                    workspace TEXT NOT NULL,
                    database_id TEXT NOT NULL,  -- Immutable database identifier
                    database_name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    
                    -- Stories-specific fields
                    title TEXT,
                    status TEXT, -- Done, In Progress, Backlog, etc.
                    epic_relation TEXT, -- Related epic page_id
                    author_name TEXT,
                    story_points INTEGER,
                    priority TEXT,
                    labels TEXT, -- JSON array
                    
                    -- Common timestamp fields
                    created_time TEXT,
                    last_edited_time TEXT,
                    synced_time TEXT NOT NULL,
                    
                    -- File metadata
                    file_size INTEGER,
                    checksum TEXT,
                    
                    UNIQUE(page_id)
                )
            """)
            
            # Create CMS content table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS notion_cms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    page_id TEXT UNIQUE NOT NULL,
                    workspace TEXT NOT NULL,
                    database_id TEXT NOT NULL,  -- Immutable database identifier
                    database_name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    
                    -- CMS-specific fields
                    title TEXT,
                    status TEXT,
                    category TEXT,
                    featured BOOLEAN DEFAULT FALSE,
                    author_name TEXT,
                    slug TEXT,
                    meta_description TEXT,
                    tags TEXT, -- JSON array
                    publish_date TEXT,
                    
                    -- Common timestamp fields
                    created_time TEXT,
                    last_edited_time TEXT,
                    synced_time TEXT NOT NULL,
                    
                    -- File metadata
                    file_size INTEGER,
                    checksum TEXT,
                    
                    UNIQUE(page_id)
                )
            """)
            
            # Create conversation content table for chat history
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS conversation_content (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    page_id TEXT UNIQUE NOT NULL,  -- Thread ID
                    workspace TEXT NOT NULL,
                    database_id TEXT NOT NULL,  -- Immutable database identifier
                    file_path TEXT NOT NULL,

                    -- Conversation-specific fields
                    thread_id TEXT UNIQUE NOT NULL,
                    thread_name TEXT,
                    message_count INTEGER DEFAULT 0,
                    context_type TEXT,  -- 'general', 'sql_query', 'search', etc.
                    sql_query_prompt TEXT,  -- If natural language query

                    -- Common timestamp fields
                    created_time TEXT,
                    last_edited_time TEXT,
                    synced_time TEXT NOT NULL,

                    -- File metadata
                    file_size INTEGER,
                    checksum TEXT,

                    UNIQUE(page_id),
                    UNIQUE(thread_id)
                )
            """)

            # Create generic content table for unknown/new content types
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS generic_content (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    page_id TEXT UNIQUE NOT NULL,
                    workspace TEXT NOT NULL,
                    database_id TEXT NOT NULL,  -- Immutable database identifier
                    database_name TEXT NOT NULL,
                    content_type TEXT NOT NULL, -- 'awakenings', 'cpj', etc.
                    file_path TEXT NOT NULL,

                    -- Basic fields
                    title TEXT,

                    -- Common timestamp fields
                    created_time TEXT,
                    last_edited_time TEXT,
                    synced_time TEXT NOT NULL,

                    -- File metadata
                    file_size INTEGER,
                    checksum TEXT,

                    -- Flexible metadata for unknown properties
                    metadata TEXT, -- JSON string for properties that don't fit above

                    UNIQUE(page_id)
                )
            """)
            
            # Create notion page chunks table for large page handling
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS notion_page_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    page_id TEXT NOT NULL,
                    chunk_id TEXT UNIQUE NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    total_chunks INTEGER NOT NULL,
                    workspace TEXT NOT NULL,
                    database_name TEXT NOT NULL,

                    -- Chunk boundaries
                    char_start INTEGER,
                    char_end INTEGER,
                    estimated_tokens INTEGER,

                    -- Date-based chunking metadata
                    date_boundary TEXT,  -- YYYY-MM-DD if split by date

                    -- References
                    parent_file_path TEXT NOT NULL,

                    -- Timestamps
                    created_time TEXT,
                    synced_time TEXT NOT NULL,

                    UNIQUE(chunk_id)
                )
            """)

            # Create notion property schema table to track property definitions
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS notion_property_schema (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    database_id TEXT NOT NULL,
                    database_name TEXT NOT NULL,
                    table_name TEXT NOT NULL,
                    property_name TEXT NOT NULL,
                    column_name TEXT NOT NULL,
                    property_type TEXT NOT NULL,
                    notion_type TEXT NOT NULL,
                    added_time TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE,

                    UNIQUE(database_id, property_name),
                    UNIQUE(table_name, column_name)
                )
            """)

            # Create indexes for better performance
            self._create_indexes(cursor)

            conn.commit()
            logger.info(f"Initialized hybrid content registry at {self.db_path}")

        # Build unified view dynamically to include all workspace tables
        self.rebuild_unified_content_view()

    def _migrate_add_cc_recipients(self):
        """Migration: Add cc_recipients column to gmail_content table if it doesn't exist."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Check if cc_recipients column exists
                cursor.execute("PRAGMA table_info(gmail_content)")
                columns = {row[1] for row in cursor.fetchall()}

                if 'cc_recipients' not in columns:
                    logger.info("Migrating gmail_content table: Adding cc_recipients column")
                    cursor.execute("ALTER TABLE gmail_content ADD COLUMN cc_recipients TEXT")
                    conn.commit()
                    logger.info("Migration complete: cc_recipients column added")
                else:
                    logger.debug("Migration skipped: cc_recipients column already exists")

        except Exception as e:
            logger.error(f"Migration failed: {e}")
            # Don't raise - allow system to continue even if migration fails

    def _migrate_add_attachments(self):
        """Migration: Add attachments column to gmail_content table if it doesn't exist."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Check if attachments column exists
                cursor.execute("PRAGMA table_info(gmail_content)")
                columns = {row[1] for row in cursor.fetchall()}

                if 'attachments' not in columns:
                    logger.info("Migrating gmail_content table: Adding attachments column")
                    cursor.execute("ALTER TABLE gmail_content ADD COLUMN attachments TEXT")
                    conn.commit()
                    logger.info("Migration complete: attachments column added")
                else:
                    logger.debug("Migration skipped: attachments column already exists")

        except Exception as e:
            logger.error(f"Migration failed: {e}")
            # Don't raise - allow system to continue even if migration fails

    def _migrate_add_property_ids(self):
        """Migration: Add property_id column to notion_property_schema table."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Check if property_id column exists
                cursor.execute("PRAGMA table_info(notion_property_schema)")
                columns = {row[1] for row in cursor.fetchall()}

                if 'property_id' not in columns:
                    logger.info("Migrating notion_property_schema table: Adding property_id column")
                    cursor.execute("ALTER TABLE notion_property_schema ADD COLUMN property_id TEXT")
                    conn.commit()
                    logger.info("Migration complete: property_id column added")
                else:
                    logger.debug("Migration skipped: property_id column already exists")

        except Exception as e:
            logger.error(f"Migration failed: {e}")
            # Don't raise - allow system to continue even if migration fails

    def _migrate_add_select_options_table(self):
        """Migration: Create notion_select_options table for tracking select/multi-select/status options."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Check if table exists
                cursor.execute("""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name='notion_select_options'
                """)
                table_exists = cursor.fetchone() is not None

                if not table_exists:
                    logger.info("Creating notion_select_options table")
                    cursor.execute("""
                        CREATE TABLE notion_select_options (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            database_id TEXT NOT NULL,
                            property_id TEXT NOT NULL,
                            property_name TEXT NOT NULL,
                            option_id TEXT NOT NULL,
                            option_name TEXT NOT NULL,
                            option_color TEXT,
                            property_type TEXT NOT NULL,
                            first_seen TEXT NOT NULL,
                            last_seen TEXT NOT NULL,
                            is_active BOOLEAN DEFAULT TRUE,
                            UNIQUE(database_id, property_id, option_id)
                        )
                    """)

                    # Create indexes
                    cursor.execute("""
                        CREATE INDEX IF NOT EXISTS idx_select_options_db
                        ON notion_select_options (database_id)
                    """)
                    cursor.execute("""
                        CREATE INDEX IF NOT EXISTS idx_select_options_property
                        ON notion_select_options (database_id, property_id)
                    """)
                    cursor.execute("""
                        CREATE INDEX IF NOT EXISTS idx_select_options_option
                        ON notion_select_options (database_id, property_id, option_id)
                    """)

                    conn.commit()
                    logger.info("Migration complete: notion_select_options table created")
                else:
                    logger.debug("Migration skipped: notion_select_options table already exists")

        except Exception as e:
            logger.error(f"Migration failed: {e}")
            # Don't raise - allow system to continue even if migration fails

    def _migrate_add_relations_table(self):
        """Migration: Create notion_relations table for tracking relation properties."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Check if table exists
                cursor.execute("""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name='notion_relations'
                """)
                table_exists = cursor.fetchone() is not None

                if not table_exists:
                    logger.info("Creating notion_relations table")
                    cursor.execute("""
                        CREATE TABLE notion_relations (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            database_id TEXT NOT NULL,
                            property_id TEXT NOT NULL,
                            property_name TEXT NOT NULL,
                            target_database_id TEXT NOT NULL,
                            target_database_name TEXT,
                            relation_type TEXT,
                            synced_property_id TEXT,
                            synced_property_name TEXT,
                            first_seen TEXT NOT NULL,
                            last_seen TEXT NOT NULL,
                            is_active BOOLEAN DEFAULT TRUE,
                            UNIQUE(database_id, property_id)
                        )
                    """)

                    # Create indexes
                    cursor.execute("""
                        CREATE INDEX IF NOT EXISTS idx_relations_db
                        ON notion_relations (database_id)
                    """)
                    cursor.execute("""
                        CREATE INDEX IF NOT EXISTS idx_relations_target
                        ON notion_relations (target_database_id)
                    """)

                    conn.commit()
                    logger.info("Migration complete: notion_relations table created")
                else:
                    logger.debug("Migration skipped: notion_relations table already exists")

        except Exception as e:
            logger.error(f"Migration failed: {e}")
            # Don't raise - allow system to continue even if migration fails

    def _create_indexes(self, cursor):
        """Create indexes for better query performance."""
        # Gmail indexes for message-level storage
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_gmail_workspace ON gmail_content (workspace)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_gmail_sender ON gmail_content (sender_email)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_gmail_date ON gmail_content (email_date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_gmail_labels ON gmail_content (gmail_labels)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_gmail_thread_id ON gmail_content (thread_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_gmail_message_id ON gmail_content (message_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_gmail_thread_position ON gmail_content (thread_id, thread_position)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_gmail_latest_in_thread ON gmail_content (thread_id, is_latest_in_thread)")
        
        # Journal indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_journal_workspace ON notion_journal (workspace, database_name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_journal_status ON notion_journal (status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_journal_date ON notion_journal (date_value)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_journal_featured ON notion_journal (featured)")
        
        # Stories indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_stories_workspace ON notion_stories (workspace, database_name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_stories_status ON notion_stories (status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_stories_epic ON notion_stories (epic_relation)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_stories_priority ON notion_stories (priority)")
        
        # CMS indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_cms_workspace ON notion_cms (workspace, database_name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_cms_status ON notion_cms (status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_cms_category ON notion_cms (category)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_cms_featured ON notion_cms (featured)")
        
        # Conversation indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversation_workspace ON conversation_content (workspace)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversation_thread_id ON conversation_content (thread_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversation_context_type ON conversation_content (context_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversation_created ON conversation_content (created_time)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversation_last_edited ON conversation_content (last_edited_time)")

        # Generic indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_generic_workspace ON generic_content (workspace, database_name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_generic_type ON generic_content (content_type)")

        # Chunks indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chunks_page_id ON notion_page_chunks (page_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_chunks_workspace ON notion_page_chunks (workspace, database_name)")

        # Property schema indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_property_schema_db ON notion_property_schema (database_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_property_schema_table ON notion_property_schema (table_name)")

    def rebuild_unified_content_view(self):
        """
        Dynamically rebuild the unified_content view to include all workspace-specific tables.

        This method scans for all tables matching the pattern notion_{workspace}_{database}
        and creates a unified view that includes them along with legacy tables.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Find all workspace-specific Notion tables
                cursor.execute("""
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name LIKE 'notion_%'
                    AND name NOT IN ('notion_page_chunks', 'notion_property_schema', 'notion_select_options', 'notion_relations')
                    ORDER BY name
                """)

                notion_tables = [row[0] for row in cursor.fetchall()]
                logger.info(f"Found {len(notion_tables)} Notion tables for unified view: {notion_tables}")

                # Start building the view SQL
                view_parts = []

                # 1. Gmail content (always included)
                view_parts.append("""
                SELECT
                    page_id,
                    workspace,
                    database_id,
                    'gmail' as database_name,
                    'gmail' as content_type,
                    file_path,
                    subject as title,
                    created_time,
                    last_edited_time,
                    synced_time,
                    file_size,
                    checksum,
                    NULL as status,
                    sender_email,
                    sender_name,
                    has_attachments,
                    is_unread,
                    NULL as featured,
                    NULL as priority,
                    NULL as category,
                    email_date,
                    json_object(
                        'subject', subject,
                        'sender_email', sender_email,
                        'sender_name', sender_name,
                        'recipient_emails', recipient_emails,
                        'cc_recipients', cc_recipients,
                        'labels', gmail_labels,
                        'has_attachments', has_attachments,
                        'is_unread', is_unread,
                        'email_date', email_date
                    ) as metadata
                FROM gmail_content
                """)

                # 2. Conversation content (always included)
                view_parts.append("""
                SELECT
                    page_id,
                    workspace,
                    database_id,
                    'convos' as database_name,
                    'conversation' as content_type,
                    file_path,
                    thread_name as title,
                    created_time,
                    last_edited_time,
                    synced_time,
                    file_size,
                    checksum,
                    NULL as status,
                    NULL as sender_email,
                    NULL as sender_name,
                    NULL as has_attachments,
                    NULL as is_unread,
                    NULL as featured,
                    NULL as priority,
                    NULL as category,
                    NULL as email_date,
                    json_object(
                        'thread_id', thread_id,
                        'thread_name', thread_name,
                        'message_count', message_count,
                        'context_type', context_type,
                        'sql_query_prompt', sql_query_prompt
                    ) as metadata
                FROM conversation_content
                """)

                # 3. Add all Notion tables (workspace-specific and legacy)
                for table_name in notion_tables:
                    # Get the table schema to determine available columns
                    cursor.execute(f"PRAGMA table_info({table_name})")
                    columns = {row[1]: row[2] for row in cursor.fetchall()}  # column_name: type

                    # Build metadata JSON based on available columns
                    metadata_fields = []
                    common_property_columns = ['status', 'featured', 'priority', 'category', 'tags',
                                              'author_name', 'date_value', 'epic_relation',
                                              'story_points', 'labels', 'slug', 'publish_date']

                    for prop_col in common_property_columns:
                        if prop_col in columns:
                            metadata_fields.append(f"'{prop_col}', {prop_col}")

                    # Add any additional property columns not in the common list
                    for col_name in columns:
                        if col_name not in ['id', 'page_id', 'workspace', 'database_id', 'database_name',
                                          'file_path', 'title', 'created_time', 'last_edited_time',
                                          'synced_time', 'file_size', 'checksum'] + common_property_columns:
                            metadata_fields.append(f"'{col_name}', {col_name}")

                    metadata_json = f"json_object({', '.join(metadata_fields)})" if metadata_fields else "NULL"

                    # Determine content_type (use table name without notion_ prefix)
                    content_type = table_name.replace('notion_', 'notion_')

                    # Build SELECT for this table
                    select_stmt = f"""
                SELECT
                    page_id,
                    workspace,
                    database_id,
                    database_name,
                    '{content_type}' as content_type,
                    file_path,
                    title,
                    created_time,
                    last_edited_time,
                    synced_time,
                    file_size,
                    checksum,
                    {'status' if 'status' in columns else 'NULL'} as status,
                    NULL as sender_email,
                    NULL as sender_name,
                    NULL as has_attachments,
                    NULL as is_unread,
                    {'featured' if 'featured' in columns else 'NULL'} as featured,
                    {'priority' if 'priority' in columns else 'NULL'} as priority,
                    {'category' if 'category' in columns else 'NULL'} as category,
                    NULL as email_date,
                    {metadata_json} as metadata
                FROM {table_name}
                    """

                    view_parts.append(select_stmt)

                # 4. Generic content (fallback table)
                view_parts.append("""
                SELECT
                    page_id,
                    workspace,
                    database_id,
                    database_name,
                    content_type,
                    file_path,
                    title,
                    created_time,
                    last_edited_time,
                    synced_time,
                    file_size,
                    checksum,
                    json_extract(metadata, '$.status') as status,
                    NULL as sender_email,
                    NULL as sender_name,
                    NULL as has_attachments,
                    NULL as is_unread,
                    CAST(json_extract(metadata, '$.featured') AS INTEGER) as featured,
                    json_extract(metadata, '$.priority') as priority,
                    json_extract(metadata, '$.category') as category,
                    NULL as email_date,
                    metadata
                FROM generic_content
                """)

                # Combine all parts with UNION ALL
                full_view_sql = "CREATE VIEW unified_content AS\n" + "\nUNION ALL\n".join(view_parts)

                # Drop and recreate the view
                cursor.execute("DROP VIEW IF EXISTS unified_content")
                cursor.execute(full_view_sql)

                conn.commit()
                logger.info(f"✅ Rebuilt unified_content view with {len(view_parts)} sources")

        except Exception as e:
            logger.error(f"Failed to rebuild unified_content view: {e}")
            raise

    def add_gmail_content(self, content_data: Dict[str, Any]) -> bool:
        """Add Gmail content with optimized schema."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Extract Gmail-specific fields from metadata
                metadata = content_data.get('metadata', {})
                
                # Ensure last_edited_time is initialized to created_time if missing
                created_time = content_data.get('created_time')
                last_edited_time = content_data.get('last_edited_time') or created_time
                
                cursor.execute("""
                    INSERT OR REPLACE INTO gmail_content (
                        page_id, workspace, database_id, file_path, subject, sender_email, sender_name,
                        recipient_emails, cc_recipients, gmail_labels, thread_id, message_id,
                        has_attachments, attachments, is_unread, body_snippet, message_content,
                        thread_position, is_latest_in_thread, email_date,
                        created_time, last_edited_time, synced_time, file_size, checksum
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    content_data['page_id'],
                    content_data['workspace'],
                    content_data.get('database_id'),
                    content_data['file_path'],
                    metadata.get('subject', content_data.get('title')),
                    metadata.get('sender_email'),
                    metadata.get('sender_name'),
                    json.dumps(metadata.get('recipient_emails', [])),
                    json.dumps(metadata.get('cc_recipients', [])),
                    json.dumps(metadata.get('labels', [])),
                    metadata.get('thread_id'),
                    metadata.get('message_id'),
                    metadata.get('has_attachments', False),
                    json.dumps(metadata.get('attachments', [])),
                    metadata.get('is_unread', False),
                    metadata.get('body_snippet'),
                    metadata.get('message_content', ''),
                    metadata.get('thread_position', 0),
                    metadata.get('is_latest_in_thread', False),
                    metadata.get('email_date'),
                    created_time,
                    last_edited_time,
                    content_data['synced_time'],
                    content_data.get('file_size'),
                    content_data.get('checksum')
                ))
                
                conn.commit()
                return True
                
        except Exception as e:
            logger.error(f"Error adding Gmail content: {e}")
            return False

    def add_conversation_content(self, content_data: Dict[str, Any]) -> bool:
        """Add conversation content with optimized schema."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Extract conversation-specific fields from metadata
                metadata = content_data.get('metadata', {})

                # Ensure last_edited_time is initialized to created_time if missing
                created_time = content_data.get('created_time')
                last_edited_time = content_data.get('last_edited_time') or created_time

                cursor.execute("""
                    INSERT OR REPLACE INTO conversation_content (
                        page_id, workspace, database_id, file_path,
                        thread_id, thread_name, message_count, context_type, sql_query_prompt,
                        created_time, last_edited_time, synced_time, file_size, checksum, workspaces_used
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    content_data['page_id'],
                    content_data['workspace'],
                    content_data.get('database_id', 'convos'),
                    content_data['file_path'],
                    metadata.get('thread_id', content_data['page_id']),
                    content_data.get('title', 'Untitled Conversation'),
                    metadata.get('message_count', 0),
                    metadata.get('context_type', 'general'),
                    metadata.get('sql_query_prompt', ''),
                    created_time,
                    last_edited_time,
                    content_data['synced_time'],
                    content_data.get('file_size'),
                    content_data.get('checksum'),
                    metadata.get('workspaces_used')
                ))

                conn.commit()
                return True

        except Exception as e:
            logger.error(f"Error adding conversation content: {e}")
            return False

    def query_conversations_by_workspace(self, workspaces: List[str]) -> List[Dict[str, Any]]:
        """
        Get conversations that used any of the specified workspaces.
        Uses UNION logic: conversation appears if it contains ANY requested workspace.

        Args:
            workspaces: List of workspace names to filter by

        Returns:
            List of conversation records with page_id, thread_name, workspace, workspaces_used
        """
        if not workspaces:
            return []

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                # Build query for JSON array containment
                # Check if any requested workspace appears in the workspaces_used JSON array
                conditions = []
                params = []

                for workspace in workspaces:
                    conditions.append("workspaces_used LIKE ?")
                    params.append(f'%"{workspace}"%')

                where_clause = " OR ".join(conditions)

                query = f"""
                    SELECT
                        page_id,
                        thread_id,
                        thread_name,
                        workspace,
                        workspaces_used,
                        message_count,
                        context_type,
                        created_time,
                        last_edited_time
                    FROM conversation_content
                    WHERE workspaces_used IS NOT NULL
                    AND ({where_clause})
                    ORDER BY last_edited_time DESC
                """

                cursor.execute(query, params)
                results = cursor.fetchall()

                # Convert Row objects to dictionaries
                return [dict(row) for row in results]

        except Exception as e:
            logger.error(f"Error querying conversations by workspace: {e}")
            return []

    def get_existing_message_ids_for_thread(self, thread_id: str, workspace: str = None) -> set:
        """Get existing message IDs for a thread to avoid duplicates."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                query = "SELECT message_id FROM gmail_content WHERE thread_id = ?"
                params = [thread_id]
                
                if workspace:
                    query += " AND workspace = ?"
                    params.append(workspace)
                
                cursor.execute(query, params)
                results = cursor.fetchall()
                
                return {row[0] for row in results if row[0]}
                
        except Exception as e:
            logger.error(f"Error getting existing message IDs for thread {thread_id}: {e}")
            return set()
    
    def update_latest_message_flags(self, thread_id: str, latest_message_id: str, workspace: str = None):
        """Update is_latest_in_thread flags for a thread."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # First, set all messages in thread to not latest
                query = "UPDATE gmail_content SET is_latest_in_thread = FALSE WHERE thread_id = ?"
                params = [thread_id]
                
                if workspace:
                    query += " AND workspace = ?"
                    params.append(workspace)
                
                cursor.execute(query, params)
                
                # Then set the latest message to TRUE
                query = "UPDATE gmail_content SET is_latest_in_thread = TRUE WHERE message_id = ?"
                params = [latest_message_id]
                
                if workspace:
                    query += " AND workspace = ?"
                    params.append(workspace)
                
                cursor.execute(query, params)
                conn.commit()
                
        except Exception as e:
            logger.error(f"Error updating latest message flags for thread {thread_id}: {e}")
    
    def add_notion_journal(self, content_data: Dict[str, Any]) -> bool:
        """Add Notion journal content with optimized schema."""
        return self._add_notion_with_properties('notion_journal', content_data)
    
    def add_notion_stories(self, content_data: Dict[str, Any]) -> bool:
        """Add Notion stories content with optimized schema."""
        return self._add_notion_with_properties('notion_stories', content_data)
    
    def add_notion_cms(self, content_data: Dict[str, Any]) -> bool:
        """Add Notion CMS content with optimized schema."""
        return self._add_notion_with_properties('notion_cms', content_data)

    def _ensure_notion_table_exists(self, table_name: str) -> bool:
        """
        Ensure a Notion content table exists with base schema.

        Args:
            table_name: Name of the table to create

        Returns:
            True if successful or already exists, False otherwise
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Create table with base schema (similar to notion_journal)
                cursor.execute(f"""
                    CREATE TABLE IF NOT EXISTS {table_name} (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        page_id TEXT UNIQUE NOT NULL,
                        workspace TEXT NOT NULL,
                        database_id TEXT,
                        database_name TEXT NOT NULL,
                        file_path TEXT NOT NULL,
                        title TEXT,
                        created_time TEXT,
                        last_edited_time TEXT,
                        synced_time TEXT NOT NULL,
                        file_size INTEGER,
                        checksum TEXT,
                        UNIQUE(page_id)
                    )
                """)

                # Create indexes
                cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{table_name}_workspace ON {table_name} (workspace, database_name)")
                cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{table_name}_page_id ON {table_name} (page_id)")

                conn.commit()
                return True

        except Exception as e:
            logger.error(f"Failed to create table {table_name}: {e}")
            return False

    def _add_notion_with_properties(self, table_name: str, content_data: Dict[str, Any]) -> bool:
        """
        Add Notion content with dynamic property extraction.

        This method dynamically extracts and stores ALL properties based on the schema.

        Args:
            table_name: Name of the table (notion_journal, notion_stories, notion_cms)
            content_data: Content data dictionary

        Returns:
            True if successful, False otherwise
        """
        try:
            # Ensure table exists before attempting insert
            self._ensure_notion_table_exists(table_name)

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Extract metadata and properties
                metadata = content_data.get('metadata', {})
                properties = metadata.get('properties', {})
                database_id = content_data.get('database_id')

                # Base columns that are always present
                columns = ['page_id', 'workspace', 'database_id', 'database_name', 'file_path',
                          'title', 'created_time', 'last_edited_time', 'synced_time',
                          'file_size', 'checksum']
                values = [
                    content_data['page_id'],
                    content_data['workspace'],
                    database_id,
                    content_data['database_name'],
                    content_data['file_path'],
                    content_data.get('title'),
                    content_data.get('created_time'),
                    content_data.get('last_edited_time') or content_data.get('created_time'),
                    content_data['synced_time'],
                    content_data.get('file_size'),
                    content_data.get('checksum')
                ]

                # Get property schema for this database
                if database_id:
                    property_schema = self.get_property_schema(database_id)

                    # Extract and add dynamic properties
                    for prop_schema in property_schema:
                        prop_name = prop_schema['property_name']
                        column_name = prop_schema['column_name']

                        # Check if property exists in page properties
                        if prop_name in properties:
                            # Extract value using flexible extraction
                            value = self.extract_property_value_flexible(properties[prop_name])
                            columns.append(column_name)
                            values.append(value)

                # Build dynamic INSERT query
                placeholders = ', '.join(['?' for _ in columns])
                column_str = ', '.join(columns)

                query = f"""
                    INSERT OR REPLACE INTO {table_name} ({column_str})
                    VALUES ({placeholders})
                """

                cursor.execute(query, values)
                conn.commit()
                return True

        except Exception as e:
            logger.error(f"Error adding Notion content to {table_name}: {e}")
            logger.debug(f"Content data: {content_data.get('page_id', 'unknown')}")
            return False

    def add_generic_content(self, content_data: Dict[str, Any]) -> bool:
        """Add generic content for unknown/new content types."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Ensure last_edited_time is initialized to created_time if missing
                created_time = content_data.get('created_time')
                last_edited_time = content_data.get('last_edited_time') or created_time
                
                cursor.execute("""
                    INSERT OR REPLACE INTO generic_content (
                        page_id, workspace, database_id, database_name, content_type, file_path, title,
                        created_time, last_edited_time, synced_time, file_size, checksum, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    content_data['page_id'],
                    content_data['workspace'],
                    content_data.get('database_id'),
                    content_data['database_name'],
                    content_data.get('content_type', content_data['database_name']),
                    content_data['file_path'],
                    content_data.get('title'),
                    created_time,
                    last_edited_time,
                    content_data['synced_time'],
                    content_data.get('file_size'),
                    content_data.get('checksum'),
                    json.dumps(content_data.get('metadata', {}))
                ))
                
                conn.commit()
                return True
                
        except Exception as e:
            logger.error(f"Error adding generic content: {e}")
            return False

    def _cleanup_stale_notion_entries(self, table_name: str, page_id: str):
        """Remove stale entries from misrouted notion tables (e.g., Slack data that was incorrectly stored in notion_* tables)."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
                if cursor.fetchone():
                    cursor.execute(f"DELETE FROM {table_name} WHERE page_id = ?", (page_id,))
                    conn.commit()
        except Exception:
            pass  # Non-critical cleanup, don't fail the sync

    def update_page_properties(self, page_id: str, database_id: str, database_name: str,
                              workspace: str, properties: Dict[str, Any]) -> bool:
        """
        Update only the property columns for an existing page without touching content.

        This is used for property-only sync mode to backfill properties without re-downloading content.

        Args:
            page_id: The Notion page ID
            database_id: The Notion database ID
            database_name: The database nickname
            workspace: The workspace name
            properties: The properties dict from Notion API response

        Returns:
            True if successful, False otherwise
        """
        try:
            # Determine table name
            table_name = f"notion_{workspace}_{database_name}"

            # Ensure table exists
            self._ensure_notion_table_exists(table_name)

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Check if page exists
                cursor.execute(f"SELECT page_id FROM {table_name} WHERE page_id = ?", (page_id,))
                if not cursor.fetchone():
                    logger.warning(f"Page {page_id} not found in {table_name}, skipping property update")
                    return False

                # Get property schema for this database
                property_schema = self.get_property_schema(database_id)

                if not property_schema:
                    logger.debug(f"No property schema found for database {database_id}")
                    return False

                # Build UPDATE query with only property columns
                update_columns = []
                update_values = []

                for prop_schema in property_schema:
                    prop_name = prop_schema['property_name']
                    column_name = prop_schema['column_name']

                    # Check if property exists in page properties
                    if prop_name in properties:
                        # Extract value using flexible extraction
                        value = self.extract_property_value_flexible(properties[prop_name])
                        update_columns.append(f"{column_name} = ?")
                        update_values.append(value)

                if not update_columns:
                    logger.debug(f"No properties to update for page {page_id}")
                    return True  # Not an error, just nothing to update

                # Build and execute UPDATE query
                update_sql = f"UPDATE {table_name} SET {', '.join(update_columns)} WHERE page_id = ?"
                update_values.append(page_id)

                cursor.execute(update_sql, update_values)
                conn.commit()

                logger.debug(f"✅ Updated {len(update_columns)} properties for page {page_id}")

            # Generate property embeddings
            self._generate_property_embeddings_only(
                page_id=page_id,
                database_id=database_id,
                database_name=database_name,
                workspace=workspace,
                table_name=table_name
            )

            return True

        except Exception as e:
            logger.error(f"Error updating page properties for {page_id}: {e}")
            return False

    def _generate_property_embeddings_only(self, page_id: str, database_id: str,
                                           database_name: str, workspace: str,
                                           table_name: str) -> bool:
        """
        Generate property embeddings only (without content embeddings).

        Used for property-only sync mode.

        Args:
            page_id: The page ID
            database_id: The database ID
            database_name: The database nickname
            workspace: The workspace name
            table_name: The SQL table name

        Returns:
            True if successful, False otherwise
        """
        try:
            # Check if vector search is enabled
            import json
            import os
            from promaia.utils.env_writer import get_config_path
            config_path = str(get_config_path())
            with open(config_path, 'r') as f:
                config = json.load(f)
            vector_config = config.get('global', {}).get('vector_search', {})

            if not vector_config.get('enabled', False):
                return False  # Vector search disabled

            if not vector_config.get('property_embeddings', {}).get('enabled', False):
                return False  # Property embeddings disabled

            # Initialize vector DB
            from promaia.storage.vector_db import VectorDBManager
            vector_db = VectorDBManager(chroma_path=vector_config.get('chroma_path'))

            # Get property schema
            property_schema = self.get_property_schema(database_id)
            if not property_schema:
                return False

            EMBEDDABLE_TYPES = {'title', 'text', 'rich_text', 'relation'}

            # Query properties from SQLite
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                column_names = [prop['column_name'] for prop in property_schema]
                if not column_names:
                    return False

                columns_str = ', '.join(column_names)
                query = f"SELECT {columns_str} FROM {table_name} WHERE page_id = ?"

                cursor.execute(query, (page_id,))
                row = cursor.fetchone()

                if not row:
                    logger.warning(f"Page {page_id} not found in {table_name}")
                    return False

                # Base metadata for embeddings
                base_metadata = {
                    'workspace': workspace,
                    'database_name': database_name,
                    'database_id': database_id,
                    'content_type': 'notion'
                }

                # Create embeddings for embeddable properties
                for prop_schema_item in property_schema:
                    col_name = prop_schema_item['column_name']
                    prop_type = prop_schema_item['notion_type']
                    value = row[col_name]

                    if value is not None and value != '':
                        # Create property embedding if type is embeddable
                        if prop_type in EMBEDDABLE_TYPES:
                            formatted_value = self._format_property_for_embedding(
                                value, prop_type, table_name
                            )

                            if formatted_value:
                                try:
                                    vector_db.add_property_embedding(
                                        page_id=page_id,
                                        property_name=col_name,
                                        property_value=formatted_value,
                                        property_type=prop_type,
                                        base_metadata=base_metadata
                                    )
                                    logger.debug(f"✅ Created property embedding: {page_id}.{col_name}")
                                except Exception as e:
                                    logger.warning(f"Failed to embed property {col_name}: {e}")
                    else:
                        # Value is None or empty - delete embedding if it exists
                        if prop_type in EMBEDDABLE_TYPES:
                            try:
                                deleted = vector_db.delete_property_embedding(
                                    page_id=page_id,
                                    property_name=col_name
                                )
                                if deleted:
                                    logger.debug(f"🗑️ Deleted property embedding for cleared value: {page_id}.{col_name}")
                            except Exception as e:
                                logger.warning(f"Failed to delete property embedding {col_name}: {e}")

            return True

        except Exception as e:
            logger.error(f"Error generating property embeddings for {page_id}: {e}")
            return False

    def add_content(self, content_data: Dict[str, Any]) -> bool:
        """Add content using the appropriate table based on content type."""
        database_name = content_data.get('database_name', '')
        workspace = content_data.get('workspace', '')

        # Check data_source to determine routing
        # Discord and other non-Notion sources should use generic_content
        # Check both top-level and nested metadata for data_source
        data_source = content_data.get('data_source', '')
        if not data_source and 'metadata' in content_data:
            # For content coming from unified_storage, data_source is nested in metadata
            metadata = content_data.get('metadata', {})
            data_source = metadata.get('data_source', '')

        # Route to appropriate table based on content type
        if database_name == 'gmail' or 'gmail' in database_name:
            sql_success = self.add_gmail_content(content_data)
        elif data_source == 'conversation' or database_name == 'convos':
            # Conversation history goes to conversation_content table
            sql_success = self.add_conversation_content(content_data)
        elif data_source in ('discord', 'slack'):
            # Discord/Slack messages always go to generic_content for proper metadata support
            sql_success = self.add_generic_content(content_data)
            # Clean up any stale entries from misrouted notion tables (one-time migration)
            if sql_success and database_name and workspace:
                stale_table = f"notion_{workspace}_{database_name}"
                self._cleanup_stale_notion_entries(stale_table, content_data['page_id'])
        elif database_name and workspace:
            # Route ALL Notion databases to workspace-specific tables
            # This ensures every database gets proper schema with property columns
            table_name = f"notion_{workspace}_{database_name}"
            sql_success = self._add_notion_with_properties(table_name, content_data)
        else:
            # Fallback to generic table only if missing database_name or workspace
            sql_success = self.add_generic_content(content_data)

        # If SQL insertion succeeded, also embed to ChromaDB (if enabled)
        if sql_success:
            self._embed_to_vector_db(content_data)

        return sql_success
    
    def _embed_to_vector_db(self, content_data: Dict[str, Any]) -> bool:
        """
        Embed content to ChromaDB for vector search.

        This is called after successful SQL insertion and runs silently
        to avoid disrupting the sync flow if vector DB is unavailable.
        """
        try:
            # Check for emergency disable flag (to bypass ChromaDB crashes)
            import os
            if os.environ.get('DISABLE_VECTOR_EMBEDDINGS') == '1':
                logger.debug("Vector embeddings disabled via DISABLE_VECTOR_EMBEDDINGS env var")
                return False

            # Check if vector search is enabled - load from main config file
            import json
            from promaia.utils.env_writer import get_config_path
            config_path = str(get_config_path())
            with open(config_path, 'r') as f:
                config = json.load(f)
            vector_config = config.get('global', {}).get('vector_search', {})

            if not vector_config.get('enabled', False):
                return False  # Vector search disabled, skip silently
            
            # Get required fields
            page_id = content_data.get('page_id')
            file_path = content_data.get('file_path')
            
            if not page_id or not file_path:
                return False  # Missing required fields
            
            # Read markdown content
            if not os.path.exists(file_path):
                return False  # File doesn't exist
            
            with open(file_path, 'r', encoding='utf-8') as f:
                content_text = f.read()
            
            if not content_text or len(content_text.strip()) < 10:
                return False  # Content too short or empty
            
            # Initialize vector DB
            from promaia.storage.vector_db import VectorDBManager
            vector_db = VectorDBManager(chroma_path=vector_config.get('chroma_path'))
            
            # Prepare metadata
            metadata = {
                'database_name': content_data.get('database_name', ''),
                'workspace': content_data.get('workspace', ''),
                'created_time': content_data.get('created_time', ''),
                'content_type': content_data.get('content_type', ''),
            }

            # Add properties to metadata (except title, which is in content)
            try:
                database_id = content_data.get('database_id')
                database_name = content_data.get('database_name', '')
                workspace = content_data.get('workspace', '')

                # Determine table name to query properties from (use workspace-specific tables)
                if workspace and database_name:
                    # ALL Notion databases use workspace-specific tables now
                    table_name = f"notion_{workspace}_{database_name}"
                else:
                    # Fallback for backwards compatibility with old data
                    table_mapping = {
                        'journal': 'notion_journal',
                        'stories': 'notion_stories',
                        'cms': 'notion_cms',
                    }
                    table_name = table_mapping.get(database_name, None)

                # Query properties if we have a known table
                if table_name and database_id:
                    with sqlite3.connect(self.db_path) as conn:
                        conn.row_factory = sqlite3.Row
                        cursor = conn.cursor()

                        # Get property schema
                        property_schema = self.get_property_schema(database_id)

                        if property_schema:
                            # Embeddable property types (for separate property embeddings)
                            EMBEDDABLE_TYPES = {'title', 'text', 'rich_text', 'relation'}

                            # Build query to fetch page with properties
                            column_names = [prop['column_name'] for prop in property_schema]
                            if column_names:
                                columns_str = ', '.join(column_names)
                                query = f"SELECT {columns_str} FROM {table_name} WHERE page_id = ?"

                                cursor.execute(query, (page_id,))
                                row = cursor.fetchone()

                                if row:
                                    # Add each property to metadata AND create embeddings
                                    for i, prop_schema_item in enumerate(property_schema):
                                        col_name = prop_schema_item['column_name']
                                        prop_type = prop_schema_item['notion_type']
                                        value = row[col_name]

                                        if value is not None and value != '':
                                            # Add to metadata (for filtering)
                                            metadata[col_name] = value

                                            # Create property embedding if type is embeddable
                                            if prop_type in EMBEDDABLE_TYPES:
                                                formatted_value = self._format_property_for_embedding(
                                                    value, prop_type, table_name
                                                )

                                                if formatted_value:
                                                    try:
                                                        vector_db.add_property_embedding(
                                                            page_id=page_id,
                                                            property_name=col_name,
                                                            property_value=formatted_value,
                                                            property_type=prop_type,
                                                            base_metadata={
                                                                'workspace': metadata.get('workspace'),
                                                                'database_name': metadata.get('database_name'),
                                                                'database_id': metadata.get('database_id'),
                                                                'content_type': metadata.get('content_type')
                                                            }
                                                        )
                                                        logger.debug(f"✅ Created property embedding: {page_id}.{col_name}")
                                                    except Exception as e:
                                                        logger.warning(f"Failed to embed property {col_name}: {e}")
                                        else:
                                            # Value is None or empty - delete embedding if it exists
                                            if prop_type in EMBEDDABLE_TYPES:
                                                try:
                                                    deleted = vector_db.delete_property_embedding(
                                                        page_id=page_id,
                                                        property_name=col_name
                                                    )
                                                    if deleted:
                                                        logger.debug(f"🗑️ Deleted property embedding for cleared value: {page_id}.{col_name}")
                                                except Exception as e:
                                                    logger.warning(f"Failed to delete property embedding {col_name}: {e}")

                logger.debug(f"Added {len(metadata) - 4} properties to vector metadata for {page_id}")

            except Exception as e:
                logger.warning(f"Could not add properties to vector metadata for {page_id}: {e}")
            
            # Check chunking configuration
            chunking_config = vector_config.get('chunking', {})
            chunking_enabled = chunking_config.get('enabled', True)
            max_tokens = chunking_config.get('max_tokens_per_chunk', 6000)
            
            # Estimate tokens in content
            estimated_tokens = vector_db.estimate_tokens(content_text)
            
            # Determine if chunking is needed
            if chunking_enabled and estimated_tokens > max_tokens:
                # Content exceeds token limit, use chunking
                logger.info(f"Page {page_id} has {estimated_tokens} tokens (> {max_tokens}), using chunking")
                
                from promaia.storage.page_chunker import chunk_page_content
                from datetime import datetime
                
                # Generate chunks
                chunks = chunk_page_content(
                    markdown_content=content_text,
                    page_id=page_id,
                    block_metadata=None,  # Could be enhanced with block timestamps
                    max_tokens=max_tokens,
                    provider=vector_db.embedding_provider
                )
                
                if not chunks:
                    logger.warning(f"Failed to chunk page {page_id}")
                    return False
                
                # Store chunks in database
                synced_time = datetime.utcnow().isoformat()
                for chunk in chunks:
                    chunk_data = {
                        'chunk_id': chunk['chunk_id'],
                        'page_id': page_id,
                        'chunk_index': chunk['chunk_index'],
                        'total_chunks': chunk['total_chunks'],
                        'workspace': content_data.get('workspace', ''),
                        'database_name': content_data.get('database_name', ''),
                        'char_start': chunk['char_start'],
                        'char_end': chunk['char_end'],
                        'estimated_tokens': chunk['estimated_tokens'],
                        'date_boundary': chunk.get('date_boundary'),
                        'parent_file_path': file_path,
                        'created_time': content_data.get('created_time'),
                        'synced_time': synced_time
                    }
                    self.add_page_chunk(chunk_data)
                
                # Embed chunks to vector DB
                success = vector_db.add_content_with_chunking(
                    page_id=page_id,
                    content_text=content_text,
                    metadata=metadata,
                    chunks=chunks
                )
                
                if success:
                    logger.info(f"✅ Embedded {len(chunks)} chunks to vector DB for page: {page_id}")
                
                return success
            else:
                # Content fits in single embedding, use standard flow
                success = vector_db.add_content(
                    page_id=page_id,
                    content_text=content_text,
                    metadata=metadata
                )
                
                if success:
                    logger.debug(f"✅ Embedded to vector DB: {page_id} ({estimated_tokens} tokens)")
                
                return success
        
        except Exception as e:
            # Silently log errors - don't disrupt sync if vector DB has issues
            logger.debug(f"Could not embed to vector DB for {content_data.get('page_id', 'unknown')}: {e}")
            return False
    
    def add_content_batch(self, content_list: List[Dict[str, Any]]) -> List[bool]:
        """Add multiple content items efficiently - significant performance improvement."""
        if not content_list:
            return []
        
        results = []
        success_count = 0
        
        for content_data in content_list:
            try:
                # Use existing add_content method (already optimized with proper routing)
                success = self.add_content(content_data)
                results.append(success)
                if success:
                    success_count += 1
            except Exception as e:
                logger.error(f"Error in batch add for content {content_data.get('page_id', 'unknown')}: {e}")
                results.append(False)
        
        logger.info(f"Batch processed {len(content_list)} content items with {success_count} successes")
        return results
    
    def query_content(self, workspace: str = None, database_name: str = None, 
                     content_type: str = None, filters: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """Query content using the unified view."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Build WHERE clause
                where_conditions = []
                params = []
                
                if workspace:
                    where_conditions.append("workspace = ?")
                    params.append(workspace)
                
                if database_name:
                    where_conditions.append("database_name = ?")
                    params.append(database_name)
                
                if content_type:
                    where_conditions.append("content_type = ?")
                    params.append(content_type)
                
                # Add custom filters
                if filters:
                    for key, value in filters.items():
                        if key in ['status', 'featured', 'priority', 'category']:
                            # These can be searched in metadata JSON
                            where_conditions.append(f"json_extract(metadata, '$.{key}') = ?")
                            params.append(value)
                        elif key.endswith('_date') or key.endswith('_time'):
                            where_conditions.append(f"{key} >= ?")
                            params.append(value)
                
                where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
                
                query = f"""
                    SELECT page_id, workspace, database_name, content_type, file_path, title,
                           created_time, last_edited_time, synced_time, metadata
                    FROM unified_content 
                    WHERE {where_clause}
                    ORDER BY last_edited_time DESC
                """
                
                cursor.execute(query, params)
                results = cursor.fetchall()
                
                # Convert to list of dictionaries
                columns = ['page_id', 'workspace', 'database_name', 'content_type', 'file_path', 
                          'title', 'created_time', 'last_edited_time', 'synced_time', 'metadata']
                
                content_list = []
                for row in results:
                    content_dict = dict(zip(columns, row))
                    # Parse metadata JSON
                    if content_dict['metadata']:
                        content_dict['metadata'] = json.loads(content_dict['metadata'])
                    content_list.append(content_dict)
                
                return content_list
                
        except Exception as e:
            logger.error(f"Error querying content: {e}")
            return []
    
    def get_content_statistics(self) -> Dict[str, Any]:
        """Get statistics about content in each table."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                stats = {}
                
                # Gmail stats
                cursor.execute("SELECT COUNT(*) FROM gmail_content")
                stats['gmail'] = cursor.fetchone()[0]
                
                # Journal stats
                cursor.execute("SELECT COUNT(*) FROM notion_journal")
                stats['journal'] = cursor.fetchone()[0]
                
                # Stories stats
                cursor.execute("SELECT COUNT(*) FROM notion_stories")
                stats['stories'] = cursor.fetchone()[0]
                
                # CMS stats
                cursor.execute("SELECT COUNT(*) FROM notion_cms")
                stats['cms'] = cursor.fetchone()[0]
                
                # Generic stats
                cursor.execute("SELECT COUNT(*) FROM generic_content")
                stats['generic'] = cursor.fetchone()[0]
                
                # Total stats
                cursor.execute("SELECT COUNT(*) FROM unified_content")
                stats['total'] = cursor.fetchone()[0]
                
                return stats
                
        except Exception as e:
            logger.error(f"Error getting statistics: {e}")
            return {}
    
    def migrate_from_legacy(self, legacy_db_path: str = "data/metadata.db") -> bool:
        """Migrate data from legacy single-table structure to hybrid architecture.
        
        DEPRECATED: Legacy migration is no longer supported.
        The system now uses hybrid architecture exclusively.
        """
        logger.warning("Legacy migration is deprecated. System uses hybrid architecture exclusively.")
        logger.info("If you need to import data, use the database sync commands instead.")
        return False
    
    def _extract_notion_property(self, properties: Dict[str, Any],
                                prop_name: str, prop_type: str,
                                *path_elements) -> Any:
        """Extract a property value from Notion property structure."""
        try:
            if prop_name not in properties:
                return None

            prop = properties[prop_name]
            if prop_type not in prop:
                return None

            value = prop[prop_type]

            # Navigate through path elements
            for element in path_elements:
                if isinstance(element, int) and isinstance(value, list):
                    if element < len(value):
                        value = value[element]
                    else:
                        return None
                elif isinstance(element, str) and isinstance(value, dict):
                    if element in value:
                        value = value[element]
                    else:
                        return None
                else:
                    return None

            return value

        except Exception:
            return None

    def extract_property_value_flexible(self, property_data: Dict[str, Any]) -> Any:
        """
        Extract value from a Notion property in a flexible way.

        Handles all Notion property types and returns appropriately formatted values.

        Args:
            property_data: Single property dict from Notion API

        Returns:
            Extracted value (str, int, float, list, or None)
        """
        try:
            prop_type = property_data.get('type')

            if not prop_type:
                return None

            # Extract based on type
            if prop_type == 'title':
                title_arr = property_data.get('title', [])
                return title_arr[0]['plain_text'] if title_arr else None

            elif prop_type == 'rich_text':
                text_arr = property_data.get('rich_text', [])
                return ' '.join([t['plain_text'] for t in text_arr]) if text_arr else None

            elif prop_type == 'number':
                return property_data.get('number')

            elif prop_type == 'select':
                select_data = property_data.get('select')
                return select_data['name'] if select_data else None

            elif prop_type == 'multi_select':
                multi_arr = property_data.get('multi_select', [])
                return json.dumps([item['name'] for item in multi_arr]) if multi_arr else None

            elif prop_type == 'status':
                status_data = property_data.get('status')
                return status_data['name'] if status_data else None

            elif prop_type == 'date':
                date_data = property_data.get('date')
                return date_data['start'] if date_data else None

            elif prop_type == 'checkbox':
                return 1 if property_data.get('checkbox') else 0

            elif prop_type == 'url':
                return property_data.get('url')

            elif prop_type == 'email':
                return property_data.get('email')

            elif prop_type == 'phone_number':
                return property_data.get('phone_number')

            elif prop_type == 'relation':
                relation_arr = property_data.get('relation', [])
                return json.dumps([r['id'] for r in relation_arr]) if relation_arr else None

            elif prop_type == 'people':
                people_arr = property_data.get('people', [])
                return json.dumps([p['id'] for p in people_arr]) if people_arr else None

            elif prop_type == 'files':
                files_arr = property_data.get('files', [])
                return json.dumps([f.get('name', f.get('file', {}).get('url', ''))
                                  for f in files_arr]) if files_arr else None

            elif prop_type == 'formula':
                formula_data = property_data.get('formula', {})
                formula_type = formula_data.get('type')
                if formula_type:
                    return formula_data.get(formula_type)
                return None

            elif prop_type == 'rollup':
                rollup_data = property_data.get('rollup', {})
                rollup_type = rollup_data.get('type')
                if rollup_type == 'number':
                    return rollup_data.get('number')
                elif rollup_type == 'array':
                    return json.dumps(rollup_data.get('array', []))
                return None

            elif prop_type in ['created_time', 'last_edited_time']:
                return property_data.get(prop_type)

            elif prop_type in ['created_by', 'last_edited_by']:
                user_data = property_data.get(prop_type)
                return user_data.get('id') if user_data else None

            else:
                # Unknown type, try to serialize as JSON
                logger.warning(f"Unknown property type: {prop_type}")
                return json.dumps(property_data)

        except Exception as e:
            logger.error(f"Error extracting property value: {e}")
            return None

    def _format_property_for_embedding(
        self,
        value: Any,
        prop_type: str,
        table_name: str = None
    ) -> Optional[str]:
        """
        Format property value as text for embedding.

        Args:
            value: Raw property value
            prop_type: Notion property type
            table_name: Table name for relation resolution

        Returns:
            Formatted text string or None
        """
        MAX_TOKENS = 8000  # Maximum tokens for property embeddings

        if value is None or value == '':
            return None

        # Format value based on type
        formatted_value = None

        if prop_type == 'relation':
            # Resolve relation IDs to titles
            formatted_value = self._resolve_relation_titles(value)

        elif prop_type in ['people', 'multi_select']:
            # Already comma-separated strings or JSON arrays
            if isinstance(value, str):
                # If it's a JSON array string, parse and format nicely
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, list):
                        formatted_value = ", ".join(str(item) for item in parsed)
                except:
                    pass
            if formatted_value is None:
                formatted_value = str(value)

        else:
            # title, text, rich_text, select, status
            formatted_value = str(value)

        if formatted_value is None:
            return None

        # Truncate if exceeds max tokens
        try:
            # Try using tiktoken for accurate token counting (OpenAI)
            try:
                import tiktoken
                encoding = tiktoken.get_encoding("cl100k_base")
                tokens = encoding.encode(formatted_value)

                if len(tokens) > MAX_TOKENS:
                    # Truncate to max tokens
                    truncated_tokens = tokens[:MAX_TOKENS]
                    formatted_value = encoding.decode(truncated_tokens)
                    logger.warning(
                        f"⚠️ Property value truncated from {len(tokens)} to {MAX_TOKENS} tokens "
                        f"(type: {prop_type})"
                    )
            except ImportError:
                # Fallback: rough character-based estimation (1 token ≈ 4 chars)
                estimated_tokens = len(formatted_value) // 4
                if estimated_tokens > MAX_TOKENS:
                    max_chars = MAX_TOKENS * 4
                    formatted_value = formatted_value[:max_chars]
                    logger.warning(
                        f"⚠️ Property value truncated (estimated {estimated_tokens} tokens, "
                        f"max {MAX_TOKENS} tokens, type: {prop_type})"
                    )
        except Exception as e:
            logger.debug(f"Could not check token length, using value as-is: {e}")

        return formatted_value

    def _resolve_relation_titles(self, relation_value: str) -> Optional[str]:
        """
        Resolve relation page IDs to titles.

        Args:
            relation_value: JSON array string of page IDs or comma-separated IDs

        Returns:
            Comma-separated titles or None
        """
        if not relation_value:
            return None

        try:
            # Parse relation value (could be JSON array or comma-separated)
            if relation_value.startswith('['):
                # JSON array: ["page_id1", "page_id2"]
                page_ids = json.loads(relation_value)
            else:
                # Comma-separated: "page_id1,page_id2"
                page_ids = [pid.strip() for pid in relation_value.split(',') if pid.strip()]

            if not page_ids:
                return None

            titles = []
            not_found = []
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                for page_id in page_ids:
                    cursor.execute(
                        "SELECT title FROM unified_content WHERE page_id = ?",
                        (page_id,)
                    )
                    row = cursor.fetchone()
                    if row and row[0]:
                        titles.append(row[0])
                    else:
                        not_found.append(page_id)
                        logger.debug(f"Relation page not found (may have been deleted): {page_id}")

            if not_found and not titles:
                logger.debug(f"All relation pages not found: {len(not_found)} missing")
                return None  # All relations are broken

            return ", ".join(titles) if titles else None

        except Exception as e:
            logger.warning(f"Failed to resolve relation titles: {e}")
            return None

    def get_content_by_file_path(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Retrieve a single content entry by its file path."""
        query = "SELECT * FROM unified_content WHERE file_path = ?"
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(query, (file_path,))
                row = cursor.fetchone()
                return dict(row) if row else None
        except sqlite3.Error as e:
            print(f"Database error in get_content_by_file_path: {e}")
            return None

    def clear_generic_content_for_database(self, database_name: str) -> int:
        """Deletes all entries from the generic_content table for a specific database."""
        query = "DELETE FROM generic_content WHERE database_name = ?"
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(query, (database_name,))
                conn.commit()
                # Return the number of deleted rows
                return cursor.rowcount
        except sqlite3.Error as e:
            print(f"Database error while clearing generic_content for {database_name}: {e}")
            return 0
    
    def add_page_chunk(self, chunk_data: Dict[str, Any]) -> bool:
        """
        Add a page chunk to the database.
        
        Args:
            chunk_data: Dict containing chunk metadata
                Required: chunk_id, page_id, chunk_index, total_chunks, 
                         workspace, database_name, parent_file_path, synced_time
                Optional: char_start, char_end, estimated_tokens, date_boundary, created_time
        
        Returns:
            True if successful, False otherwise
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO notion_page_chunks (
                        chunk_id, page_id, chunk_index, total_chunks,
                        workspace, database_name, char_start, char_end,
                        estimated_tokens, date_boundary, parent_file_path,
                        created_time, synced_time
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    chunk_data['chunk_id'],
                    chunk_data['page_id'],
                    chunk_data['chunk_index'],
                    chunk_data['total_chunks'],
                    chunk_data['workspace'],
                    chunk_data['database_name'],
                    chunk_data.get('char_start'),
                    chunk_data.get('char_end'),
                    chunk_data.get('estimated_tokens'),
                    chunk_data.get('date_boundary'),
                    chunk_data['parent_file_path'],
                    chunk_data.get('created_time'),
                    chunk_data['synced_time']
                ))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error adding page chunk {chunk_data.get('chunk_id')}: {e}")
            return False
    
    def get_chunks_for_page(self, page_id: str) -> List[Dict[str, Any]]:
        """
        Retrieve all chunks for a given page.
        
        Args:
            page_id: Page identifier
        
        Returns:
            List of chunk metadata dicts, ordered by chunk_index
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM notion_page_chunks 
                    WHERE page_id = ?
                    ORDER BY chunk_index
                """, (page_id,))
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error retrieving chunks for page {page_id}: {e}")
            return []
    
    def get_page_metadata(self, page_id: str) -> Optional[Dict[str, Any]]:
        """
        Get metadata for a specific page by page_id from unified_content.
        
        Args:
            page_id: Page identifier
            
        Returns:
            Dict with page metadata or None if not found
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT page_id, workspace, database_name, content_type, 
                           title, created_time, last_edited_time, synced_time
                    FROM unified_content 
                    WHERE page_id = ?
                """, (page_id,))
                
                row = cursor.fetchone()
                if not row:
                    return None
                    
                return {
                    'page_id': row['page_id'],
                    'workspace': row['workspace'],
                    'database_name': row['database_name'],
                    'content_type': row['content_type'],
                    'title': row['title'],
                    'created_time': row['created_time'],
                    'last_edited_time': row['last_edited_time'],
                    'synced_time': row['synced_time']
                }
        except Exception as e:
            logger.error(f"Error getting page metadata for {page_id}: {e}")
            return None

    def remove_chunks_for_page(self, page_id: str) -> bool:
        """
        Remove all chunks for a given page.

        Args:
            page_id: Page identifier

        Returns:
            True if successful, False otherwise
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM notion_page_chunks WHERE page_id = ?", (page_id,))
                conn.commit()
                deleted_count = cursor.rowcount
                if deleted_count > 0:
                    logger.debug(f"Removed {deleted_count} chunks for page {page_id}")
                return True
        except Exception as e:
            logger.error(f"Error removing chunks for page {page_id}: {e}")
            return False

    def delete_page(self, page_id: str) -> bool:
        """
        Delete a page and all its related data from the registry.

        This removes the page from:
        - gmail_content or generic_content (underlying tables for unified_content view)
        - notion_page_chunks (embeddings/chunks)
        - Property tables (if they exist)

        Args:
            page_id: Page identifier to delete

        Returns:
            True if successful, False otherwise
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Get page info before deletion to know which tables to clean up
                cursor.execute("""
                    SELECT database_name, workspace, content_type
                    FROM unified_content
                    WHERE page_id = ?
                """, (page_id,))
                page_info = cursor.fetchone()

                if not page_info:
                    logger.debug(f"Page {page_id} not found in registry")
                    return True  # Already deleted

                database_name, workspace, content_type = page_info

                # Delete from chunks table
                cursor.execute("DELETE FROM notion_page_chunks WHERE page_id = ?", (page_id,))

                # Delete from content tables
                if content_type == 'gmail':
                    cursor.execute("DELETE FROM gmail_content WHERE page_id = ?", (page_id,))
                else:
                    cursor.execute("DELETE FROM generic_content WHERE page_id = ?", (page_id,))

                # Delete from property table if it exists
                if workspace and database_name:
                    property_table = f"notion_{workspace}_{database_name}"
                    # Check if table exists
                    cursor.execute("""
                        SELECT name FROM sqlite_master
                        WHERE type='table' AND name=?
                    """, (property_table,))
                    if cursor.fetchone():
                        try:
                            cursor.execute(f"DELETE FROM {property_table} WHERE page_id = ?", (page_id,))
                        except Exception as e:
                            logger.warning(f"Failed to delete from property table {property_table}: {e}")

                conn.commit()
                logger.debug(f"Deleted page {page_id} from registry")
                return True

        except Exception as e:
            logger.error(f"Error deleting page {page_id}: {e}")
            return False

    @staticmethod
    def sanitize_property_name(property_name: str) -> str:
        """
        Sanitize a Notion property name to create a valid SQLite column name.

        Rules:
        - Convert to lowercase
        - Replace spaces with underscores
        - Remove special characters except underscores
        - Ensure it starts with a letter or underscore
        - Truncate to 64 characters

        Args:
            property_name: Original Notion property name

        Returns:
            Sanitized column name safe for SQLite
        """
        import re

        # Convert to lowercase
        sanitized = property_name.lower()

        # Replace spaces and hyphens with underscores
        sanitized = sanitized.replace(' ', '_').replace('-', '_')

        # Remove all characters except alphanumeric and underscores
        sanitized = re.sub(r'[^a-z0-9_]', '', sanitized)

        # Ensure it starts with a letter or underscore
        if sanitized and not sanitized[0].isalpha() and sanitized[0] != '_':
            sanitized = 'prop_' + sanitized

        # If empty after sanitization, use a default
        if not sanitized:
            sanitized = 'property_value'

        # Truncate to 64 characters
        sanitized = sanitized[:64]

        # Avoid SQL keywords
        sql_keywords = {'select', 'from', 'where', 'table', 'index', 'order', 'group', 'by', 'having', 'join', 'union'}
        if sanitized in sql_keywords:
            sanitized = sanitized + '_prop'

        return sanitized

    @staticmethod
    def get_sqlite_type_for_notion_property(notion_type: str) -> str:
        """
        Map Notion property type to SQLite column type.

        Args:
            notion_type: Notion property type (e.g., 'select', 'number', 'checkbox')

        Returns:
            SQLite column type (TEXT, INTEGER, REAL, BOOLEAN)
        """
        type_mapping = {
            'title': 'TEXT',
            'rich_text': 'TEXT',
            'number': 'REAL',
            'select': 'TEXT',
            'multi_select': 'TEXT',  # Will store as JSON array
            'status': 'TEXT',
            'date': 'TEXT',  # Store as ISO format string
            'checkbox': 'INTEGER',  # SQLite boolean (0/1)
            'url': 'TEXT',
            'email': 'TEXT',
            'phone_number': 'TEXT',
            'formula': 'TEXT',  # Complex, store as text
            'relation': 'TEXT',  # Store as JSON array of IDs
            'rollup': 'TEXT',  # Complex, store as JSON
            'people': 'TEXT',  # Store as JSON array
            'files': 'TEXT',  # Store as JSON array
            'created_time': 'TEXT',
            'last_edited_time': 'TEXT',
            'created_by': 'TEXT',
            'last_edited_by': 'TEXT',
        }

        return type_mapping.get(notion_type, 'TEXT')

    def get_property_schema(self, database_id: str) -> List[Dict[str, Any]]:
        """
        Get the property schema for a specific Notion database.

        Args:
            database_id: Notion database ID

        Returns:
            List of property definitions
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM notion_property_schema
                    WHERE database_id = ? AND is_active = TRUE
                    ORDER BY added_time
                """, (database_id,))
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error getting property schema for database {database_id}: {e}")
            return []

    def update_property_schema(self, database_id: str, database_name: str,
                              table_name: str, properties: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update the property schema for a Notion database.

        Compares current properties against stored schema and returns
        changes (added/removed/unchanged properties).

        Args:
            database_id: Notion database ID
            database_name: Notion database name
            table_name: SQL table name (e.g., 'notion_journal')
            properties: Dict of Notion properties from API

        Returns:
            Dict with keys: 'added', 'removed', 'unchanged'
        """
        try:
            now = datetime.utcnow().isoformat()

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Get current schema from database
                cursor.execute("""
                    SELECT property_name, column_name, notion_type
                    FROM notion_property_schema
                    WHERE database_id = ? AND is_active = TRUE
                """, (database_id,))

                current_schema = {row[0]: {'column_name': row[1], 'notion_type': row[2]}
                                 for row in cursor.fetchall()}

                # Analyze changes
                current_props = set(current_schema.keys())
                new_props = set(properties.keys())

                added_props = new_props - current_props
                removed_props = current_props - new_props
                potentially_unchanged = current_props & new_props

                # Check for type changes in "unchanged" properties
                actually_unchanged = set()
                type_changed = set()

                for prop_name in potentially_unchanged:
                    old_type = current_schema[prop_name]['notion_type']
                    new_type = properties[prop_name].get('type', 'rich_text')

                    if old_type != new_type:
                        type_changed.add(prop_name)
                        logger.info(f"Property type changed: {prop_name} ({old_type} → {new_type})")
                    else:
                        actually_unchanged.add(prop_name)

                result = {
                    'added': [],
                    'removed': [],
                    'unchanged': list(actually_unchanged),
                    'type_changed': []
                }

                # Detect likely property renames (heuristic)
                if len(added_props) > 0 and len(added_props) == len(removed_props):
                    logger.warning(
                        f"⚠️ Detected {len(added_props)} properties added and {len(removed_props)} removed. "
                        f"This may indicate property renames in Notion. "
                        f"If properties were renamed, their embeddings will be recreated. "
                        f"Removed: {', '.join(removed_props)} | Added: {', '.join(added_props)}"
                    )

                # Add new properties to schema
                for prop_name in added_props:
                    prop_data = properties[prop_name]
                    notion_type = prop_data.get('type', 'rich_text')
                    column_name = self.sanitize_property_name(prop_name)
                    sqlite_type = self.get_sqlite_type_for_notion_property(notion_type)

                    # Check if column name already exists (collision)
                    cursor.execute("""
                        SELECT property_name FROM notion_property_schema
                        WHERE table_name = ? AND column_name = ? AND is_active = TRUE
                    """, (table_name, column_name))

                    existing = cursor.fetchone()
                    if existing:
                        # Column name collision - append a suffix
                        suffix = 1
                        original_column = column_name
                        while existing:
                            column_name = f"{original_column}_{suffix}"
                            cursor.execute("""
                                SELECT property_name FROM notion_property_schema
                                WHERE table_name = ? AND column_name = ? AND is_active = TRUE
                            """, (table_name, column_name))
                            existing = cursor.fetchone()
                            suffix += 1

                        logger.warning(f"Column name collision for '{prop_name}', using '{column_name}'")

                    # Check if property already exists (may be inactive)
                    cursor.execute("""
                        SELECT added_time, is_active, column_name FROM notion_property_schema
                        WHERE database_id = ? AND property_name = ?
                    """, (database_id, prop_name))

                    existing = cursor.fetchone()
                    if existing:
                        # Property exists (maybe inactive) - reactivate and update it
                        added_time_to_use = existing[0]  # Keep original added_time
                        existing_column_name = existing[2]  # Keep existing column name!

                        # Use existing column name to maintain consistency
                        column_name = existing_column_name

                        cursor.execute("""
                            UPDATE notion_property_schema
                            SET property_type = ?, notion_type = ?,
                                is_active = TRUE, last_seen = ?, table_name = ?
                            WHERE database_id = ? AND property_name = ?
                        """, (sqlite_type, notion_type, now, table_name,
                              database_id, prop_name))

                        if not existing[1]:  # was inactive
                            logger.info(f"Reactivated previously removed property: {prop_name} (column: {column_name})")
                    else:
                        # New property - insert it
                        cursor.execute("""
                            INSERT INTO notion_property_schema (
                                database_id, database_name, table_name, property_name,
                                column_name, property_type, notion_type, added_time, last_seen
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (database_id, database_name, table_name, prop_name,
                              column_name, sqlite_type, notion_type, now, now))

                    result['added'].append({
                        'property_name': prop_name,
                        'column_name': column_name,
                        'sqlite_type': sqlite_type,
                        'notion_type': notion_type
                    })

                # Mark removed properties as inactive
                for prop_name in removed_props:
                    cursor.execute("""
                        UPDATE notion_property_schema
                        SET is_active = FALSE
                        WHERE database_id = ? AND property_name = ?
                    """, (database_id, prop_name))

                    result['removed'].append({
                        'property_name': prop_name,
                        'column_name': current_schema[prop_name]['column_name']
                    })

                # Delete orphaned property embeddings for removed properties
                if removed_props:
                    try:
                        # Load vector config to check if embeddings are enabled
                        from promaia.utils.env_writer import get_config_path
                        config_path = str(get_config_path())
                        with open(config_path, 'r') as f:
                            config = json.load(f)
                        vector_config = config.get('global', {}).get('vector_search', {})

                        if vector_config.get('enabled') and vector_config.get('property_embeddings', {}).get('enabled'):
                            from promaia.storage.vector_db import VectorDBManager
                            vector_db = VectorDBManager(chroma_path=vector_config.get('chroma_path'))

                            EMBEDDABLE_TYPES = {'title', 'text', 'rich_text', 'relation'}

                            for prop_name in removed_props:
                                prop_info = current_schema[prop_name]
                                # Only delete embeddings for embeddable types
                                if prop_info['notion_type'] in EMBEDDABLE_TYPES:
                                    deleted_count = vector_db.delete_property_embeddings(
                                        property_name=prop_info['column_name'],
                                        database_id=database_id
                                    )
                                    logger.info(f"🗑️ Deleted {deleted_count} embeddings for removed property: {prop_name}")
                    except Exception as e:
                        logger.warning(f"Could not delete orphaned property embeddings: {e}")

                # Handle property type changes
                if type_changed:
                    try:
                        # Load vector config to check if embeddings are enabled
                        from promaia.utils.env_writer import get_config_path
                        config_path = str(get_config_path())
                        with open(config_path, 'r') as f:
                            config = json.load(f)
                        vector_config = config.get('global', {}).get('vector_search', {})

                        if vector_config.get('enabled') and vector_config.get('property_embeddings', {}).get('enabled'):
                            from promaia.storage.vector_db import VectorDBManager
                            vector_db = VectorDBManager(chroma_path=vector_config.get('chroma_path'))

                            EMBEDDABLE_TYPES = {'title', 'text', 'rich_text', 'relation'}

                            for prop_name in type_changed:
                                old_type = current_schema[prop_name]['notion_type']
                                new_type = properties[prop_name].get('type', 'rich_text')
                                col_name = current_schema[prop_name]['column_name']

                                # Delete old embeddings if old type was embeddable
                                if old_type in EMBEDDABLE_TYPES:
                                    deleted_count = vector_db.delete_property_embeddings(
                                        property_name=col_name,
                                        database_id=database_id
                                    )
                                    logger.info(f"🗑️ Deleted {deleted_count} embeddings for type-changed property: {prop_name} ({old_type} → {new_type})")

                                # Update schema with new type
                                cursor.execute("""
                                    UPDATE notion_property_schema
                                    SET notion_type = ?, last_seen = ?
                                    WHERE database_id = ? AND property_name = ?
                                """, (new_type, now, database_id, prop_name))

                                result['type_changed'].append({
                                    'property_name': prop_name,
                                    'column_name': col_name,
                                    'old_type': old_type,
                                    'new_type': new_type
                                })
                    except Exception as e:
                        logger.warning(f"Could not handle property type changes: {e}")

                # Update last_seen for unchanged properties
                for prop_name in actually_unchanged:
                    cursor.execute("""
                        UPDATE notion_property_schema
                        SET last_seen = ?
                        WHERE database_id = ? AND property_name = ?
                    """, (now, database_id, prop_name))

                conn.commit()

                if result['added']:
                    logger.info(f"Added {len(result['added'])} properties to schema for {database_name}")
                if result['removed']:
                    logger.info(f"Removed {len(result['removed'])} properties from schema for {database_name}")
                if result['type_changed']:
                    logger.info(f"Type changed for {len(result['type_changed'])} properties in schema for {database_name}")

                return result

        except Exception as e:
            logger.error(f"Error updating property schema: {e}")
            return {'added': [], 'removed': [], 'unchanged': [], 'type_changed': []}

    def apply_schema_changes(self, table_name: str, schema_changes: Dict[str, Any],
                            remove_columns: bool = False) -> bool:
        """
        Apply schema changes to a table (add/remove columns).

        Args:
            table_name: Name of the table to modify
            schema_changes: Dict from update_property_schema with 'added' and 'removed' lists
            remove_columns: If True, remove columns for removed properties (dangerous!)

        Returns:
            True if successful, False otherwise
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Add new columns
                for prop in schema_changes.get('added', []):
                    column_name = prop['column_name']
                    sqlite_type = prop['sqlite_type']

                    # Check if column already exists
                    cursor.execute(f"PRAGMA table_info({table_name})")
                    columns = {row[1] for row in cursor.fetchall()}

                    if column_name in columns:
                        logger.warning(f"Column '{column_name}' already exists in {table_name}, skipping")
                        continue

                    # Add the column
                    alter_query = f"ALTER TABLE {table_name} ADD COLUMN {column_name} {sqlite_type}"
                    logger.info(f"Adding column: {alter_query}")

                    try:
                        cursor.execute(alter_query)
                    except sqlite3.OperationalError as e:
                        logger.error(f"Failed to add column '{column_name}' to {table_name}: {e}")
                        continue

                # Remove columns (if requested and supported)
                # Note: SQLite doesn't support DROP COLUMN directly in older versions
                # This is a destructive operation and should be used carefully
                if remove_columns and schema_changes.get('removed'):
                    logger.warning(f"Column removal requested for {table_name}")

                    # Check SQLite version
                    cursor.execute("SELECT sqlite_version()")
                    version = cursor.fetchone()[0]
                    major_version = int(version.split('.')[0])
                    minor_version = int(version.split('.')[1])

                    # DROP COLUMN supported in SQLite 3.35.0+
                    if major_version > 3 or (major_version == 3 and minor_version >= 35):
                        for prop in schema_changes['removed']:
                            column_name = prop['column_name']
                            drop_query = f"ALTER TABLE {table_name} DROP COLUMN {column_name}"
                            logger.warning(f"Removing column: {drop_query}")

                            try:
                                cursor.execute(drop_query)
                            except sqlite3.OperationalError as e:
                                logger.error(f"Failed to drop column '{column_name}' from {table_name}: {e}")
                    else:
                        logger.warning(f"SQLite version {version} does not support DROP COLUMN. "
                                     f"Columns marked inactive in schema but not removed from table.")

                conn.commit()
                return True

        except Exception as e:
            logger.error(f"Error applying schema changes to {table_name}: {e}")
            return False

    def sync_table_schema_with_properties(self, database_id: str, database_name: str,
                                         properties: Dict[str, Any],
                                         workspace: str = None,
                                         remove_columns: bool = False) -> bool:
        """
        Synchronize a table's schema with Notion properties.

        This is the main entry point for schema synchronization. It:
        1. Determines the correct table for the database
        2. Updates the property schema tracking
        3. Applies changes to the actual database table

        Args:
            database_id: Notion database ID
            database_name: Notion database name
            properties: Dict of Notion properties
            workspace: Workspace name (for determining table name)
            remove_columns: If True, remove columns for deleted properties (default: False)

        Returns:
            True if successful, False otherwise
        """
        try:
            # Determine table name using workspace-specific naming
            if workspace and database_name:
                # ALL Notion databases use workspace-specific tables now
                table_name = f"notion_{workspace}_{database_name}"
            else:
                # Fallback for backwards compatibility with old data
                table_mapping = {
                    'journal': 'notion_journal',
                    'stories': 'notion_stories',
                    'cms': 'notion_cms',
                }
                table_name = table_mapping.get(database_name, 'generic_content')

            # Skip universal properties that are handled separately
            excluded_props = {
                'Title', 'title',
                'Created time', 'created_time',
                'Last edited time', 'last_edited_time',
                'Created by', 'created_by',
                'Last edited by', 'last_edited_by'
            }

            filtered_properties = {k: v for k, v in properties.items()
                                  if k not in excluded_props}

            # Update schema tracking
            schema_changes = self.update_property_schema(
                database_id, database_name, table_name, filtered_properties
            )

            # Apply changes to table (only if not generic_content)
            if table_name != 'generic_content':
                # Ensure table exists before trying to add columns
                if table_name.startswith('notion_') and table_name not in ['notion_journal', 'notion_stories', 'notion_cms']:
                    # This is a workspace-specific table, ensure it exists
                    self._ensure_notion_table_exists(table_name)

                # Check for missing columns (properties in schema but not in table)
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()

                    # Get current table columns
                    cursor.execute(f"PRAGMA table_info({table_name})")
                    existing_columns = {row[1] for row in cursor.fetchall()}

                    # Get all active properties from schema
                    cursor.execute("""
                        SELECT column_name, property_name, notion_type, property_type
                        FROM notion_property_schema
                        WHERE database_id = ? AND is_active = TRUE
                    """, (database_id,))

                    # Add missing columns to the "added" list
                    for row in cursor.fetchall():
                        col_name, prop_name, notion_type, sqlite_type = row
                        if col_name not in existing_columns:
                            logger.info(f"Found missing column in table: {col_name} (adding to sync)")
                            schema_changes['added'].append({
                                'property_name': prop_name,
                                'column_name': col_name,
                                'sqlite_type': sqlite_type,
                                'notion_type': notion_type
                            })

                success = self.apply_schema_changes(table_name, schema_changes, remove_columns)
                if not success:
                    logger.error(f"Failed to apply schema changes to {table_name}")
                    return False

                # Rebuild unified view to include new table or columns
                try:
                    self.rebuild_unified_content_view()
                except Exception as view_error:
                    logger.warning(f"Failed to rebuild unified view after schema sync: {view_error}")

            logger.info(f"Schema synchronized for {database_name} ({table_name})")
            return True

        except Exception as e:
            logger.error(f"Error synchronizing table schema: {e}")
            return False

    def close(self):
        """Close the database connection."""
        # The connection is now managed with 'with' statements, so this is less critical
        # but can be kept for explicit closure if needed elsewhere.


# Global instance
_hybrid_registry = None

def get_hybrid_registry(db_path: str = None) -> HybridContentRegistry:
    """Get the global hybrid content registry instance."""
    global _hybrid_registry
    if _hybrid_registry is None:
        _hybrid_registry = HybridContentRegistry(db_path)
    return _hybrid_registry 