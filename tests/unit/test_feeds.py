import pytest

from vaya_indexer.connectors.feeds import parse_feed
from vaya_indexer.core.exceptions import ParseError

# Feeds SINTETICOS.
RSS = """<?xml version="1.0"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel><title>Vagas</title>
  <item><title> Dev  Backend </title><link>https://exemplo.test/1</link>
    <guid>job-1</guid><pubDate>Wed, 01 May 2024 10:00:00 GMT</pubDate>
    <description>Resumo</description><content:encoded><![CDATA[<p>Corpo</p>]]></content:encoded>
    <dc:creator>RH</dc:creator><category>TI</category></item>
  <item><title>Sem guid</title><link>https://exemplo.test/2</link></item>
  <item><link>https://exemplo.test/3</link></item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><id>urn:job:7</id><title>Analista</title>
    <link rel="self" href="https://exemplo.test/self"/>
    <link rel="alternate" href="https://exemplo.test/7"/>
    <published>2024-05-01T10:00:00Z</published><updated>2024-05-02T10:00:00Z</updated>
    <summary>S</summary><content type="html">C</content>
    <author><name>Fulano</name></author><category term="dados"/></entry>
</feed>"""


def test_rss_entries() -> None:
    entries = parse_feed(RSS)
    assert [e.id for e in entries] == ["job-1", "https://exemplo.test/2"]
    first = entries[0]
    assert first.title == "Dev Backend" and first.link == "https://exemplo.test/1"
    assert first.published is not None and first.published.year == 2024
    assert first.content == "<p>Corpo</p>" and first.summary == "Resumo"
    assert first.author == "RH" and first.categories == ["TI"]


def test_atom_entries() -> None:
    (entry,) = parse_feed(ATOM.encode())
    assert entry.id == "urn:job:7" and entry.link == "https://exemplo.test/7"
    assert entry.published is not None and entry.updated is not None
    assert entry.author == "Fulano" and entry.categories == ["dados"]


def test_invalid_and_unknown_feeds() -> None:
    with pytest.raises(ParseError):
        parse_feed("<rss><channel>")
    with pytest.raises(ParseError):
        parse_feed("<html><body/></html>")


def test_entities_are_not_expanded() -> None:
    xml = (
        '<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY x "EXPANDIDO">]>'
        "<rss><channel><item><title>&x;</title><guid>1</guid></item></channel></rss>"
    )
    try:
        entries = parse_feed(xml)
    except ParseError:
        return  # rejeitar tambem e seguro
    assert all("EXPANDIDO" not in e.title for e in entries)
