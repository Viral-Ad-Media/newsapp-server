"""Fictional text fixtures used only by automated tests; never seeded into reader data."""

FULL_ARTICLE = """Solar power is the subject of a fictional community reporting exercise about how households evaluate electricity options. In this test scenario, the local information office has published guidance explaining that residents should compare their energy use, equipment requirements and installation arrangements before choosing a system. The exercise makes no claim about an actual council, company or public programme. It provides enough narrative text to exercise the application's article validation and rendering without publishing invented news.

## Solar power and household decisions

The fictional guidance starts with the practical question of what a household needs to run. Lighting, refrigeration and communications equipment can place different demands on a system, so a list of essential appliances helps explain the tradeoffs. The document describes this as a planning step rather than a promise that any particular product will meet every requirement. Its purpose within the test is to distinguish substantive paragraphs from a collection of source links or a short provider excerpt.

Installation is treated as a separate decision in the example. The scenario asks residents to consider where equipment would be placed and how maintenance would be arranged. It does not name a real installer, recommend a purchase or claim that a particular technology has been independently tested. These limits are part of the fixture so that automated tests can check a full article body without introducing factual assertions about a real commercial service or institution.

## What the example establishes

The supporting fictional document also separates equipment costs from other considerations. A household might need to understand what an installation agreement covers, which components require attention and where support would come from. No price estimates or savings projections are supplied in this exercise. An editor adapting a real report would need relevant source evidence before making any financial claim, and the article preparation service is expected to reject unsupported numbers rather than guess them.

Residents in the scenario are directed to compare written information instead of relying on an isolated headline. The exercise does not describe interviews or manufacture quotations from participants. It instead explains the limited scope of the published guidance and identifies what remains unknown. This is useful for testing because a body can contain several informative paragraphs while remaining clear about its evidence and avoiding statements that imply reporting which never took place.

The example ends with a straightforward account of the next step: the fictional office will accept questions about its guidance through its ordinary information process. There is no promised launch date, grant award or guaranteed outcome. In a real story, those details would appear only if a source established them. The complete fixture therefore supports tests for paragraph structure, descriptive headings and source attribution while keeping the summary separate from the longer reading experience."""

PREPARED = {
    "content": FULL_ARTICLE + "\n\n[Source](https://example.org/story)",
    "focus_keyword": "Solar power",
    "seo_title": "Solar power guidance for local households",
    "seo_description": "Solar power guidance explains how households can assess their electricity needs, installation requirements and the limits of available information.",
    "quality": {"passed": True, "word_count": 500},
    "seo": {"score": 85},
}
