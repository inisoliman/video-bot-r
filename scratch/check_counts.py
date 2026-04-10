# scratch/check_counts.py
import os
import psycopg2
from urllib.parse import urlparse

DATABASE_URL = os.environ.get('DATABASE_URL')
result = urlparse(DATABASE_URL)
conn = psycopg2.connect(
    user=result.username,
    password=result.password,
    host=result.hostname,
    port=result.port,
    dbname=result.path[1:]
)
cur = conn.cursor()
cur.execute("SELECT content_type, COUNT(*) FROM video_archive GROUP BY content_type")
rows = cur.fetchall()
for row in rows:
    print(f"{row[0]}: {row[1]}")
cur.close()
conn.close()
