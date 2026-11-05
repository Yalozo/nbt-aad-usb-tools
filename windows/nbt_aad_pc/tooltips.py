"""Small keyboard-accessible tooltips for native Tk buttons."""
import tkinter as tk


class Tooltip:
    def __init__(self, widget, text, delay=500):
        self.widget, self.text, self.delay = widget, text, delay
        self.pending = self.window = None
        widget.bind('<Enter>', self.schedule, add='+')
        widget.bind('<FocusIn>', self.schedule, add='+')
        for event in ('<Leave>', '<FocusOut>', '<ButtonPress>', '<Escape>', '<Destroy>'):
            widget.bind(event, self.hide, add='+')

    def schedule(self, event=None):
        self.hide()
        if not self.widget.instate(['disabled']):
            self.pending = self.widget.after(self.delay, self.show)

    def show(self):
        self.pending = None
        if not self.widget.winfo_exists() or self.widget.instate(['disabled']):
            return
        self.window = tk.Toplevel(self.widget)
        self.window.withdraw()
        self.window.overrideredirect(True)
        label = tk.Label(self.window, text=self.text, justify='left', wraplength=410,
                         bg='#fff9dc', fg='#142d34', relief='solid', borderwidth=1,
                         padx=11, pady=8, font='TkDefaultFont')
        label.pack()
        self.window.update_idletasks()
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        x = max(0, min(self.widget.winfo_rootx() + 8, self.widget.winfo_screenwidth() - width - 8))
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        if y + height > self.widget.winfo_screenheight() - 8:
            y = max(0, self.widget.winfo_rooty() - height - 6)
        self.window.geometry('+' + str(x) + '+' + str(y))
        self.window.deiconify()

    def hide(self, event=None):
        if self.pending is not None:
            self.widget.after_cancel(self.pending)
            self.pending = None
        if self.window is not None:
            self.window.destroy()
            self.window = None
