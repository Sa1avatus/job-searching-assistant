"""Learn a search recipe from the HTML of a results page a human already produced.

The person searches on the site themselves; the page they end on is analysed offline here (stdlib
``html.parser``, no page JavaScript). The idea: the vacancy links of a results page share one URL
"shape" (``/jobs/<something>``), and each link sits inside a repeated card. Anchoring on the links
avoids depending on any class name the site happens to use, then a plain CSS selector for the card
is derived from what the cards have in common.
"""

from __future__ import annotations

import os.path
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from html.parser import HTMLParser
from urllib.parse import quote, quote_plus, urlsplit

from app.domain.search_recipe import (
    LOCATION_PLACEHOLDER,
    LOCATION_SLUG_PLACEHOLDER,
    QUERY_PLACEHOLDER,
    QUERY_SLUG_PLACEHOLDER,
    resolve_hit_url,
    slugify,
)

_VOID_TAGS = frozenset(
    {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }
)  # fmt: skip
_SKIPPED_TEXT_TAGS = frozenset({"script", "style", "noscript", "template"})
_CHROME_TAGS = frozenset({"nav", "header", "footer", "aside"})
_CONTAINER_TAGS = frozenset({"html", "body", "ul", "ol", "table", "tbody", "main", "section"})
_HEADING_TAGS = ("h1", "h2", "h3", "h4")
_STABLE_ATTRIBUTES = ("data-testid", "data-qa", "data-test", "data-automation-id")
_COMPANY_HINT = re.compile(r"company|employer|organi[sz]ation|\borg\b|firm|client", re.IGNORECASE)
_UNSTABLE_CLASS = re.compile(
    r"^(css|sc|jsx|emotion|styled)-|__[A-Za-z0-9]{4,}$|\d{3,}|^(active|selected|hover|open|"
    r"hidden|visible|is-|has-|js-)",
    re.IGNORECASE,
)
_STABLE_CLASS = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,40}$")
_MIN_CARDS = 3
_MAX_CLASSES = 3


class RecipeLearningError(ValueError):
    """The page could not be turned into a reliable recipe; the message explains why."""


@dataclass(eq=False)
class Node:
    tag: str
    attrs: dict[str, str]
    parent: Node | None = None
    children: list[Node] = field(default_factory=list)
    content: list[str | Node] = field(default_factory=list)  # text and children, in order

    @property
    def classes(self) -> tuple[str, ...]:
        return tuple((self.attrs.get("class") or "").split())

    def text(self) -> str:
        parts = [item if isinstance(item, str) else item.text() for item in self.content]
        return re.sub(r"\s+", " ", " ".join(parts)).strip()

    def walk(self) -> list[Node]:
        nodes = [self]
        for child in self.children:
            nodes.extend(child.walk())
        return nodes

    def has_ancestor(self, tags: frozenset[str]) -> bool:
        node = self.parent
        while node is not None:
            if node.tag in tags:
                return True
            node = node.parent
        return False


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document", {})
        self._current = self.root
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth:
            if tag in _SKIPPED_TEXT_TAGS:
                self._skip_depth += 1
            return
        if tag in _SKIPPED_TEXT_TAGS:
            self._skip_depth = 1
            return
        node = Node(tag, {name: value or "" for name, value in attrs}, parent=self._current)
        self._current.children.append(node)
        self._current.content.append(node)
        if tag not in _VOID_TAGS:
            self._current = node

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            if tag in _SKIPPED_TEXT_TAGS:
                self._skip_depth -= 1
            return
        node: Node | None = self._current
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self._current = node.parent

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and data.strip():
            self._current.content.append(data)


def parse_html(html: str) -> Node:
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


# --- selectors we generate (a deliberately tiny subset of CSS) -------------------------------


