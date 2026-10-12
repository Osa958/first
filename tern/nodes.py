"""AST node definitions. Every node carries the (line, col) where it starts."""


class Node:
    fields = ()

    def __init__(self, *args, line=0, col=0):
        if len(args) != len(self.fields):
            raise TypeError("%s expects %d fields" % (type(self).__name__, len(self.fields)))
        for name, value in zip(self.fields, args):
            setattr(self, name, value)
        self.line = line
        self.col = col

    def __repr__(self):
        inner = ", ".join("%s=%r" % (f, getattr(self, f)) for f in self.fields)
        return "%s(%s)" % (type(self).__name__, inner)


def _node(name, *fields):
    return type(name, (Node,), {"fields": fields, "__slots__": ()})


# ---- expressions -------------------------------------------------------
Literal = _node("Literal", "value")
Ident = _node("Ident", "name")
ListLit = _node("ListLit", "items")
DictLit = _node("DictLit", "pairs")                    # [(key, value)]
Unary = _node("Unary", "op", "operand")                # op: "-" | "not"
Binary = _node("Binary", "op", "left", "right")
Logical = _node("Logical", "op", "left", "right")      # op: "and" | "or"
Assign = _node("Assign", "target", "op", "value")      # op None or "+", "-", ...
Call = _node("Call", "callee", "args")
Attr = _node("Attr", "obj", "name")
Index = _node("Index", "obj", "index")
FnExpr = _node("FnExpr", "name", "params", "body")     # body: [stmt]
SelfExpr = _node("SelfExpr")
SuperAttr = _node("SuperAttr", "name")

# ---- statements --------------------------------------------------------
ExprStmt = _node("ExprStmt", "expr")
Let = _node("Let", "name", "value")
FnDecl = _node("FnDecl", "name", "params", "body")
ClassDecl = _node("ClassDecl", "name", "superclass", "methods")  # methods: [FnDecl]
If = _node("If", "cond", "then", "else_")              # else_: [stmt] | None
While = _node("While", "cond", "body")
ForIn = _node("ForIn", "var", "iterable", "body")
Return = _node("Return", "value")
Break = _node("Break")
Continue = _node("Continue")
Throw = _node("Throw", "value")
Try = _node("Try", "body", "catch_var", "catch_body", "finally_body")
Import = _node("Import", "path", "alias")
Block = _node("Block", "body")
