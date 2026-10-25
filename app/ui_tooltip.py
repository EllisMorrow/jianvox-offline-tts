"""Small, transient help bubbles without additional UI dependencies."""
import tkinter as tk


class Tooltip:
    def __init__(self, widget, text, *, delay=500):
        self.widget, self.text, self.delay = widget, text, delay
        self.timer = None
        self.window = None
        widget.bind("<Enter>", self.schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")
        widget.bind("<Destroy>", self.hide, add="+")

    def schedule(self, _event=None):
        self.hide()
        self.timer = self.widget.after(self.delay, self.show)

    def show(self):
        self.timer = None
        if not self.widget.winfo_exists() or not self.widget.winfo_ismapped():
            return
        window = self.window = tk.Toplevel(self.widget)
        window.withdraw()
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        tk.Label(window, text=self.text, justify="left", anchor="w", padx=12, pady=9,
                 background="#202338", foreground="white", relief="solid", borderwidth=1,
                 font=("Microsoft YaHei UI", 10), wraplength=340).pack()
        window.update_idletasks()
        x = min(self.widget.winfo_rootx(), window.winfo_screenwidth() - window.winfo_reqwidth() - 8)
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        if y + window.winfo_reqheight() > window.winfo_screenheight() - 8:
            y = self.widget.winfo_rooty() - window.winfo_reqheight() - 8
        window.geometry(f"+{max(0, x)}+{max(0, y)}")
        window.deiconify()

    def hide(self, _event=None):
        if self.timer is not None:
            self.widget.after_cancel(self.timer)
            self.timer = None
        if self.window is not None:
            self.window.destroy()
            self.window = None