@dataclass(frozen=True, slots=True)
class Sel:
    tag: str
    classes: tuple[str, ...] = ()
    attribute: tuple[str, str] | None = None
    href_contains: str | None = None
    # :has(a[href*="..."]) - a card can be structurally identical to another list item that
    # never got real content (e.g. an empty carousel slot sharing the same bare <li> markup),
    # distinguishable only by actually containing the vacancy link.
    has_link_href_contains: str | None = None

    def css(self) -> str:
        text = self.tag + "".join(f".{name}" for name in self.classes)
        if self.attribute is not None:
            text += f'[{self.attribute[0]}="{self.attribute[1]}"]'
        if self.href_contains is not None:
            text += f'[href*="{self.href_contains}"]'
        if self.has_link_href_contains is not None:
            text += f':has(a[href*="{self.has_link_href_contains}"])'
        return text

    def matches(self, node: Node) -> bool:
        if node.tag != self.tag:
            return False
        if not set(self.classes) <= set(node.classes):
            return False
        if self.attribute is not None and node.attrs.get(self.attribute[0]) != self.attribute[1]:
            return False
        if self.href_contains is not None and self.href_contains not in node.attrs.get("href", ""):
            return False
        return self.has_link_href_contains is None or any(
            descendant.tag == "a"
            and self.has_link_href_contains in descendant.attrs.get("href", "")
            for descendant in node.walk()[1:]
        )


def _stable_classes(node: Node) -> tuple[str, ...]:
    stable = [
        name
        for name in node.classes
        if _STABLE_CLASS.fullmatch(name) and not _UNSTABLE_CLASS.search(name)
    ]
    return tuple(stable[:_MAX_CLASSES])


def _selector_for(node: Node) -> Sel:
    classes = _stable_classes(node)
    if classes:
        return Sel(node.tag, classes)
    for name in _STABLE_ATTRIBUTES:
        value = node.attrs.get(name, "")
        if value and len(value) <= 60 and not re.search(r"\d{3,}", value) and '"' not in value:
            return Sel(node.tag, attribute=(name, value))
    return Sel(node.tag)


# --- URL template ---------------------------------------------------------------------------


def infer_url_template(results_url: str, *, query: str, location: str = "") -> str | None:
    """Replace what the person searched for in the results URL with placeholders."""
    parts = urlsplit(results_url)
    base = f"{parts.scheme}://{parts.netloc}"
    rest = results_url[len(base) :]

    def substitute(value: str, text: str, placeholder: str, slug_placeholder: str) -> str | None:
        for variant in dict.fromkeys((quote_plus(text), quote(text, safe=""), text)):
            if variant and re.search(re.escape(variant), value, re.IGNORECASE):
                return re.sub(re.escape(variant), placeholder, value, flags=re.IGNORECASE)
        # Some sites put the search text into the URL as an SEO slug instead of standard
        # encoding (e.g. ".../ML-Engineer-k-en.html" for the query "ML Engineer").
        slug = slugify(text)
        if slug and slug != text and re.search(re.escape(slug), value, re.IGNORECASE):
            return re.sub(re.escape(slug), slug_placeholder, value, flags=re.IGNORECASE)
        return None

    query = query.strip()
    if not query:
        return None
    replaced = substitute(rest, query, QUERY_PLACEHOLDER, QUERY_SLUG_PLACEHOLDER)
    if replaced is None:
        return None
    if location.strip():
        with_location = substitute(
            replaced, location.strip(), LOCATION_PLACEHOLDER, LOCATION_SLUG_PLACEHOLDER
        )
        replaced = with_location or replaced
    return base + replaced


# --- card learning --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LearnedSelectors:
    card_selector: str
    link_selector: str
    title_selector: str
    company_selector: str
    card_count: int


def _shape(url: str) -> tuple[str, int, str]:
    parts = urlsplit(url)
    segments = [segment for segment in parts.path.split("/") if segment]
    first = segments[0] if segments else ""
    if re.search(r"\d", first) or len(first) > 15:
        first = "*"
    return (parts.hostname or "", len(segments), first)


def _literal_prefix(url: str) -> str | None:
    segments = [segment for segment in urlsplit(url).path.split("/") if segment]
    if len(segments) >= 2 and re.fullmatch(r"[A-Za-z_-]{2,20}", segments[0]):
        return f"/{segments[0]}/"
    return None


