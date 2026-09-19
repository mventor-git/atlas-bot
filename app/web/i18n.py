"""Minimal EN/AR strings (ticket-037). No Babel; dict lookup + dir switch.

Language lives in a plain cookie (display preference only, never auth).
Semantic RTL via <html dir> + CSS logical properties, not mirroring hacks.
"""
from __future__ import annotations

from flask import request

STRINGS = {
    "app_name": {"en": "Atlas HQ", "ar": "أطلس HQ"},
    "login": {"en": "Sign in", "ar": "تسجيل الدخول"},
    "username": {"en": "Username", "ar": "اسم المستخدم"},
    "password": {"en": "Password", "ar": "كلمة المرور"},
    "logout": {"en": "Sign out", "ar": "تسجيل الخروج"},
    "dashboard": {"en": "Dashboard", "ar": "اللوحة"},
    "hr": {"en": "HR", "ar": "الموارد البشرية"},
    "dev": {"en": "DEV", "ar": "الإدارة"},
    "site": {"en": "Site", "ar": "الموقع"},
    "no_data": {"en": "No data yet", "ar": "لا بيانات بعد"},
    "planned": {"en": "Planned", "ar": "مخطط له"},
    "readonly": {"en": "Read-only", "ar": "للقراءة فقط"},
    "save": {"en": "Save", "ar": "حفظ"},
    "cancel": {"en": "Cancel", "ar": "إلغاء"},
    "approve": {"en": "Approve", "ar": "اعتماد"},
    "reject": {"en": "Reject", "ar": "رفض"},
    "confirm": {"en": "Confirm", "ar": "تأكيد"},
    "back": {"en": "Back", "ar": "رجوع"},
    "search": {"en": "Search", "ar": "بحث"},
    "status": {"en": "Status", "ar": "الحالة"},
    "actions": {"en": "Actions", "ar": "إجراءات"},
    "details": {"en": "Details", "ar": "التفاصيل"},
    "forbidden": {"en": "Not allowed.", "ar": "غير مسموح."},
    "not_found": {"en": "Not found.", "ar": "غير موجود."},
    "sign_in_failed": {"en": "Wrong username or password.", "ar": "خطأ في الاسم أو كلمة المرور."},
}

NAV = [
    ("today", {"en": "Today Board", "ar": "لوحة اليوم"}),
    ("projects", {"en": "Projects / Sites", "ar": "المشاريع / المواقع"}),
    ("reports", {"en": "Daily Reports", "ar": "التقارير اليومية"}),
    ("approvals", {"en": "Approvals", "ar": "الاعتمادات"}),
    ("attendance", {"en": "Attendance", "ar": "الحضور"}),
    ("employees", {"en": "Employees", "ar": "الموظفون"}),
    ("hr", {"en": "HR", "ar": "الموارد البشرية"}),
    ("grievances", {"en": "Grievances", "ar": "التظلمات"}),
    ("warnings", {"en": "Warnings", "ar": "الإنذارات"}),
    ("notifications", {"en": "Notifications", "ar": "الإشعارات"}),
    ("documents", {"en": "Documents", "ar": "المستندات"}),
    ("stats", {"en": "Reports", "ar": "التقارير"}),
    ("config", {"en": "Configuration", "ar": "الإعدادات"}),
    ("audit", {"en": "Audit", "ar": "التدقيق"}),
    ("chat", {"en": "Atlas Chat", "ar": "محادثة أطلس"}),
]

LANGS = ("en", "ar")


def get_lang() -> str:
    lang = request.cookies.get("atlas_lang", "en")
    return lang if lang in LANGS else "en"


def t(key: str, lang: str | None = None) -> str:
    lang = lang or get_lang()
    return STRINGS.get(key, {}).get(lang, STRINGS.get(key, {}).get("en", key))


def nav_items(lang: str):
    return [(slug, label.get(lang, label["en"])) for slug, label in NAV]


SECTIONS = [
    ({"en": "Operate", "ar": "التشغيل"}, ["today", "reports", "approvals", "attendance"]),
    ({"en": "People", "ar": "الأفراد"},
     ["employees", "hr", "grievances", "warnings"]),
    ({"en": "Manage", "ar": "الإدارة"},
     ["notifications", "documents", "stats"]),
    ({"en": "System", "ar": "النظام"},
     ["projects", "config", "audit", "chat"]),
]


def section_items(lang: str):
    labels = {slug: label.get(lang, label["en"]) for slug, label in NAV}
    return [((title.get(lang, title["en"])), [s for s in slugs])
            for title, slugs in SECTIONS
            if any(s in labels for s in slugs)]


def slug_label(slug: str, lang: str) -> str:
    for s, label in NAV:
        if s == slug:
            return label.get(lang, label["en"])
    return slug
