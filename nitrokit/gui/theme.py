"""Visual system for Nitro GFX: a calm dark theme with one accent colour.

Every panel uses the same few tokens, so the look stays consistent and a
colour change is a one-line edit here.
"""
import tkinter as tk
from tkinter import ttk

BG = '#0b0f17'          # window
PANEL = '#121826'       # cards
PANEL_2 = '#1a2233'     # raised / hover
FIELD = '#0e1420'       # inputs, lists
BORDER = '#263047'
TEXT = '#e6ebf5'
MUTED = '#8b97ad'
ACCENT = '#7c5cff'      # violet
ACCENT_2 = '#9d85ff'
SUCCESS = '#3ddc97'
WARNING = '#ffc857'
DANGER = '#ff6b81'
CANVAS = '#06080d'      # behind previews (checkerboard is drawn on top)

FONT = ('Segoe UI', 10)
FONT_SMALL = ('Segoe UI', 9)
FONT_BOLD = ('Segoe UI Semibold', 10)
FONT_TITLE = ('Segoe UI Semibold', 18)
FONT_MONO = ('Consolas', 9)


def apply(root):
    root.configure(bg=BG)
    style = ttk.Style(root)
    try:
        style.theme_use('clam')
    except tk.TclError:
        pass
    style.configure('.', background=BG, foreground=TEXT, fieldbackground=FIELD,
                    bordercolor=BORDER, lightcolor=PANEL, darkcolor=PANEL,
                    troughcolor=FIELD, font=FONT, focuscolor=ACCENT)
    # frames
    style.configure('TFrame', background=BG)
    style.configure('Panel.TFrame', background=PANEL, relief='flat', borderwidth=0)
    style.configure('Card.TFrame', background=PANEL_2, relief='flat', borderwidth=0)
    style.configure('Header.TFrame', background=PANEL)
    # labels
    style.configure('TLabel', background=BG, foreground=TEXT)
    style.configure('Panel.TLabel', background=PANEL, foreground=TEXT)
    style.configure('Muted.TLabel', background=PANEL, foreground=MUTED, font=FONT_SMALL)
    style.configure('Title.TLabel', background=PANEL, foreground=TEXT, font=FONT_TITLE)
    style.configure('Game.TLabel', background=PANEL, foreground=ACCENT_2, font=('Segoe UI Semibold', 12))
    style.configure('Section.TLabel', background=PANEL, foreground=TEXT, font=FONT_BOLD)
    style.configure('Big.TLabel', background=PANEL, foreground=TEXT, font=('Segoe UI Semibold', 16))
    style.configure('Warn.TLabel', background=PANEL, foreground=WARNING, font=FONT_SMALL)
    style.configure('Help.TLabel', background=PANEL, foreground=ACCENT_2, font=FONT_SMALL)
    style.configure('Tooltip.TLabel', background=PANEL_2, foreground=TEXT, font=FONT_SMALL)
    # buttons
    style.configure('TButton', background=PANEL_2, foreground=TEXT, padding=(12, 7),
                    borderwidth=0, focusthickness=0)
    style.map('TButton', background=[('active', '#252f46'), ('disabled', PANEL)],
              foreground=[('disabled', '#566076')])
    style.configure('Accent.TButton', background=ACCENT, foreground='white', padding=(14, 8),
                    font=FONT_BOLD, borderwidth=0)
    style.map('Accent.TButton', background=[('active', ACCENT_2), ('disabled', '#3a3366')])
    style.configure('Success.TButton', background='#1f7a57', foreground='white', padding=(14, 8),
                    font=FONT_BOLD, borderwidth=0)
    style.map('Success.TButton', background=[('active', '#27996d')])
    style.configure('Tool.TButton', background=PANEL, foreground=TEXT, padding=(8, 4), borderwidth=0)
    style.map('Tool.TButton', background=[('active', PANEL_2)])
    # inputs
    style.configure('TEntry', fieldbackground=FIELD, foreground=TEXT, insertcolor=TEXT, padding=6,
                    bordercolor=BORDER)
    style.configure('TCombobox', fieldbackground=FIELD, foreground=TEXT, padding=5,
                    arrowcolor=MUTED, bordercolor=BORDER)
    style.map('TCombobox', fieldbackground=[('readonly', FIELD)], foreground=[('readonly', TEXT)])
    style.configure('TSpinbox', fieldbackground=FIELD, foreground=TEXT, arrowcolor=MUTED)
    style.configure('TCheckbutton', background=PANEL, foreground=TEXT, indicatorcolor=FIELD)
    style.map('TCheckbutton', indicatorcolor=[('selected', ACCENT)], background=[('active', PANEL)])
    style.configure('TRadiobutton', background=PANEL, foreground=TEXT, indicatorcolor=FIELD)
    style.map('TRadiobutton', indicatorcolor=[('selected', ACCENT)], background=[('active', PANEL)])
    # lists
    style.configure('Treeview', background=FIELD, fieldbackground=FIELD, foreground=TEXT,
                    rowheight=24, borderwidth=0)
    style.map('Treeview', background=[('selected', '#3b2f7a')], foreground=[('selected', 'white')])
    style.configure('Treeview.Heading', background=PANEL_2, foreground=MUTED, font=FONT_BOLD,
                    relief='flat', padding=(6, 5))
    style.map('Treeview.Heading', background=[('active', PANEL_2)])
    # notebook
    style.configure('TNotebook', background=BG, borderwidth=0, tabmargins=(8, 6, 8, 0))
    style.configure('TNotebook.Tab', background=BG, foreground=MUTED, padding=(20, 9), borderwidth=0,
                    font=FONT_BOLD)
    style.map('TNotebook.Tab', background=[('selected', PANEL)], foreground=[('selected', TEXT)])
    style.configure('Sub.TNotebook', background=PANEL)
    style.configure('Sub.TNotebook.Tab', background=PANEL, foreground=MUTED, padding=(14, 6))
    style.map('Sub.TNotebook.Tab', background=[('selected', PANEL_2)], foreground=[('selected', TEXT)])
    # misc
    style.configure('Horizontal.TProgressbar', troughcolor=FIELD, background=ACCENT, bordercolor=FIELD,
                    lightcolor=ACCENT, darkcolor=ACCENT)
    for name, color in (('Good', SUCCESS), ('Mid', WARNING), ('Bad', DANGER)):
        style.configure(f'{name}.Horizontal.TProgressbar', troughcolor=FIELD, background=color,
                        bordercolor=FIELD, lightcolor=color, darkcolor=color)
    style.configure('TScrollbar', background=PANEL_2, troughcolor=FIELD, arrowcolor=MUTED,
                    bordercolor=FIELD)
    style.configure('TPanedwindow', background=BG)
    style.configure('TSeparator', background=BORDER)
    root.option_add('*TCombobox*Listbox.background', PANEL)
    root.option_add('*TCombobox*Listbox.foreground', TEXT)
    root.option_add('*TCombobox*Listbox.selectBackground', ACCENT)
    root.option_add('*TCombobox*Listbox.font', FONT)
