from ..database import execute


def get_categories():
    return execute('SELECT * FROM categories ORDER BY name', fetch='all')


def get_category(category_id):
    return execute('SELECT * FROM categories WHERE id = %s', (category_id,), fetch='one')


def add_category(name, parent_id=None):
    parent_id_val = parent_id if parent_id else None
    return execute('INSERT INTO categories (name, parent_id, full_path) VALUES (%s, %s, %s) RETURNING id',
                   (name, parent_id_val, name if not parent_id else None), fetch='one')


def get_child_categories(parent_id=None):
    if parent_id is None:
        return execute('SELECT * FROM categories WHERE parent_id IS NULL ORDER BY name', fetch='all')
    return execute('SELECT * FROM categories WHERE parent_id = %s ORDER BY name', (parent_id,), fetch='all')
