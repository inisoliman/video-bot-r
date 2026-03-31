CREATE TABLE IF NOT EXISTS categories (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  parent_id INTEGER REFERENCES categories(id) ON DELETE CASCADE,
  full_path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS video_archive (
  id SERIAL PRIMARY KEY,
  message_id BIGINT UNIQUE,
  caption TEXT,
  chat_id BIGINT,
  file_name TEXT,
  file_id TEXT,
  category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
  metadata JSONB,
  view_count INTEGER DEFAULT 0,
  upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  grouping_key TEXT,
  thumbnail_file_id TEXT,
  content_type TEXT DEFAULT 'VIDEO'
);

CREATE TABLE IF NOT EXISTS required_channels (
  channel_id BIGINT PRIMARY KEY,
  channel_name TEXT
);

CREATE TABLE IF NOT EXISTS bot_users (
  user_id BIGINT PRIMARY KEY,
  username TEXT,
  first_name TEXT,
  join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bot_settings (
  setting_key TEXT PRIMARY KEY,
  setting_value TEXT
);

CREATE TABLE IF NOT EXISTS video_ratings (
  id SERIAL PRIMARY KEY,
  video_id INTEGER REFERENCES video_archive(id) ON DELETE CASCADE,
  user_id BIGINT,
  rating INTEGER,
  UNIQUE(video_id, user_id)
);

CREATE TABLE IF NOT EXISTS user_states (
  user_id BIGINT PRIMARY KEY,
  state TEXT,
  context JSONB
);

CREATE TABLE IF NOT EXISTS user_favorites (
  id SERIAL PRIMARY KEY,
  user_id BIGINT,
  video_id INTEGER REFERENCES video_archive(id) ON DELETE CASCADE,
  date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(user_id, video_id)
);

CREATE TABLE IF NOT EXISTS user_history (
  id SERIAL PRIMARY KEY,
  user_id BIGINT,
  video_id INTEGER REFERENCES video_archive(id) ON DELETE CASCADE,
  last_watched TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(user_id, video_id)
);

CREATE TABLE IF NOT EXISTS video_comments (
  id SERIAL PRIMARY KEY,
  video_id INTEGER REFERENCES video_archive(id) ON DELETE CASCADE,
  user_id BIGINT NOT NULL,
  username TEXT,
  comment_text TEXT NOT NULL,
  admin_reply TEXT,
  is_read BOOLEAN DEFAULT FALSE,
  replied_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
