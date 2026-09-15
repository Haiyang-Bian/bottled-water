"""Small scrollback-friendly selection and paging applications."""

from dataclasses import dataclass

from .terminal_text import safe_text


@dataclass(frozen=True)
class Choice:
    value: str
    label: str
    detail: str = ""


class Selector:
    def __init__(self, choices, title="选择会话", *, color=True):
        from prompt_toolkit.application import Application
        from prompt_toolkit.buffer import Buffer
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import HSplit, Layout, Window
        from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
        from prompt_toolkit.styles import Style

        self.choices, self.title, self.index = list(choices), title, 0
        self.search = Buffer(on_text_changed=lambda _: self.reset())
        keys = KeyBindings()

        @keys.add("up")
        def up(event):
            self.move(-1)

        @keys.add("down")
        def down(event):
            self.move(1)

        @keys.add("pageup")
        def page_up(event):
            self.move(-self.page_size())

        @keys.add("pagedown")
        def page_down(event):
            self.move(self.page_size())

        @keys.add("enter")
        def accept(event):
            matches = self.matches()
            if matches:
                event.app.exit(result=matches[self.index].value)

        @keys.add("escape", eager=True)
        def cancel(event):
            event.app.exit(result=None)

        @keys.add("c-c")
        def interrupt(event):
            event.app.exit(exception=KeyboardInterrupt())

        self.app = Application(
            layout=Layout(HSplit([
                Window(FormattedTextControl(lambda: safe_text(self.title)), height=1),
                Window(BufferControl(buffer=self.search), height=1),
                Window(FormattedTextControl(self.render), dont_extend_height=True),
            ])), key_bindings=keys, full_screen=False, erase_when_done=True,
            style=Style.from_dict({"selected": "ansicyan bold"} if color else {}),
        )

    def reset(self):
        self.index = 0

    def matches(self):
        words = self.search.text.casefold().split()
        return [c for c in self.choices
                if all(w in (c.label + " " + c.detail).casefold() for w in words)]

    def page_size(self):
        return max(1, min(10, self.app.output.get_size().rows - 7))

    def move(self, delta):
        self.index = max(0, min(self.index + delta, len(self.matches()) - 1))

    def render(self):
        matches, size = self.matches(), self.page_size()
        self.index = min(self.index, max(0, len(matches) - 1))
        start = self.index // size * size
        fragments = []
        for i in range(start, min(start + size, len(matches))):
            selected = i == self.index
            fragments.append(("class:selected" if selected else "",
                              ("> " if selected else "  ") + safe_text(matches[i].label) + "\n"))
        detail = matches[self.index].detail if matches else "没有匹配的会话"
        fragments.append(("", safe_text(detail) + "\n"))
        fragments.append(("", f"{self.index + 1 if matches else 0}/{len(matches)} · "
                          "输入过滤 · ↑↓ 选择 · PgUp/PgDn 翻页 · Enter 确认 · Esc 返回"))
        return fragments

    async def run(self):
        return await self.app.run_async()


async def choose_session(catalog, root, current=None, *, color=True):
    choices = [Choice(s.id, ("[当前] " if s.id == current else "") + s.label(), s.preview)
               for s in catalog.list(root)]
    return await Selector(choices, "恢复会话 · 当前目录", color=color).run()


async def pager(text, *, prompt_session=None):
    from prompt_toolkit import PromptSession
    import shutil

    prompt_session = prompt_session or PromptSession()
    # Bound both character count and height; a single very long line is also pageable.
    width = max(20, shutil.get_terminal_size().columns - 2)
    lines = [line[i:i + width] for line in safe_text(text).splitlines()
             for i in range(0, max(1, len(line)), width)]
    height = max(1, shutil.get_terminal_size().lines - 4)
    for offset in range(0, len(lines), height):
        print("\n".join(lines[offset:offset + height]))
        if offset + height < len(lines):
            answer = await prompt_session.prompt_async("Enter 继续，q 返回 > ")
            if answer.strip().lower() == "q":
                return False
    return True