def _group_links(
    root: Node, page_url: str, allowed_hosts: tuple[str, ...]
) -> tuple[dict[Node, str], list[str]]:
    """Anchors whose URLs share one shape and look like vacancy links."""
    page_path = urlsplit(page_url).path
    candidates: list[tuple[Node, str]] = []
    for node in root.walk():
        if node.tag != "a" or node.has_ancestor(_CHROME_TAGS):
            continue
        resolved = resolve_hit_url(page_url, node.attrs.get("href", ""), allowed_hosts)
        if resolved is None or urlsplit(resolved).path.rstrip("/") == page_path.rstrip("/"):
            continue  # pagination and "search again" links point back at the results page
        candidates.append((node, resolved))
    by_shape: dict[tuple[str, int, str], list[tuple[Node, str]]] = {}
    for node, resolved in candidates:
        by_shape.setdefault(_shape(resolved), []).append((node, resolved))
    if not by_shape:
        raise RecipeLearningError("На странице нет ссылок на разрешённых хостах")
    best = max(by_shape.values(), key=lambda group: len({url for _n, url in group}))
    if len({url for _node, url in best}) < _MIN_CARDS:
        raise RecipeLearningError(
            "Не нашлось хотя бы трёх однотипных ссылок на вакансии; "
            "откройте страницу с результатами поиска"
        )
    anchors: dict[Node, str] = {}
    seen: set[str] = set()
    for node, resolved in sorted(best, key=lambda item: -len(item[0].text())):
        if resolved not in seen:
            seen.add(resolved)
            anchors[node] = resolved
    return anchors, sorted(seen)


def _card_for(anchor: Node, group: dict[Node, str]) -> Node:
    """Highest ancestor that still contains exactly one vacancy link."""
    group_nodes = set(group)

    def links_under(node: Node) -> set[str]:
        return {group[item] for item in node.walk() if item in group_nodes}

    card = anchor
    while card.parent is not None and card.parent.tag not in _CONTAINER_TAGS:
        if len(links_under(card.parent)) != 1:
            break
        card = card.parent
    return card


def _first_match(card: Node, selector: Sel) -> Node | None:
    for node in card.walk()[1:]:
        if selector.matches(node):
            return node
    return None


def _learn_link_selector(cards: list[tuple[Node, Node]]) -> str:
    if all(card is anchor for card, anchor in cards):
        return ""
    sample_anchor = cards[0][1]
    candidates = [Sel("a", _stable_classes(sample_anchor))]
    prefix = _literal_prefix(sample_anchor.attrs.get("href", ""))
    if prefix:
        candidates.append(Sel("a", href_contains=prefix))
    candidates.append(Sel("a"))
    for candidate in candidates:
        if all(_first_match(card, candidate) is anchor for card, anchor in cards):
            return candidate.css()
    raise RecipeLearningError("Не удалось однозначно выделить ссылку внутри карточки")


def _share(cards: list[Node], selector: Sel) -> float:
    hits = sum(1 for card in cards if (node := _first_match(card, selector)) and node.text())
    return hits / len(cards)


def _learn_title_selector(cards: list[tuple[Node, Node]]) -> str:
    nodes = [card for card, _anchor in cards]
    for tag in _HEADING_TAGS:
        selector = Sel(tag)
        if _share(nodes, selector) >= 0.8:
            return selector.css()
    if all(anchor.text() for _card, anchor in cards):
        return ""
    for hint in ("title", "position", "role", "name"):
        sample = next(
            (n for n in nodes[0].walk()[1:] if any(hint in c.lower() for c in n.classes)), None
        )
        if sample is not None:
            selector = _selector_for(sample)
            if _share(nodes, selector) >= 0.8:
                return selector.css()
    return ""


def _learn_company_selector(cards: list[tuple[Node, Node]]) -> str:
    nodes = [card for card, _anchor in cards]
    sample = next(
        (
            n
            for n in nodes[0].walk()[1:]
            if n.text() and any(_COMPANY_HINT.search(c) for c in _stable_classes(n))
        ),
        None,
    )
    if sample is None:
        return ""
    selector = _selector_for(sample)
    return selector.css() if _share(nodes, selector) >= 0.5 else ""


_MAX_DISAMBIGUATION_LEVELS = 4


def _matches_some_ancestor(node: Node, selector: Sel) -> bool:
    ancestor = node.parent
    while ancestor is not None:
        if selector.matches(ancestor):
            return True
        ancestor = ancestor.parent
    return False


