from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

CALLBACK_DELIM = '::'


def main_menu_keyboard():
    """القائمة الرئيسية - ReplyKeyboardMarkup"""
    kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    kb.row(KeyboardButton('🎬 عرض كل الفيديوهات'), KeyboardButton('🔥 الفيديوهات الشائعة'))
    kb.row(KeyboardButton('⭐ المفضلة'), KeyboardButton('📺 سجل المشاهدة'))
    kb.row(KeyboardButton('🍿 اقترح لي فيلم'), KeyboardButton('🔍 بحث'))
    return kb


def inline_search_choice_keyboard():
    """خيارات البحث - InlineKeyboardMarkup"""
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton('🔎 بحث عادي', callback_data='st:n'),
        InlineKeyboardButton('⚙️ بحث متقدم', callback_data='st:a')
    )
    return kb


def video_action_keyboard(video_id, user_id, is_fav=False, user_rating=0):
    """أزرار إجراءات الفيديو - row_width=1 للأزرار الكبيرة"""
    kb = InlineKeyboardMarkup(row_width=1)
    
    # زر المفضلة
    if is_fav:
        kb.add(InlineKeyboardButton('⭐ إزالة من المفضلة', callback_data=f'fav:r:{video_id}'))
    else:
        kb.add(InlineKeyboardButton('☆ إضافة للمفضلة', callback_data=f'fav:a:{video_id}'))
    
    # زر التقييم (زر واحد يفتح قائمة التقييم)
    rating_text = f'⭐ تقييمك: {user_rating}' if user_rating > 0 else '⭐ قيّم الفيديو'
    kb.add(InlineKeyboardButton(rating_text, callback_data=f'rate:{video_id}'))
    
    # زر التعليق
    kb.add(InlineKeyboardButton('💬 تعليق', callback_data=f'com:{video_id}'))
    
    # زر الرجوع
    kb.add(InlineKeyboardButton('🔙 رجوع', callback_data='back'))
    
    return kb


def rating_keyboard(video_id):
    """أزرار التقييم من 1 إلى 5"""
    kb = InlineKeyboardMarkup(row_width=3)
    for i in range(1, 6):
        kb.add(InlineKeyboardButton(f'{i}⭐', callback_data=f'rt:{video_id}:{i}'))
    kb.add(InlineKeyboardButton('🔙 رجوع', callback_data='back'))
    return kb


def paginated_keyboard(items, total_count, current_page, action_prefix, context=''):
    """أزرار التنقل بين الصفحات - row_width=1 للأزرار الكبيرة"""
    kb = InlineKeyboardMarkup(row_width=1)
    
    for item in items:
        # اختصار callback_data لتجنب تجاوز 64 حرف
        title = item.get('display_title', '')[:30]
        kb.add(InlineKeyboardButton(title, callback_data=f"{action_prefix}:{item['id']}"))
    
    # أزرار التنقل
    nav_kb = InlineKeyboardMarkup(row_width=2)
    if current_page > 0:
        nav_kb.add(InlineKeyboardButton('⬅️ السابق', callback_data=f'{action_prefix}:p:{current_page-1}'))
    if (current_page+1)*len(items) < total_count:
        nav_kb.add(InlineKeyboardButton('التالي ➡️', callback_data=f'{action_prefix}:p:{current_page+1}'))
    nav_kb.add(InlineKeyboardButton('🔙 الرئيسية', callback_data='back'))
    
    return kb, nav_kb


def category_keyboard(categories):
    """أزرار التصنيفات - row_width=1"""
    kb = InlineKeyboardMarkup(row_width=1)
    for cat in categories:
        kb.add(InlineKeyboardButton(cat['name'], callback_data=f"cat:{cat['id']}"))
    kb.add(InlineKeyboardButton('🔙 رجوع', callback_data='back'))
    return kb


def subcategory_keyboard(subcategories, parent_id):
    """أزرار التصنيفات الفرعية - row_width=1"""
    kb = InlineKeyboardMarkup(row_width=1)
    for sub in subcategories:
        kb.add(InlineKeyboardButton(sub['name'], callback_data=f"sub:{sub['id']}"))
    kb.add(InlineKeyboardButton('🔙 رجوع', callback_data=f'cat:{parent_id}'))
    return kb
