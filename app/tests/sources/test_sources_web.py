"""Web evidence: the blocklist, extraction, assembly and every way it can come back thin.

Nothing here touches the network; searches are canned and page fetches go through an
``httpx.MockTransport``.
"""

from __future__ import annotations

import httpx
import pytest

from config.settings import Settings
from core.sources.web import (
    BLOCKED_HOSTS,
    EvidenceSource,
    HttpSearchProvider,
    IssuerEvidence,
    NullSearchProvider,
    SearchHit,
    StaticSearchProvider,
    WebEvidenceGatherer,
    extract_text,
    is_blocked,
)

PAGE = """
<html><head>
  <title>Nordkap Funding B.V.</title>
  <meta name="description" content="Financing vehicle of the Nordkap group.">
  <style>.a { color: red }</style>
</head><body>
  <nav>Home About Contact</nav>
  <script>var tracking = 1;</script>
  <h1>About us</h1>
  <p>Nordkap Funding B.V. issues bonds and on-lends the proceeds to its parent bank.</p>
  <p>The company does not take deposits from the public.</p>
  <footer>All rights reserved</footer>
</body></html>
"""


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "web_enabled": True,
        "web_min_interval_seconds": 0.0,
        "web_max_pages": 2,
        "web_max_results": 4,
        "web_search_url": None,
    }
    base.update(overrides)
    return Settings(**base)


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def page_client(body: str = PAGE, *, status: int = 200, content_type: str = "text/html"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=body, headers={"content-type": content_type})

    return client(handler)


class TestBlocklist:
    @pytest.mark.parametrize(
        "url",
        [
            "https://apl.czso.cz/irsw/detail.jsp?prajed_id=1",
            "https://or.justice.cz/ias/ui/rejstrik",
            "https://www.or.justice.cz/x",
            "http://sub.apl.czso.cz/y",
        ],
    )
    def test_czech_registers_are_refused(self, url: str) -> None:
        """A hard rule from the brief: these have a sanctioned API and must not be scraped."""
        assert is_blocked(url)

    @pytest.mark.parametrize("url", ["https://example.com/about", "http://issuer.de/ir"])
    def test_ordinary_sites_are_allowed(self, url: str) -> None:
        assert not is_blocked(url)

    @pytest.mark.parametrize(
        "url", ["file:///etc/passwd", "ftp://host/x", "javascript:alert(1)", ""]
    )
    def test_non_http_schemes_are_refused(self, url: str) -> None:
        assert is_blocked(url)

    def test_the_rule_is_enforced_at_fetch_even_if_a_hit_slips_through(self) -> None:
        """Defence in depth: the fetcher refuses a blocked host itself, not only the filter."""
        gatherer = WebEvidenceGatherer(
            settings(), provider=NullSearchProvider(), client=page_client()
        )
        text, error = gatherer._read("https://or.justice.cz/anything")
        assert text == ""
        assert error is not None and "blocked" in error

    def test_blocked_hosts_covers_both_names_from_the_brief(self) -> None:
        assert {"apl.czso.cz", "or.justice.cz"} <= BLOCKED_HOSTS


class TestExtraction:
    def test_meta_description_is_picked_up(self) -> None:
        meta, _title, _body = extract_text(PAGE)
        assert meta == "Financing vehicle of the Nordkap group."

    def test_title_is_picked_up(self) -> None:
        _meta, title, _body = extract_text(PAGE)
        assert title == "Nordkap Funding B.V."

    def test_body_text_is_readable(self) -> None:
        _meta, _title, body = extract_text(PAGE)
        assert "on-lends the proceeds to its parent bank" in body

    def test_scripts_styles_and_chrome_are_dropped(self) -> None:
        """Navigation and tracking code would be noise in the classifier's prompt."""
        _meta, _title, body = extract_text(PAGE)
        assert "tracking" not in body
        assert "color: red" not in body
        assert "All rights reserved" not in body

    def test_og_description_is_accepted_too(self) -> None:
        meta, _, _ = extract_text('<meta property="og:description" content="Captive lender.">')
        assert meta == "Captive lender."

    def test_malformed_html_does_not_raise(self) -> None:
        meta, title, body = extract_text("<p>unclosed <b>bold <div>text")
        assert "unclosed" in body and isinstance(meta, str) and isinstance(title, str)


