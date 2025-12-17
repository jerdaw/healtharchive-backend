from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Iterable, Iterator, Literal, Sequence


FieldName = Literal["title", "snippet", "url"]


class QueryParseError(ValueError):
    pass


class TokenType(str, Enum):
    word = "word"
    phrase = "phrase"
    lparen = "lparen"
    rparen = "rparen"
    colon = "colon"
    op_and = "and"
    op_or = "or"
    op_not = "not"


@dataclass(frozen=True)
class Token:
    type: TokenType
    value: str | None = None


@dataclass(frozen=True)
class Term:
    text: str
    field: FieldName | None = None
    is_phrase: bool = False


@dataclass(frozen=True)
class Not:
    child: "QueryNode"


@dataclass(frozen=True)
class And:
    children: tuple["QueryNode", ...]


@dataclass(frozen=True)
class Or:
    children: tuple["QueryNode", ...]


QueryNode = Term | Not | And | Or

_FIELD_NAMES: tuple[FieldName, ...] = ("title", "snippet", "url")
_FIELD_SET = set(_FIELD_NAMES)

_ADVANCED_HINT_RE = re.compile(
    r"(\(|\)|\bAND\b|\bOR\b|\bNOT\b|(?:^|\s)-[A-Za-z0-9]|\b(?:title|snippet|url):)",
    re.IGNORECASE,
)


def looks_like_advanced_query(value: str) -> bool:
    """
    Return True when a query likely contains boolean/field syntax.

    We intentionally do NOT treat quotes alone as "advanced" so that simple
    phrase queries can keep using Postgres websearch_to_tsquery ranking.
    """
    raw = value.strip()
    if not raw:
        return False
    return _ADVANCED_HINT_RE.search(raw) is not None


def tokenize(value: str, *, max_tokens: int = 128) -> list[Token]:
    raw = value.strip()
    if not raw:
        return []

    tokens: list[Token] = []
    idx = 0

    def push(tok: Token) -> None:
        tokens.append(tok)
        if len(tokens) > max_tokens:
            raise QueryParseError("Query is too complex.")

    while idx < len(raw):
        ch = raw[idx]
        if ch.isspace():
            idx += 1
            continue

        if ch == "(":
            push(Token(TokenType.lparen))
            idx += 1
            continue
        if ch == ")":
            push(Token(TokenType.rparen))
            idx += 1
            continue

        if ch == '"':
            idx += 1
            buf: list[str] = []
            while idx < len(raw):
                cur = raw[idx]
                if cur == "\\" and idx + 1 < len(raw):
                    buf.append(raw[idx + 1])
                    idx += 2
                    continue
                if cur == '"':
                    idx += 1
                    break
                buf.append(cur)
                idx += 1
            push(Token(TokenType.phrase, "".join(buf).strip()))
            continue

        # Consume a word-ish token, treating "field:" prefixes specially while
        # leaving URL schemes ("https://") intact.
        buf: list[str] = []
        while idx < len(raw):
            cur = raw[idx]
            if cur.isspace() or cur in ("(", ")"):
                break

            if cur == ":":
                candidate = "".join(buf).lower()
                if candidate in _FIELD_SET:
                    if candidate:
                        push(Token(TokenType.word, candidate))
                    push(Token(TokenType.colon))
                    buf.clear()
                    idx += 1
                    break

            buf.append(cur)
            idx += 1

        text = "".join(buf).strip()
        if not text:
            continue

        if text.startswith("-") and len(text) > 1 and text[1].isalnum():
            push(Token(TokenType.op_not))
            push(Token(TokenType.word, text[1:]))
            continue

        lowered = text.lower()
        if lowered == "and":
            push(Token(TokenType.op_and))
        elif lowered == "or":
            push(Token(TokenType.op_or))
        elif lowered == "not":
            push(Token(TokenType.op_not))
        else:
            push(Token(TokenType.word, text))

    return tokens


