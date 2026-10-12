"""Error types and source-snippet formatting shared by every stage."""


def caret_snippet(source, line, col):
    """Return the offending source line followed by a caret under `col`."""
    if not source or line < 1:
        return ""
    lines = source.split("\n")
    if line > len(lines):
        return ""
    text = lines[line - 1].replace("\t", " ")
    col = max(1, col)
    return "    %s\n    %s^" % (text, " " * (col - 1))


class TernError(Exception):
    """Base class for every error Tern reports to the user."""

    def format(self):
        return str(self)


class TernSyntaxError(TernError):
    """A lexing, parsing or compile-time error with a source position."""

    kind = "SyntaxError"

    def __init__(self, message, line, col, filename="<input>", source="", at_eof=False):
        super().__init__(message)
        self.message = message
        self.line = line
        self.col = col
        self.filename = filename
        self.source = source
        self.at_eof = at_eof

    def format(self):
        head = "%s:%d:%d: %s: %s" % (self.filename, self.line, self.col, self.kind, self.message)
        snippet = caret_snippet(self.source, self.line, self.col)
        return head + ("\n" + snippet if snippet else "")

    def __str__(self):
        return self.format()


class TernCompileError(TernSyntaxError):
    kind = "CompileError"


class RuntimeFault(Exception):
    """Raised inside the VM / natives; converted into a Tern Error instance.

    `kind` names a built-in Tern error class (TypeError, IndexError, ...).
    """

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind
        self.message = message


class TernThrow(Exception):
    """Carries a thrown Tern value out of a (nested) VM run loop."""

    def __init__(self, value, trace):
        super().__init__("uncaught Tern exception")
        self.value = value
        self.trace = trace


class TernUncaught(TernError):
    """An exception that propagated out of the program; formatted traceback."""

    def __init__(self, value, trace, text):
        super().__init__(text)
        self.value = value
        self.trace = trace
        self.text = text

    def format(self):
        return self.text

    def __str__(self):
        return self.text
