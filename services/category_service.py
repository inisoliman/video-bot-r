
# services/category_service.py

import logging
from repositories import category_repository

logger = logging.getLogger(__name__)

def add_new_category(name, parent_id=None):
    return category_repository.add_category(name, parent_id)

def get_category_details(category_id):
    return category_repository.get_category_by_id(category_id)

def get_category_by_name_and_parent(name, parent_id=None):
    return category_repository.get_category_by_name(name, parent_id)

def get_child_categories(parent_id=None):
    return category_repository.get_child_categories(parent_id)

def get_all_categories_tree():
    return category_repository.get_categories_tree()

def delete_existing_category(category_id):
    return category_repository.delete_category(category_id)

def update_category_name(category_id, new_name):
    return category_repository.update_category_name(category_id, new_name)

def get_active_category_for_user(user_id):
    return category_repository.get_active_category_id(user_id)

def build_category_tree_display(categories):
    """
    Organizes categories into a hierarchical tree structure with emojis.
    """
    tree = []
    cats_by_parent = {}
    
    for cat in categories:
        parent_id = cat.get("parent_id")
        if parent_id not in cats_by_parent:
            cats_by_parent[parent_id] = []
        cats_by_parent[parent_id].append(cat)
    
    def insert_cats(parent_id, prefix="", level=0):
        from config.constants import EMOJI_FOLDER, EMOJI_LEAF, EMOJI_DIAMOND
        children = cats_by_parent.get(parent_id, [])
        
        for child in sorted(children, key=lambda x: x["name"]):
            if level == 0:
                emoji = EMOJI_FOLDER
                display_name = f"{emoji} {child["name"]}"
            elif level == 1:
                emoji = EMOJI_LEAF
                display_name = f"{prefix}└─ {emoji} {child["name"]}"
            else:
                emoji = EMOJI_DIAMOND
                display_name = f"{prefix}└─ {emoji} {child["name"]}"
            
            tree.append({
                "id": child["id"],
                "name": display_name,
                "original_name": child["name"],
                "level": level,
                "parent_id": parent_id
            })
            
            next_prefix = prefix + ("    " if level == 0 else "  ")
            insert_cats(child["id"], next_prefix, level + 1)
    
    insert_cats(None, "", 0)
    
    return tree
