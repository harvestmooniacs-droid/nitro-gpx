"""Small shared widgets: hover tooltips, (?) help icons, cards, metadata text."""
import tkinter as tk
from tkinter import ttk

from . import theme


class Tooltip:
    """Hover popup for a single widget - shown on <Enter>, hidden on
    <Leave> or click, with a short delay so it doesn't flash while the
    mouse just passes over the label on its way somewhere else."""
    DELAY_MS = 400

    def __init__(self, widget, text):
        self.widget, self.text = widget, text
        self._after_id = self._win = None
        widget.bind('<Enter>', self._schedule)
        widget.bind('<Leave>', self._hide)
        widget.bind('<ButtonPress>', self._hide)

    def _schedule(self, _event=None):
        self._cancel()
        self._after_id = self.widget.after(self.DELAY_MS, self._show)

    def _cancel(self):
        if self._after_id:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _show(self):
        if self._win or not self.widget.winfo_exists():
            return
        x = self.widget.winfo_rootx() + 14
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self._win = win = tk.Toplevel(self.widget)
        win.wm_overrideredirect(True)
        win.wm_geometry(f'+{x}+{y}')
        try:
            win.wm_attributes('-topmost', True)
        except Exception:
            pass
        frame = tk.Frame(win, bg=theme.ACCENT, padx=1, pady=1)
        frame.pack()
        ttk.Label(frame, text=self.text, style='Tooltip.TLabel', wraplength=320,
                  justify='left', padding=(8, 6)).pack()

    def _hide(self, _event=None):
        self._cancel()
        if self._win:
            self._win.destroy()
            self._win = None


def help_icon(parent, text):
    """A small '(?)' label that shows `text` in a Tooltip on hover - drop
    next to any field label to document what that control does/is for."""
    lbl = ttk.Label(parent, text=' (?)', style='Help.TLabel', cursor='question_arrow')
    Tooltip(lbl, text)
    return lbl


class Card(ttk.Frame):
    def __init__(self, parent, title, **kwargs):
        super().__init__(parent, style='Panel.TFrame', padding=10, **kwargs)
        ttk.Label(self, text=title, style='Section.TLabel').pack(fill='x', pady=(0, 8))


class MetadataView(Card):
    def __init__(self, parent):
        super().__init__(parent, 'Informações do recurso')
        self.text = tk.Text(self, height=12, bg=theme.PANEL, fg=theme.TEXT, relief='flat',
                            font=('Consolas', 9), wrap='word', padx=2, pady=2)
        self.text.pack(fill='both', expand=True)
        self.text.configure(state='disabled')

    def set_values(self, pairs):
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        for key, value in pairs:
            self.text.insert('end', f'{key:<16} {value}\n')
        self.text.configure(state='disabled')
