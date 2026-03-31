from ..database import execute


def set_active_category_id(category_id):
    return execute('INSERT INTO bot_settings (setting_key, setting_value) VALUES (%s, %s) ON CONFLICT (setting_key) DO UPDATE SET setting_value = EXCLUDED.setting_value',
                   ('active_category_id', str(category_id)))


def get_active_category_id():
    row = execute('SELECT setting_value FROM bot_settings WHERE setting_key = %s', ('active_category_id',), fetch='one')
    return int(row['setting_value']) if row and row['setting_value'] and str(row['setting_value']).isdigit() else None
