#!/usr/bin/env python3
"""
Audit Viewer GUI — Track user-contributor activity.

Displays two listboxes:
  Left:  User IDs (roles other than viewer)
  Right: Contractors (grouped by type)

When a user AND contractor are selected, shows a table of values entered
by that user for that contractor with timestamps and who finalized the report.

Usage:
    python -m scripts.audit_viewer_gui
"""

import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
from typing import Optional

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database.manager import DatabaseManager

# ──────────────────────────────────────────────────────────────────────
# Database queries
# ──────────────────────────────────────────────────────────────────────


def get_users_excluding_viewer(db: DatabaseManager) -> list[dict]:
    """Get all registered users whose role is not 'viewer'.

    Returns:
        List of dicts: {telegram_user, role}
    """
    try:
        rows = db.execute("""
            SELECT telegram_user, role
            FROM users
            WHERE role != 'viewer'
            ORDER BY telegram_user
        """).fetchall()
        return [{"telegram_user": str(r[0]), "role": str(r[1])} for r in rows]
    except Exception as e:
        print(f"Error fetching users: {e}")
        return []


def get_contractors_grouped(db: DatabaseManager) -> list[dict]:
    """Get all contractors with their types.

    Returns:
        List of dicts: {name, type}
    """
    try:
        rows = db.execute("""
            SELECT name, type
            FROM contractors
            ORDER BY type, name
        """).fetchall()
        return [{"name": str(r[0]), "type": str(r[1] or "")} for r in rows]
    except Exception as e:
        print(f"Error fetching contractors: {e}")
        return []


def get_user_contractor_activity(
    db: DatabaseManager,
    telegram_user: str,
    contractor_name: str,
) -> list[dict]:
    """Get all 'added' activity for a user+contractor pair, with finalization info.

    Returns:
        List of dicts with keys:
            report_date, workers, zone, details,
            entry_timestamp, finalized_by, finalized_at
    """
    try:
        rows = db.execute("""
            SELECT
                a.report_date,
                a.workers,
                a.zone,
                a.details,
                a.timestamp,
                r.finalized_by,
                r.finalized_at
            FROM user_activity_log a
            LEFT JOIN reports r ON r.date = a.report_date
            WHERE a.action = 'added'
              AND a.telegram_user = ?
              AND a.contractor_name = ?
            ORDER BY a.timestamp DESC
        """, (telegram_user, contractor_name)).fetchall()
        return [
            {
                "report_date": str(r[0]) if r[0] else "",
                "workers": int(r[1]) if r[1] is not None else 0,
                "zone": str(r[2]) if r[2] else "",
                "details": str(r[3]) if r[3] else "",
                "entry_timestamp": str(r[4]) if r[4] else "",
                "finalized_by": str(r[5]) if r[5] else "—",
                "finalized_at": str(r[6]) if r[6] else "—",
            }
            for r in rows
        ]
    except Exception as e:
        print(f"Error fetching activity: {e}")
        return []


def find_db_path() -> Optional[str]:
    """Auto-detect the SQLite database path."""
    candidates = [
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "labor_report.db"),
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "labor_report.db"),
        "labor_report.db",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "labor_report.sqlite"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


# ──────────────────────────────────────────────────────────────────────
# GUI Application
# ──────────────────────────────────────────────────────────────────────