def _disambiguate_card_selector(
    root: Node, cards: list[tuple[Node, Node]], selector: Sel, count: int
) -> tuple[str, int] | None:
    """A card is often a bare tag with no class of its own (e.g. a plain ``<li>``) that also
    matches unrelated elements elsewhere on the page (nav menus, footers). Its immediate parent
    can be just as bare (a plain ``<ul>``). Walk up one ancestor level at a time - not just the
    direct parent - for the closest ancestor shared by every card whose own selector, chained in
    front as a descendant selector, narrows the match count back down to roughly just the cards.
    """
    level_nodes: list[Node | None] = [card for card, _anchor in cards]
    for _level in range(_MAX_DISAMBIGUATION_LEVELS):
        level_nodes = [node.parent if node else None for node in level_nodes]
        if any(node is None for node in level_nodes):
            return None
        signatures = Counter(_selector_for(node) for node in level_nodes)  # type: ignore[arg-type]
        ancestor_selector, frequency = signatures.most_common(1)[0]
        if frequency < len(level_nodes):
            continue  # not shared by every card at this depth; try one level further up
        chained = [
            node
            for node in root.walk()
            if selector.matches(node) and _matches_some_ancestor(node, ancestor_selector)
        ]
        if chained and len(chained) <= count * 2:
            return f"{ancestor_selector.css()} {selector.css()}", min(count, len(chained))
    return None


def _content_disambiguated_selector(
    root: Node, cards: list[tuple[Node, Node]], selector: Sel, count: int
) -> tuple[str, int] | None:
    """Some sites repeat the exact same card markup for an unrelated widget (e.g. a "most
    searched" or "related jobs" list using the same bare ``<li><a href="/jobs/...">`` markup as
    the real results, or an empty placeholder slot for another breakpoint), so even the closest
    classed ancestor is shared with it. What only a real card has is the vacancy link itself -
    require it, the same way a person would refine the selector by hand. The known-good cards'
    own links usually share more than just the site's generic "/jobs/"-style path prefix (a
    per-query slug, a shared id fragment); prefer that longer, more specific common prefix over
    the generic one when it is there, so a same-shaped but unrelated link list is still excluded.
    """
    hrefs = [anchor.attrs.get("href", "") for _card, anchor in cards]
    if not hrefs:
        return None
    common_prefix = os.path.commonprefix(hrefs)
    prefix = common_prefix if len(common_prefix) > 8 else _literal_prefix(hrefs[0])
    if not prefix or '"' in prefix:
        return None
    refined = replace(selector, has_link_href_contains=prefix)
    if not all(refined.matches(card) for card, _anchor in cards):
        return None
    matches = [node for node in root.walk() if refined.matches(node)]
    if matches and len(matches) <= count * 2:
        return refined.css(), len(matches)
    return None


def learn_selectors(
    html: str, *, page_url: str, allowed_hosts: tuple[str, ...] | list[str]
) -> LearnedSelectors:
    hosts = tuple(allowed_hosts)
    root = parse_html(html)
    group, _urls = _group_links(root, page_url, hosts)
    pairs = [(_card_for(anchor, group), anchor) for anchor in group]
    signatures = Counter(_selector_for(card) for card, _anchor in pairs)
    selector, count = signatures.most_common(1)[0]
    if count < _MIN_CARDS:
        raise RecipeLearningError("Карточки вакансий на странице слишком разнородны")
    cards = [(card, anchor) for card, anchor in pairs if _selector_for(card) == selector]
    total_matches = sum(1 for node in root.walk() if selector.matches(node))
    card_css = selector.css()
    if total_matches > count * 2:
        disambiguated = _content_disambiguated_selector(
            root, cards, selector, count
        ) or _disambiguate_card_selector(root, cards, selector, count)
        if disambiguated is None:
            raise RecipeLearningError("Селектор карточки неоднозначен")
        card_css, count = disambiguated
    return LearnedSelectors(
        card_selector=card_css,
        link_selector=_learn_link_selector(cards),
        title_selector=_learn_title_selector(cards),
        company_selector=_learn_company_selector(cards),
        card_count=count,
    )
