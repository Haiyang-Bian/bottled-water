"""Session-local editing with explicit submit and safe bracketed paste."""

from .sessions import SessionCatalogReader


def create_prompt(home, scope=None, *, color=True, cwd=None):
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import Completer, Completion, CompleteEvent, PathCompleter
    from prompt_toolkit.document import Document
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.styles import Style

    commands = ["/resume", "/new", "/history", "/session", "/add-dir", "/cd",
                "/help", "/exit",
                "/tools", "/verbose on", "/verbose off"]

    class CommandCompleter(Completer):
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            prefix = next((p for p in ("/add-dir ", "/cd ") if text.startswith(p)), None)
            if prefix:
                value = text[len(prefix):]
                quoted = value.startswith('"')
                if quoted:
                    value = value[1:]
                for c in PathCompleter(
                    only_directories=True, get_paths=lambda: [str(cwd or ".")]
                ).get_completions(
                    Document(value), complete_event
                ):
                    yield c
            elif text.startswith("/"):
                for command in (["/resume --here"] if text.startswith("/resume ") else commands):
                    if command.startswith(text):
                        yield Completion(command, start_position=-len(text))

    history = InMemoryHistory()
    if scope:
        from agent_adapters.storage.session_queries import decode
        with SessionCatalogReader(home).queries() as queries:
            if queries and queries.session(scope):
                for row in queries.db.execute(
                    "SELECT request FROM runs WHERE scope=? ORDER BY created,id", (scope,)
                ):
                    value = decode(row[0]).get("input")
                    if isinstance(value, str) and value:
                        history.append_string(value)
    keys = KeyBindings()

    @keys.add("tab")
    def complete(event):
        buffer = event.current_buffer
        completions = list(CommandCompleter().get_completions(
            buffer.document, CompleteEvent(completion_requested=True)
        ))
        if len(completions) == 1:
            buffer.apply_completion(completions[0])
        elif completions:
            buffer.start_completion(select_first=True)

    @keys.add("enter")
    def submit(event):
        event.current_buffer.validate_and_handle()

    @keys.add("escape", "enter")
    @keys.add("c-j")
    def newline(event):
        event.current_buffer.insert_text("\n")

    @keys.add(Keys.BracketedPaste)
    def paste(event):
        event.current_buffer.insert_text(event.data.replace("\r\n", "\n").replace("\r", "\n"))

    return PromptSession(
        multiline=True, key_bindings=keys, history=history, completer=CommandCompleter(),
        complete_while_typing=False,
        bottom_toolbar="Enter 发送 · Alt+Enter/Ctrl+J 换行 · Tab 补全 · /resume 恢复",
        style=Style.from_dict({"prompt": "ansicyan bold"} if color else {}),
    )
