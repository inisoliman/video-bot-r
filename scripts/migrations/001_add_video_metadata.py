# Migration: Add video metadata columns
# Version: 001_add_video_metadata
# Description: Add duration, file_size, resolution, and bitrate columns to videos table

class Migration:
    name = "Add video metadata columns"
    checksum = "a1b2c3d4e5f6"

    def up(self, cursor):
        """Apply the migration"""
        # إضافة أعمدة البيانات الوصفية للفيديوهات
        cursor.execute("""
            ALTER TABLE videos
            ADD COLUMN IF NOT EXISTS duration INTEGER,
            ADD COLUMN IF NOT EXISTS file_size BIGINT,
            ADD COLUMN IF NOT EXISTS resolution VARCHAR(20),
            ADD COLUMN IF NOT EXISTS bitrate INTEGER,
            ADD COLUMN IF NOT EXISTS codec VARCHAR(50),
            ADD COLUMN IF NOT EXISTS audio_codec VARCHAR(50)
        """)

        # إضافة فهارس للأعمدة الجديدة
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_videos_duration ON videos (duration);
            CREATE INDEX IF NOT EXISTS idx_videos_file_size ON videos (file_size);
        """)

        print("Added video metadata columns and indexes")

    def down(self, cursor):
        """Rollback the migration"""
        # حذف الفهارس
        cursor.execute("""
            DROP INDEX IF EXISTS idx_videos_duration;
            DROP INDEX IF EXISTS idx_videos_file_size;
        """)

        # حذف الأعمدة
        cursor.execute("""
            ALTER TABLE videos
            DROP COLUMN IF EXISTS duration,
            DROP COLUMN IF EXISTS file_size,
            DROP COLUMN IF EXISTS resolution,
            DROP COLUMN IF EXISTS bitrate,
            DROP COLUMN IF EXISTS codec,
            DROP COLUMN IF EXISTS audio_codec
        """)

        print("Removed video metadata columns and indexes")