class AuditViewerApp:
    """Main application window."""

    def __init__(self, root: tk.Tk, db: DatabaseManager) -> None:
        self.root = root
        self.db = db

        self.root.title("Audit Viewer — User & Contractor Activity")
        self.root.geometry("1200x700")
        self.root.minsize(900, 500)

        # ── Top frame: listboxes ──
        top_frame = ttk.Frame(root, padding=10)
        top_frame.pack(fill=tk.BOTH, expand=True)

        # -- User listbox (left) --
        left_frame = ttk.LabelFrame(top_frame, text="Users (excl. viewer)", padding=5)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

        self.user_search_var = tk.StringVar()
        self.user_search_var.trace_add("write", lambda *_: self._filter_users())
        user_search_entry = ttk.Entry(left_frame, textvariable=self.user_search_var)
        user_search_entry.pack(fill=tk.X, pady=(0, 5))
        user_search_entry.insert(0, "Type to filter...")
        user_search_entry.bind("<FocusIn>", lambda e: user_search_entry.delete(0, tk.END) if user_search_entry.get() == "Type to filter..." else None)

        self.user_listbox = tk.Listbox(left_frame, font=("Consolas", 10))
        self.user_scroll = ttk.Scrollbar(left_frame, orient=tk.VERTICAL, command=self.user_listbox.yview)
        self.user_listbox.configure(yscrollcommand=self.user_scroll.set)
        self.user_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.user_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.user_listbox.bind("<<ListboxSelect>>", self._on_user_select)

        # -- Contractor listbox (right) --
        right_frame = ttk.LabelFrame(top_frame, text="Contractors (by type)", padding=5)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 0))

        self.contractor_search_var = tk.StringVar()
        self.contractor_search_var.trace_add("write", lambda *_: self._filter_contractors())
        contractor_search_entry = ttk.Entry(right_frame, textvariable=self.contractor_search_var)
        contractor_search_entry.pack(fill=tk.X, pady=(0, 5))
        contractor_search_entry.insert(0, "Type to filter...")
        contractor_search_entry.bind("<FocusIn>", lambda e: contractor_search_entry.delete(0, tk.END) if contractor_search_entry.get() == "Type to filter..." else None)

        self.contractor_listbox = tk.Listbox(right_frame, font=("Consolas", 10))
        self.contractor_scroll = ttk.Scrollbar(right_frame, orient=tk.VERTICAL, command=self.contractor_listbox.yview)
        self.contractor_listbox.configure(yscrollcommand=self.contractor_scroll.set)
        self.contractor_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.contractor_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.contractor_listbox.bind("<<ListboxSelect>>", self._on_contractor_select)

        # ── Bottom frame: results table ──
        bottom_frame = ttk.LabelFrame(root, text="Activity Log", padding=5)
        bottom_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        # Treeview for results
        columns = ("date", "workers", "zone", "details", "timestamp", "finalized_by", "finalized_at")
        self.tree = ttk.Treeview(bottom_frame, columns=columns, show="headings", height=12)
        self.tree.heading("date", text="Report Date")
        self.tree.heading("workers", text="Workers")
        self.tree.heading("zone", text="Zone")
        self.tree.heading("details", text="Details")
        self.tree.heading("timestamp", text="Entry Time")
        self.tree.heading("finalized_by", text="Finalized By")
        self.tree.heading("finalized_at", text="Finalized At")

        self.tree.column("date", width=100, minwidth=80)
        self.tree.column("workers", width=70, minwidth=50, anchor=tk.CENTER)
        self.tree.column("zone", width=80, minwidth=60)
        self.tree.column("details", width=150, minwidth=80)
        self.tree.column("timestamp", width=160, minwidth=120)
        self.tree.column("finalized_by", width=100, minwidth=80)
        self.tree.column("finalized_at", width=160, minwidth=120)

        tree_scroll_y = ttk.Scrollbar(bottom_frame, orient=tk.VERTICAL, command=self.tree.yview)
        tree_scroll_x = ttk.Scrollbar(bottom_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=tree_scroll_y.set, xscrollcommand=tree_scroll_x.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        tree_scroll_x.pack(side=tk.BOTTOM, fill=tk.X)

        # ── Status bar ──
        self.status_var = tk.StringVar(value="Select a user and a contractor to view activity")
        status_bar = ttk.Label(root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W, padding=5)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        # ── Load data ──
        self.all_users: list[dict] = []
        self.all_contractors: list[dict] = []
        self.selected_user: Optional[str] = None
        self.selected_contractor: Optional[str] = None

        self._load_data()

    # ─── Data loading ───

    def _load_data(self) -> None:
        """Load users and contractors from database."""
        self.all_users = get_users_excluding_viewer(self.db)
        self.all_contractors = get_contractors_grouped(self.db)
        self._refresh_user_list()
        self._refresh_contractor_list()
        self.status_var.set(f"Loaded {len(self.all_users)} users, {len(self.all_contractors)} contractors")

    def _refresh_user_list(self) -> None:
        """Refresh the user listbox with filtered data."""
        self.user_listbox.delete(0, tk.END)
        filter_text = self.user_search_var.get().strip().lower()
        for u in self.all_users:
            display = f"{u['telegram_user']}  [{u['role']}]"
            if filter_text and filter_text not in display.lower() and filter_text not in u['telegram_user']:
                continue
            self.user_listbox.insert(tk.END, display)

    def _refresh_contractor_list(self) -> None:
        """Refresh the contractor listbox with filtered data."""
        self.contractor_listbox.delete(0, tk.END)
        filter_text = self.contractor_search_var.get().strip().lower()
        for c in self.all_contractors:
            display = f"{c['name']}  ({c['type']})" if c['type'] else c['name']
            if filter_text and filter_text not in display.lower():
                continue
            self.contractor_listbox.insert(tk.END, display)

    # ─── Filtering ───

    def _filter_users(self) -> None:
        self._refresh_user_list()
        self._maybe_query()

    def _filter_contractors(self) -> None:
        self._refresh_contractor_list()
        self._maybe_query()

    # ─── Selection handling ───

    def _on_user_select(self, event: tk.Event) -> None:
        selection = self.user_listbox.curselection()
        if selection:
            raw = self.user_listbox.get(selection[0])
            self.selected_user = raw.split()[0]  # First token is the ID
        else:
            self.selected_user = None
        self._maybe_query()

    def _on_contractor_select(self, event: tk.Event) -> None:
        selection = self.contractor_listbox.curselection()
        if selection:
            raw = self.contractor_listbox.get(selection[0])
            self.selected_contractor = raw.split("  (")[0]  # Before "  (Type)"
        else:
            self.selected_contractor = None
        self._maybe_query()

    def _maybe_query(self) -> None:
        """If both user and contractor are selected, query and display."""
        if self.selected_user and self.selected_contractor:
            self._query_activity()
        else:
            # Clear table
            for item in self.tree.get_children():
                self.tree.delete(item)

    def _query_activity(self) -> None:
        """Query the database and populate the tree."""
        # Clear existing
        for item in self.tree.get_children():
            self.tree.delete(item)

        if not self.selected_user or not self.selected_contractor:
            self.status_var.set("Select both a user and a contractor")
            return

        rows = get_user_contractor_activity(self.db, self.selected_user, self.selected_contractor)

        if not rows:
            self.status_var.set(
                f"No activity found for user {self.selected_user} / contractor '{self.selected_contractor}'"
            )
            return

        for r in rows:
            self.tree.insert("", tk.END, values=(
                r["report_date"],
                r["workers"],
                r["zone"],
                r["details"],
                r["entry_timestamp"],
                r["finalized_by"],
                r["finalized_at"],
            ))

        self.status_var.set(
            f"User: {self.selected_user} | Contractor: {self.selected_contractor} | "
            f"Entries: {len(rows)}"
        )


# ──────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────


def main() -> None:
    db_path = find_db_path()
    if not db_path:
        print("ERROR: Could not find the database. Make sure the app has been started at least once.")
        sys.exit(1)

    db = DatabaseManager(db_path)

    root = tk.Tk()
    app = AuditViewerApp(root, db)
    root.mainloop()


if __name__ == "__main__":
    main()