class TestSuppliedDescription:
    def test_user_text_is_used_verbatim_and_no_search_happens(self) -> None:
        """What MO typed is authoritative; the web only fills a gap."""
        provider = StaticSearchProvider([SearchHit(url="https://example.com")])
        gatherer = WebEvidenceGatherer(settings(), provider=provider, client=page_client())
        evidence = gatherer.gather(name="X", description="  a captive finance vehicle  ")

        assert evidence.description == "a captive finance vehicle"
        assert evidence.sources == ()
        assert evidence.provenance is not None and evidence.provenance.detail == "user-supplied"


class TestGathering:
    def _gatherer(self, hits, **overrides) -> WebEvidenceGatherer:
        return WebEvidenceGatherer(
            settings(**overrides), provider=StaticSearchProvider(hits), client=page_client()
        )

    def test_a_page_is_fetched_and_described(self) -> None:
        evidence = self._gatherer(
            [SearchHit(url="https://example.com/about", title="Nordkap")]
        ).gather(name="Nordkap Funding B.V.")
        assert evidence.has_description
        assert "parent bank" in evidence.description
        assert evidence.sources[0].fetched

    def test_sources_are_recorded_for_the_reviewer(self) -> None:
        evidence = self._gatherer(
            [SearchHit(url="https://example.com/a", title="A", snippet="s")]
        ).gather(name="X")
        assert evidence.sources[0].url == "https://example.com/a"
        assert evidence.sources[0].retrieved_at is not None

    def test_blocked_results_are_dropped_and_noted(self) -> None:
        evidence = self._gatherer(
            [SearchHit(url="https://or.justice.cz/x"), SearchHit(url="https://example.com/a")]
        ).gather(name="X")
        assert all("justice.cz" not in source.url for source in evidence.sources)
        assert any("must not be scraped" in note for note in evidence.notes)

    def test_only_blocked_results_yields_no_description(self) -> None:
        evidence = self._gatherer([SearchHit(url="https://or.justice.cz/x")]).gather(name="X")
        assert not evidence.has_description

    def test_pages_beyond_the_cap_contribute_only_their_snippet(self) -> None:
        hits = [SearchHit(url=f"https://example.com/{i}", snippet=f"snippet {i}") for i in range(4)]
        evidence = self._gatherer(hits, web_max_pages=1).gather(name="X")
        assert sum(1 for source in evidence.sources if source.fetched) == 1
        assert "snippet 1" in evidence.description

    def test_issuer_name_falls_back_to_the_best_hit_title(self) -> None:
        evidence = self._gatherer(
            [SearchHit(url="https://example.com/a", title="Nordkap Funding B.V.")]
        ).gather(isin="XS2345678901")
        assert evidence.issuer_name == "Nordkap Funding B.V."

    def test_description_is_capped_at_a_sentence_boundary(self) -> None:
        long_page = "<html><body><p>" + ("Sentence about the issuer. " * 200) + "</p></body></html>"
        gatherer = WebEvidenceGatherer(
            settings(web_max_description_chars=300),
            provider=StaticSearchProvider([SearchHit(url="https://example.com/a")]),
            client=page_client(long_page),
        )
        description = gatherer.gather(name="X").description
        assert description is not None
        assert len(description) <= 300
        assert description.endswith(".")


class TestThinResults:
    """Every one of these must come back as "no evidence", never as an exception or a guess."""

    def test_no_query_at_all(self) -> None:
        evidence = WebEvidenceGatherer(settings(), provider=NullSearchProvider()).gather()
        assert not evidence.has_description
        assert "nothing to search for" in evidence.notes[0]

    def test_web_disabled(self) -> None:
        gatherer = WebEvidenceGatherer(settings(web_enabled=False), provider=NullSearchProvider())
        evidence = gatherer.gather(name="X")
        assert not evidence.has_description
        assert "disabled" in evidence.notes[0]

    def test_no_provider_configured(self) -> None:
        evidence = WebEvidenceGatherer(settings(), provider=NullSearchProvider()).gather(name="X")
        assert not evidence.has_description
        assert any("no search provider" in note for note in evidence.notes)

    def test_search_failure_is_reported_not_raised(self) -> None:
        class Failing:
            name = "failing"

            def search(self, query: str, *, limit: int):
                from core.sources.base import SourceUnavailableError

                raise SourceUnavailableError("search is down")

        evidence = WebEvidenceGatherer(settings(), provider=Failing()).gather(name="X")
        assert not evidence.has_description
        assert any("search failed" in note for note in evidence.notes)

    def test_unfetchable_page_is_noted(self) -> None:
        gatherer = WebEvidenceGatherer(
            settings(),
            provider=StaticSearchProvider([SearchHit(url="https://example.com/a")]),
            client=page_client(status=404),
        )
        evidence = gatherer.gather(name="X")
        assert any("404" in note for note in evidence.notes)

    def test_non_html_document_is_skipped(self) -> None:
        gatherer = WebEvidenceGatherer(
            settings(),
            provider=StaticSearchProvider([SearchHit(url="https://example.com/a.pdf")]),
            client=page_client(content_type="application/pdf"),
        )
        evidence = gatherer.gather(name="X")
        assert any("not a readable document" in note for note in evidence.notes)


