"""Hand-written lexer with Go-style automatic semicolon insertion.

A newline ends a statement when the previous token could end one
(identifier, literal, `)`, `]`, `}`, `break`, `continue`, `return`, ...).
Newlines are ignored entirely while inside `(...)` or `[...]`, so long
argument lists and list literals can span lines freely.
"""
from .errors import TernSyntaxError

KEYWORDS = {
    "let", "fn", "return", "if", "else", "while", "for", "in", "break",
    "continue", "class", "self", "super", "true", "false", "null", "and",
    "or", "not", "try", "catch", "finally", "throw", "import", "as",
}

# Token kinds that may end a statement (trigger semicolon insertion).
ENDS_STATEMENT = {
    "IDENT", "INT", "FLOAT", "STRING", "true", "false", "null", "self",
    "super", "break", "continue", "return", ")", "]", "}",
}

# Longest operators first so that greedy matching works.
OPERATORS = [
    "**=", "//=",
    "==", "!=", "<=", ">=", "+=", "-=", "*=", "/=", "%=", "=>", "//", "**",
    "+", "-", "*", "/", "%", "=", "<", ">", "(", ")", "[", "]", "{", "}",
    ",", ".", ":", ";", "!",
]

ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", '"': '"', "'": "'"}


class Token:
    __slots__ = ("kind", "text", "value", "line", "col", "implicit")

    def __init__(self, kind, text, value, line, col, implicit=False):
        self.kind = kind          # "INT", "IDENT", "+", "while", "EOF", ...
        self.text = text          # source text of the token
        self.value = value        # parsed literal value (numbers / strings)
        self.line = line
        self.col = col
        self.implicit = implicit  # True for auto-inserted semicolons

    def describe(self):
        if self.kind == "EOF":
            return "end of input"
        if self.kind == ";" and self.implicit:
            return "newline"
        if self.kind == "STRING":
            return "string %r" % self.value
        return "'%s'" % self.text

    def __repr__(self):
        return "Token(%s, %r, %d:%d)" % (self.kind, self.text, self.line, self.col)


class Lexer:
    def __init__(self, source, filename="<input>"):
        self.src = source
        self.filename = filename
        self.pos = 0
        self.line = 1
        self.col = 1
        self.tokens = []
        self.brackets = []  # stack of open bracket chars

    def error(self, msg, line, col, at_eof=False):
        raise TernSyntaxError(msg, line, col, self.filename, self.src, at_eof)

    def advance(self):
        ch = self.src[self.pos]
        self.pos += 1
        if ch == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    def peek(self, offset=0):
        i = self.pos + offset
        return self.src[i] if i < len(self.src) else ""

    def add(self, kind, text, value, line, col):
        self.tokens.append(Token(kind, text, value, line, col))

    def newline(self, line, col):
        if self.brackets and self.brackets[-1] in "([":
            return
        if self.tokens and self.tokens[-1].kind in ENDS_STATEMENT:
            self.tokens.append(Token(";", "\\n", None, line, col, implicit=True))

    def tokenize(self):
        src = self.src
        while self.pos < len(src):
            ch = src[self.pos]
            line, col = self.line, self.col
            if ch == "\n":
                self.newline(line, col)
                self.advance()
            elif ch in " \t\r":
                self.advance()
            elif ch == "#":
                while self.pos < len(src) and src[self.pos] != "\n":
                    self.advance()
            elif ch.isdigit():
                self.number(line, col)
            elif ch.isalpha() or ch == "_":
                start = self.pos
                while self.pos < len(src) and (src[self.pos].isalnum() or src[self.pos] == "_"):
                    self.advance()
                word = src[start:self.pos]
                self.add(word if word in KEYWORDS else "IDENT", word, word, line, col)
            elif ch in "\"'":
                self.string(line, col)
            else:
                for op in OPERATORS:
                    if src.startswith(op, self.pos):
                        break
                else:
                    self.error("Unexpected character %r" % ch, line, col)
                for _ in op:
                    self.advance()
                if op in "([{":
                    self.brackets.append(op)
                elif op in ")]}":
                    expected = {")": "(", "]": "[", "}": "{"}[op]
                    if not self.brackets or self.brackets[-1] != expected:
                        self.error("Unmatched '%s'" % op, line, col)
                    self.brackets.pop()
                self.add(op, op, None, line, col)
        self.newline(self.line, self.col)
        self.tokens.append(Token("EOF", "", None, self.line, self.col))
        return self.tokens

    def number(self, line, col):
        src = self.src
        start = self.pos
        is_float = False
        while self.peek().isdigit() or self.peek() == "_":
            self.advance()
        if self.peek() == "." and self.peek(1).isdigit():
            is_float = True
            self.advance()
            while self.peek().isdigit() or self.peek() == "_":
                self.advance()
        if self.peek() in ("e", "E") and (self.peek(1).isdigit() or
                                          (self.peek(1) in "+-" and self.peek(2).isdigit())):
            is_float = True
            self.advance()
            if self.peek() in "+-":
                self.advance()
            while self.peek().isdigit():
                self.advance()
        text = src[start:self.pos]
        if self.peek().isalpha() or self.peek() == "_":
            self.error("Invalid number literal '%s%s'" % (text, self.peek()), line, col)
        clean = text.replace("_", "")
        if is_float:
            self.add("FLOAT", text, float(clean), line, col)
        else:
            self.add("INT", text, int(clean), line, col)

    def string(self, line, col):
        start = self.pos
        quote = self.advance()
        chars = []
        while True:
            if self.pos >= len(self.src) or self.peek() == "\n":
                self.error("Unterminated string literal", line, col)
            ch = self.advance()
            if ch == quote:
                break
            if ch == "\\":
                if self.pos >= len(self.src):
                    self.error("Unterminated string literal", line, col)
                esc_line, esc_col = self.line, self.col - 1
                e = self.advance()
                if e in ESCAPES:
                    chars.append(ESCAPES[e])
                elif e == "u":
                    hexdigits = self.src[self.pos:self.pos + 4]
                    if len(hexdigits) != 4 or any(c not in "0123456789abcdefABCDEF" for c in hexdigits):
                        self.error("Invalid \\u escape (expected 4 hex digits)", esc_line, esc_col)
                    for _ in range(4):
                        self.advance()
                    chars.append(chr(int(hexdigits, 16)))
                else:
                    self.error("Unknown escape sequence '\\%s'" % e, esc_line, esc_col)
            else:
                chars.append(ch)
        text = self.src[start:self.pos]
        self.add("STRING", text, "".join(chars), line, col)


def tokenize(source, filename="<input>"):
    return Lexer(source, filename).tokenize()
