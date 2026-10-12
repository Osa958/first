"""Recursive-descent parser: tokens -> AST (see README for the grammar)."""
from . import nodes as N
from .errors import TernSyntaxError
from .lexer import tokenize

ASSIGN_OPS = {"=": None, "+=": "+", "-=": "-", "*=": "*", "/=": "/",
              "%=": "%", "//=": "//", "**=": "**"}


class Parser:
    def __init__(self, tokens, filename="<input>", source=""):
        self.tokens = tokens
        self.pos = 0
        self.filename = filename
        self.source = source

    # ---- token helpers -------------------------------------------------
    def peek(self, offset=0):
        i = min(self.pos + offset, len(self.tokens) - 1)
        return self.tokens[i]

    def check(self, *kinds):
        return self.tokens[self.pos].kind in kinds

    def advance(self):
        tok = self.tokens[self.pos]
        if tok.kind != "EOF":
            self.pos += 1
        return tok

    def match(self, *kinds):
        if self.tokens[self.pos].kind in kinds:
            return self.advance()
        return None

    def error(self, msg, tok=None):
        tok = tok or self.peek()
        raise TernSyntaxError(msg, tok.line, tok.col, self.filename, self.source,
                              at_eof=tok.kind == "EOF")

    def expect(self, kind, what):
        if self.check(kind):
            return self.advance()
        self.error("Expected %s but found %s" % (what, self.peek().describe()))

    def skip_newlines(self):
        while self.check(";") and self.peek().implicit:
            self.advance()

    def skip_semis_before(self, kind):
        """Allow `}` NEWLINE `else` (and catch/finally) by skipping separators."""
        i = self.pos
        while self.tokens[i].kind == ";":
            i += 1
        if self.tokens[i].kind == kind:
            self.pos = i
            return True
        return False

    def end_statement(self):
        if self.match(";"):
            return
        if self.check("}", "EOF"):
            return
        self.error("Expected newline or ';' after statement but found %s" % self.peek().describe())

    # ---- program / statements -----------------------------------------
    def parse_program(self):
        stmts = []
        while not self.check("EOF"):
            if self.match(";"):
                continue
            stmts.append(self.statement())
        return stmts

    def block(self):
        self.expect("{", "'{'")
        stmts = []
        while not self.check("}"):
            if self.check("EOF"):
                self.error("Expected '}' to close block but found end of input")
            if self.match(";"):
                continue
            stmts.append(self.statement())
        self.advance()
        return stmts

    def statement(self):
        tok = self.peek()
        k = tok.kind
        if k == "let":
            return self.let_stmt()
        if k == "fn" and self.peek(1).kind == "IDENT":
            return self.fn_decl()
        if k == "class":
            return self.class_decl()
        if k == "if":
            return self.if_stmt()
        if k == "while":
            self.advance()
            cond = self.expression()
            body = self.block()
            return N.While(cond, body, line=tok.line, col=tok.col)
        if k == "for":
            return self.for_stmt()
        if k == "return":
            self.advance()
            value = None
            if not self.check(";", "}", "EOF"):
                value = self.expression()
            self.end_statement()
            return N.Return(value, line=tok.line, col=tok.col)
        if k == "break":
            self.advance()
            self.end_statement()
            return N.Break(line=tok.line, col=tok.col)
        if k == "continue":
            self.advance()
            self.end_statement()
            return N.Continue(line=tok.line, col=tok.col)
        if k == "throw":
            self.advance()
            value = self.expression()
            self.end_statement()
            return N.Throw(value, line=tok.line, col=tok.col)
        if k == "try":
            return self.try_stmt()
        if k == "import":
            return self.import_stmt()
        if k == "{":
            return N.Block(self.block(), line=tok.line, col=tok.col)
        expr = self.expression()
        self.end_statement()
        return N.ExprStmt(expr, line=tok.line, col=tok.col)

    def let_stmt(self):
        tok = self.advance()
        name = self.expect("IDENT", "variable name after 'let'")
        value = None
        if self.match("="):
            value = self.expression()
        self.end_statement()
        return N.Let(name.text, value, line=name.line, col=name.col)

    def params(self):
        self.expect("(", "'(' before parameters")
        names = []
        if not self.check(")"):
            while True:
                p = self.expect("IDENT", "parameter name")
                if p.text in names:
                    self.error("Duplicate parameter '%s'" % p.text, p)
                names.append(p.text)
                if not self.match(","):
                    break
                if self.check(")"):
                    break
        self.expect(")", "')' after parameters")
        return names

    def fn_decl(self):
        tok = self.advance()
        name = self.expect("IDENT", "function name")
        params = self.params()
        body = self.block()
        return N.FnDecl(name.text, params, body, line=tok.line, col=tok.col)

    def class_decl(self):
        tok = self.advance()
        name = self.expect("IDENT", "class name")
        superclass = None
        if self.match("<"):
            s = self.expect("IDENT", "superclass name after '<'")
            superclass = N.Ident(s.text, line=s.line, col=s.col)
            while self.match("."):           # e.g. class Square < geometry.Rect
                attr = self.expect("IDENT", "attribute name after '.'")
                superclass = N.Attr(superclass, attr.text, line=attr.line, col=attr.col)
        self.expect("{", "'{' before class body")
        methods = []
        while not self.check("}"):
            if self.match(";"):
                continue
            if self.check("EOF"):
                self.error("Expected '}' to close class body but found end of input")
            if not self.check("fn"):
                self.error("Expected method declaration ('fn name(...) {...}') in class body "
                           "but found %s" % self.peek().describe())
            methods.append(self.fn_decl())
        self.advance()
        return N.ClassDecl(name.text, superclass, methods, line=tok.line, col=tok.col)

    def if_stmt(self):
        tok = self.advance()
        cond = self.expression()
        then = self.block()
        else_ = None
        if self.skip_semis_before("else"):
            self.advance()
            if self.check("if"):
                else_ = [self.if_stmt()]
            else:
                else_ = self.block()
        return N.If(cond, then, else_, line=tok.line, col=tok.col)

    def for_stmt(self):
        tok = self.advance()
        paren = self.match("(")
        var = self.expect("IDENT", "loop variable name after 'for'")
        self.expect("in", "'in' after loop variable")
        iterable = self.expression()
        if paren:
            self.expect(")", "')' after for-in header")
        body = self.block()
        return N.ForIn(var.text, iterable, body, line=tok.line, col=tok.col)

    def try_stmt(self):
        tok = self.advance()
        body = self.block()
        catch_var = catch_body = finally_body = None
        if self.skip_semis_before("catch"):
            self.advance()
            if self.match("("):
                catch_var = self.expect("IDENT", "exception variable name").text
                self.expect(")", "')' after exception variable")
            elif self.check("IDENT"):
                catch_var = self.advance().text
            catch_body = self.block()
        if self.skip_semis_before("finally"):
            self.advance()
            finally_body = self.block()
        if catch_body is None and finally_body is None:
            self.error("Expected 'catch' or 'finally' after try block")
        return N.Try(body, catch_var, catch_body, finally_body, line=tok.line, col=tok.col)

    def import_stmt(self):
        tok = self.advance()
        if self.check("STRING"):
            path = self.advance().value
        else:
            parts = [self.expect("IDENT", "module name or path string after 'import'").text]
            while self.match("."):
                parts.append(self.expect("IDENT", "module name after '.'").text)
            path = "/".join(parts)
        alias = None
        if self.match("as"):
            alias = self.expect("IDENT", "alias name after 'as'").text
        self.end_statement()
        return N.Import(path, alias, line=tok.line, col=tok.col)

    # ---- expressions ---------------------------------------------------
    def expression(self):
        return self.assignment()

    def assignment(self):
        expr = self.or_expr()
        tok = self.peek()
        if tok.kind in ASSIGN_OPS:
            self.advance()
            value = self.assignment()
            if not isinstance(expr, (N.Ident, N.Attr, N.Index)):
                self.error("Invalid assignment target", tok)
            return N.Assign(expr, ASSIGN_OPS[tok.kind], value, line=tok.line, col=tok.col)
        return expr

    def or_expr(self):
        expr = self.and_expr()
        while self.check("or"):
            tok = self.advance()
            expr = N.Logical("or", expr, self.and_expr(), line=tok.line, col=tok.col)
        return expr

    def and_expr(self):
        expr = self.not_expr()
        while self.check("and"):
            tok = self.advance()
            expr = N.Logical("and", expr, self.not_expr(), line=tok.line, col=tok.col)
        return expr

    def not_expr(self):
        if self.check("not"):
            tok = self.advance()
            return N.Unary("not", self.not_expr(), line=tok.line, col=tok.col)
        return self.equality()

    def equality(self):
        expr = self.comparison()
        while self.check("==", "!="):
            tok = self.advance()
            expr = N.Binary(tok.kind, expr, self.comparison(), line=tok.line, col=tok.col)
        return expr

    def comparison(self):
        expr = self.additive()
        while True:
            if self.check("<", "<=", ">", ">=", "in"):
                tok = self.advance()
                expr = N.Binary(tok.kind, expr, self.additive(), line=tok.line, col=tok.col)
            elif self.check("not") and self.peek(1).kind == "in":
                tok = self.advance()
                self.advance()
                inner = N.Binary("in", expr, self.additive(), line=tok.line, col=tok.col)
                expr = N.Unary("not", inner, line=tok.line, col=tok.col)
            else:
                return expr

    def additive(self):
        expr = self.multiplicative()
        while self.check("+", "-"):
            tok = self.advance()
            expr = N.Binary(tok.kind, expr, self.multiplicative(), line=tok.line, col=tok.col)
        return expr

    def multiplicative(self):
        expr = self.unary()
        while self.check("*", "/", "//", "%"):
            tok = self.advance()
            expr = N.Binary(tok.kind, expr, self.unary(), line=tok.line, col=tok.col)
        return expr

    def unary(self):
        if self.check("-", "!"):
            tok = self.advance()
            op = "-" if tok.kind == "-" else "not"
            return N.Unary(op, self.unary(), line=tok.line, col=tok.col)
        return self.power()

    def power(self):
        expr = self.postfix()
        if self.check("**"):
            tok = self.advance()
            # right-associative; binds tighter than unary minus on its left
            return N.Binary("**", expr, self.unary(), line=tok.line, col=tok.col)
        return expr

    def postfix(self):
        expr = self.primary()
        while True:
            if self.check("("):
                tok = self.advance()
                args = []
                if not self.check(")"):
                    while True:
                        args.append(self.expression())
                        if not self.match(","):
                            break
                        if self.check(")"):
                            break
                self.expect(")", "')' after arguments")
                expr = N.Call(expr, args, line=tok.line, col=tok.col)
            elif self.check("["):
                tok = self.advance()
                index = self.expression()
                self.expect("]", "']' after index")
                expr = N.Index(expr, index, line=tok.line, col=tok.col)
            elif self.check("."):
                tok = self.advance()
                name = self.expect("IDENT", "attribute name after '.'")
                expr = N.Attr(expr, name.text, line=name.line, col=name.col)
            else:
                return expr

    def primary(self):
        tok = self.peek()
        k = tok.kind
        if k in ("INT", "FLOAT", "STRING"):
            self.advance()
            return N.Literal(tok.value, line=tok.line, col=tok.col)
        if k == "true" or k == "false":
            self.advance()
            return N.Literal(k == "true", line=tok.line, col=tok.col)
        if k == "null":
            self.advance()
            return N.Literal(None, line=tok.line, col=tok.col)
        if k == "IDENT":
            self.advance()
            return N.Ident(tok.text, line=tok.line, col=tok.col)
        if k == "self":
            self.advance()
            return N.SelfExpr(line=tok.line, col=tok.col)
        if k == "super":
            self.advance()
            self.expect(".", "'.' after 'super'")
            name = self.expect("IDENT", "superclass method name")
            return N.SuperAttr(name.text, line=tok.line, col=tok.col)
        if k == "(":
            self.advance()
            expr = self.expression()
            self.expect(")", "')' after expression")
            return expr
        if k == "[":
            self.advance()
            items = []
            while not self.check("]"):
                items.append(self.expression())
                if not self.match(","):
                    break
            self.expect("]", "']' after list items")
            return N.ListLit(items, line=tok.line, col=tok.col)
        if k == "{":
            return self.dict_literal()
        if k == "fn":
            return self.fn_expr()
        if k == "EOF":
            self.error("Unexpected end of input, expected an expression")
        self.error("Expected an expression but found %s" % tok.describe())

    def dict_literal(self):
        tok = self.advance()
        pairs = []
        self.skip_newlines()
        while not self.check("}"):
            if self.check("IDENT") and self.peek(1).kind == ":":
                # bare identifier keys are sugar for string keys: {name: 1}
                key_tok = self.advance()
                key = N.Literal(key_tok.text, line=key_tok.line, col=key_tok.col)
            else:
                key = self.expression()
            self.expect(":", "':' after dictionary key")
            self.skip_newlines()
            value = self.expression()
            pairs.append((key, value))
            self.skip_newlines()
            if not self.match(","):
                break
            self.skip_newlines()
        self.skip_newlines()
        self.expect("}", "'}' after dictionary entries")
        return N.DictLit(pairs, line=tok.line, col=tok.col)

    def fn_expr(self):
        tok = self.advance()
        name = None
        if self.check("IDENT"):
            name = self.advance().text
        params = self.params()
        if self.check("=>"):
            arrow = self.advance()
            expr = self.expression()
            body = [N.Return(expr, line=arrow.line, col=arrow.col)]
        else:
            body = self.block()
        return N.FnExpr(name, params, body, line=tok.line, col=tok.col)


def parse(source, filename="<input>"):
    tokens = tokenize(source, filename)
    return Parser(tokens, filename, source).parse_program()