class TestHttpSearchProvider:
    def test_reads_results_through_the_configured_paths(self) -> None:
        body = {
            "web": {
                "results": [
                    {"url": "https://example.com/a", "title": "A", "description": "about A"},
                    {"url": "https://example.com/b", "title": "B", "description": "about B"},
                ]
            }
        }
        provider = HttpSearchProvider(
            settings(web_search_url="https://search.test/?q={query}"),
            client=client(lambda request: httpx.Response(200, json=body)),
        )
        hits = provider.search("nordkap", limit=5)
        assert [hit.url for hit in hits] == ["https://example.com/a", "https://example.com/b"]
        assert hits[0].snippet == "about A"

    def test_limit_is_applied(self) -> None:
        body = {"web": {"results": [{"url": f"https://example.com/{i}"} for i in range(9)]}}
        provider = HttpSearchProvider(
            settings(web_search_url="https://search.test/?q={query}"),
            client=client(lambda request: httpx.Response(200, json=body)),
        )
        assert len(provider.search("x", limit=3)) == 3

    def test_a_wrong_results_path_returns_nothing_rather_than_crashing(self) -> None:
        provider = HttpSearchProvider(
            settings(
                web_search_url="https://s.test/?q={query}", web_search_results_path="not.here"
            ),
            client=client(lambda request: httpx.Response(200, json={"web": {"results": []}})),
        )
        assert provider.search("x", limit=3) == ()

    def test_server_error_is_unavailable(self) -> None:
        from core.sources.base import SourceUnavailableError

        provider = HttpSearchProvider(
            settings(web_search_url="https://s.test/?q={query}"),
            client=client(lambda request: httpx.Response(503)),
        )
        with pytest.raises(SourceUnavailableError):
            provider.search("x", limit=3)

    def test_no_url_configured_means_no_search(self) -> None:
        assert HttpSearchProvider(settings()).search("x", limit=3) == ()

    def test_the_api_key_is_sent_as_a_header_not_in_the_url(self) -> None:
        """A key in a query string ends up in logs and referrers."""
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(request.headers)
            seen["_url"] = str(request.url)
            return httpx.Response(200, json={"web": {"results": []}})

        provider = HttpSearchProvider(
            settings(web_search_url="https://s.test/?q={query}", web_search_api_key="secret-key"),
        )
        provider._client = httpx.Client(
            transport=httpx.MockTransport(handler),
            headers={provider._settings.web_search_api_key_header: "secret-key"},
        )
        provider.search("x", limit=1)
        assert "secret-key" not in seen["_url"]


class TestThrottle:
    def test_page_fetches_are_spaced_out(self) -> None:
        slept: list[float] = []
        clock = iter([0.0, 0.10, 0.50])
        gatherer = WebEvidenceGatherer(
            settings(web_min_interval_seconds=0.5, web_max_pages=2),
            provider=StaticSearchProvider(
                [SearchHit(url="https://example.com/a"), SearchHit(url="https://example.com/b")]
            ),
            client=page_client(),
            sleep=slept.append,
            monotonic=lambda: next(clock),
        )
        gatherer.gather(name="X")
        assert slept and slept[0] == pytest.approx(0.40)


def test_evidence_without_a_description_is_falsy() -> None:
    assert not IssuerEvidence(query="x").has_description
    assert not IssuerEvidence(query="x", description="   ").has_description


def test_evidence_source_defaults_are_safe() -> None:
    source = EvidenceSource(url="https://example.com")
    assert not source.fetched and source.retrieved_at is None
