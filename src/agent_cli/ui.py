"""Static transcript helpers, used only while the input application is inactive."""

import os
import sys

from .terminal_text import safe_text


class UserInterface:
    def __init__(self, args):
        self.rich = sys.stdout.isatty() and not args.plain and not args.json
        self.color = not (args.plain or args.no_color or os.environ.get("NO_COLOR"))
        self.console = None
        if self.rich:
            from rich.console import Console
            self.console = Console(no_color=not self.color)

    def note(self, text, style=""):
        text = safe_text(text)
        if self.console:
            from rich.text import Text
            self.console.print(Text(text, style=style))
        else:
            print(text)

    def markdown(self, text):
        text = safe_text(text)
        if self.console:
            from rich.markdown import Markdown
            self.console.print(Markdown(text))
        else:
            print(text)
