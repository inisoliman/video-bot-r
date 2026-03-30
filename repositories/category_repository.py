
# repositories/category_repository.py

from core.db import execute_query
import logging

logger = logging.getLogger(__name__)

def add_category(name, parent_id=None):
    query = "INSERT INTO categories (name, parent_id, full_path) VALUES (%s, %s, %s) RETURNING id;"
    # Placeholder for full_path, will be updated after insertion if needed
    result = execute_query(query, (name, parent_id, ""), fetch="one", commit=True)
    if result:
        category_id = result["id"]
        # Update full_path after getting the ID
        update_full_path(category_id)
        return category_id
    return None

def update_full_path(category_id):
    # This function needs to be more sophisticated to build the full path recursively
    # For now, a simple placeholder or direct update if parent_id is known
    query = """
        WITH RECURSIVE category_path AS (
            SELECT id, name, parent_id, name as path_name
            FROM categories
            WHERE id = %s
            UNION ALL
            SELECT c.id, c.name, c.parent_id, cp.path_name || ' > ' || c.name
            FROM categories c
            JOIN category_path cp ON c.id = cp.parent_id
        )
        UPDATE categories
        SET full_path = (SELECT path_name FROM category_path WHERE id = %s)
        WHERE id = %s;
    """
    execute_query(query, (category_id, category_id, category_id), commit=True)

def get_category_by_id(category_id):
    query = "SELECT * FROM categories WHERE id = %s;"
    return execute_query(query, (category_id,), fetch="one")

def get_category_by_name(name, parent_id=None):
    if parent_id is None:
        query = "SELECT * FROM categories WHERE name = %s AND parent_id IS NULL;"
        return execute_query(query, (name,), fetch="one")
    else:
        query = "SELECT * FROM categories WHERE name = %s AND parent_id = %s;"
        return execute_query(query, (name, parent_id), fetch="one")

def get_child_categories(parent_id=None):
    if parent_id is None:
        query = "SELECT * FROM categories WHERE parent_id IS NULL ORDER BY name;"
        return execute_query(query, fetch="all")
    else:
        query = "SELECT * FROM categories WHERE parent_id = %s ORDER BY name;"
        return execute_query(query, (parent_id,), fetch="all")

def get_categories_tree():
    query = "SELECT id, name, parent_id FROM categories ORDER BY parent_id NULLS FIRST, name;"
    return execute_query(query, fetch="all")

def delete_category(category_id):
    query = "DELETE FROM categories WHERE id = %s;"
    return execute_query(query, (category_id,), commit=True)

def update_category_name(category_id, new_name):
    query = "UPDATE categories SET name = %s WHERE id = %s;"
    return execute_query(query, (new_name, category_id), commit=True)

def get_active_category_id(user_id):
    # This function seems to be related to user state, might need to be moved or re-evaluated
    # For now, keeping it here as it interacts with categories directly
    query = "SELECT context->>‘active_category_id’ FROM user_states WHERE user_id = %s;"
    result = execute_query(query, (user_id,), fetch="one")
    return int(result[0]) if result and result[0] else None
