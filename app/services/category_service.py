from ..repositories import category_repo


def get_categories():
    return category_repo.get_categories()


def get_category(category_id):
    return category_repo.get_category(category_id)


def add_category(name, parent_id=None):
    return category_repo.add_category(name, parent_id)


def get_child_categories(parent_id=None):
    return category_repo.get_child_categories(parent_id)