class _Parser:
    def __init__(self, tokens: Sequence[Token]):
        self._tokens = tokens
        self._idx = 0

    def _peek(self) -> Token | None:
        if self._idx >= len(self._tokens):
            return None
        return self._tokens[self._idx]

    def _advance(self) -> Token:
        tok = self._peek()
        if tok is None:
            raise QueryParseError("Unexpected end of query.")
        self._idx += 1
        return tok

    def _match(self, token_type: TokenType) -> bool:
        tok = self._peek()
        if tok is None or tok.type != token_type:
            return False
        self._idx += 1
        return True

    def _expect(self, token_type: TokenType) -> Token:
        tok = self._advance()
        if tok.type != token_type:
            raise QueryParseError(f"Expected {token_type.value}.")
        return tok

    def _starts_primary(self, tok: Token | None) -> bool:
        if tok is None:
            return False
        return tok.type in {TokenType.word, TokenType.phrase, TokenType.lparen, TokenType.op_not}

    def parse(self) -> QueryNode:
        node = self._parse_or()
        if self._peek() is not None:
            raise QueryParseError("Unexpected trailing input.")
        return node

    def _parse_or(self) -> QueryNode:
        nodes = [self._parse_and()]
        while self._match(TokenType.op_or):
            nodes.append(self._parse_and())
        return _or(nodes)

    def _parse_and(self) -> QueryNode:
        nodes = [self._parse_not()]
        while True:
            if self._match(TokenType.op_and):
                nodes.append(self._parse_not())
                continue
            tok = self._peek()
            if tok is not None and tok.type not in {TokenType.op_or, TokenType.rparen} and self._starts_primary(tok):
                nodes.append(self._parse_not())
                continue
            break
        return _and(nodes)

    def _parse_not(self) -> QueryNode:
        if self._match(TokenType.op_not):
            return Not(self._parse_not())
        return self._parse_primary()

    def _parse_primary(self) -> QueryNode:
        if self._match(TokenType.lparen):
            node = self._parse_or()
            self._expect(TokenType.rparen)
            return node

        tok = self._advance()
        if tok.type == TokenType.phrase:
            return Term(text=tok.value or "", is_phrase=True)
        if tok.type != TokenType.word:
            raise QueryParseError("Expected a term.")

        if self._match(TokenType.colon):
            field = (tok.value or "").lower()
            if field not in _FIELD_SET:
                raise QueryParseError(f"Unknown field: {field}")

            rhs = self._advance()
            if rhs.type == TokenType.phrase:
                return Term(text=rhs.value or "", field=field, is_phrase=True)
            if rhs.type == TokenType.word:
                return Term(text=rhs.value or "", field=field, is_phrase=False)
            raise QueryParseError("Expected a term after field prefix.")

        return Term(text=tok.value or "", is_phrase=False)


def parse_query(value: str, *, max_tokens: int = 128) -> QueryNode:
    tokens = tokenize(value, max_tokens=max_tokens)
    if not tokens:
        raise QueryParseError("Query is empty.")
    return _Parser(tokens).parse()


def iter_terms(node: QueryNode) -> Iterator[Term]:
    if isinstance(node, Term):
        yield node
        return
    if isinstance(node, Not):
        yield from iter_terms(node.child)
        return
    if isinstance(node, And) or isinstance(node, Or):
        for child in node.children:
            yield from iter_terms(child)
        return


def iter_positive_terms(node: QueryNode) -> Iterator[Term]:
    if isinstance(node, Term):
        yield node
        return
    if isinstance(node, Not):
        return
    if isinstance(node, And) or isinstance(node, Or):
        for child in node.children:
            yield from iter_positive_terms(child)
        return


def summarize_positive_text(node: QueryNode, *, max_terms: int = 12) -> str:
    """
    Build a compact text representation of the positive portion of a boolean query.
    """
    terms = []
    for term in iter_positive_terms(node):
        if term.field:
            terms.append(f"{term.field}:{term.text}")
        else:
            terms.append(term.text)
        if len(terms) >= max_terms:
            break
    return " ".join(t for t in terms if t).strip()


def _and(nodes: Iterable[QueryNode]) -> QueryNode:
    flattened: list[QueryNode] = []
    for node in nodes:
        if isinstance(node, And):
            flattened.extend(node.children)
        else:
            flattened.append(node)
    if len(flattened) == 1:
        return flattened[0]
    return And(tuple(flattened))


def _or(nodes: Iterable[QueryNode]) -> QueryNode:
    flattened: list[QueryNode] = []
    for node in nodes:
        if isinstance(node, Or):
            flattened.extend(node.children)
        else:
            flattened.append(node)
    if len(flattened) == 1:
        return flattened[0]
    return Or(tuple(flattened))


__all__ = [
    "FieldName",
    "QueryParseError",
    "QueryNode",
    "Token",
    "TokenType",
    "Term",
    "Not",
    "And",
    "Or",
    "looks_like_advanced_query",
    "tokenize",
    "parse_query",
    "iter_terms",
    "iter_positive_terms",
    "summarize_positive_text",
]
