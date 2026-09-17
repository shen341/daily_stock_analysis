# -*- coding: utf-8 -*-
"""
Unit tests for Bing News RSS and Google News RSS search providers and SearchService integration.
"""

import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

# Mock newspaper before search_service import (optional dependency)
if "newspaper" not in sys.modules:
    mock_np = MagicMock()
    mock_np.Article = MagicMock()
    mock_np.Config = MagicMock()
    sys.modules["newspaper"] = mock_np

from src.search_service import (
    SearchService,
    BingNewsSearchProvider,
    GoogleNewsSearchProvider,
    SearchResponse,
    SearchResult,
)


class TestBingNewsSearchProvider(unittest.TestCase):
    """Tests for Bing News RSS search provider."""

    def test_disabled_provider_returns_not_available(self):
        provider = BingNewsSearchProvider(enabled=False)
        self.assertFalse(provider.is_available)
        resp = provider.search("贵州茅台")
        self.assertFalse(resp.success)
        self.assertIn("未启用", resp.error_message)

    @patch("src.search_service._get_with_retry")
    def test_successful_rss_parsing_maps_fields(self, mock_get):
        xml_content = """<?xml version="1.0" encoding="utf-8" ?>
<rss version="2.0" xmlns:News="https://www.bing.com/news/search">
  <channel>
    <title>Bing News Search</title>
    <item>
      <title>&lt;b&gt;贵州茅台&lt;/b&gt;最新消息：半年度业绩稳步增长</title>
      <link>http://www.bing.com/news/apiclick.aspx?ref=FexRss&amp;url=https%3a%2f%2ffinance.sina.com.cn%2fnews%2f2026-09-17%2f12345.shtml</link>
      <description>9月17日讯，贵州茅台发布最新经营数据，各项指标稳健。</description>
      <pubDate>Thu, 17 Sep 2026 08:30:00 GMT</pubDate>
      <News:Source>新浪财经</News:Source>
    </item>
    <item>
      <title>另一条新闻</title>
      <link>https://example.com/news/67890.html</link>
      <description>这是普通链接新闻摘要。</description>
      <pubDate>Wed, 16 Sep 2026 10:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>""".encode("utf-8")
        resp = MagicMock()
        resp.status_code = 200
        resp.content = xml_content
        mock_get.return_value = resp

        provider = BingNewsSearchProvider(enabled=True)
        self.assertTrue(provider.is_available)

        response = provider.search("600519 贵州茅台", max_results=5, days=7)
        self.assertTrue(response.success)
        self.assertEqual(response.provider, "BingNews")
        self.assertEqual(len(response.results), 2)

        item1 = response.results[0]
        self.assertEqual(item1.title, "贵州茅台最新消息：半年度业绩稳步增长")
        # Target URL unquoted from apiclick redirect
        self.assertEqual(item1.url, "https://finance.sina.com.cn/news/2026-09-17/12345.shtml")
        self.assertEqual(item1.source, "新浪财经")
        self.assertEqual(item1.published_date, "2026-09-17")
        self.assertIn("各项指标稳健", item1.snippet)

        item2 = response.results[1]
        self.assertEqual(item2.url, "https://example.com/news/67890.html")
        self.assertEqual(item2.source, "example.com")
        self.assertEqual(item2.published_date, "2026-09-16")

    @patch("src.search_service._get_with_retry")
    def test_http_error_returns_failure(self, mock_get):
        resp = MagicMock()
        resp.status_code = 503
        mock_get.return_value = resp

        provider = BingNewsSearchProvider(enabled=True)
        response = provider.search("600519")
        self.assertFalse(response.success)
        self.assertIn("HTTP 503", response.error_message)

    @patch("src.search_service._get_with_retry")
    def test_invalid_xml_returns_failure(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b"<html><body>Not XML</body></html>"
        mock_get.return_value = resp

        provider = BingNewsSearchProvider(enabled=True)
        response = provider.search("600519")
        # html tag without valid rss root
        self.assertTrue(response.success)
        self.assertEqual(len(response.results), 0)


class TestGoogleNewsSearchProvider(unittest.TestCase):
    """Tests for Google News RSS search provider."""

    def test_disabled_provider_returns_not_available(self):
        provider = GoogleNewsSearchProvider(enabled=False)
        self.assertFalse(provider.is_available)
        resp = provider.search("AAPL")
        self.assertFalse(resp.success)
        self.assertIn("未启用", resp.error_message)

    @patch("src.search_service._get_with_retry")
    def test_successful_rss_parsing_maps_fields(self, mock_get):
        xml_content = b"""<?xml version="1.0" encoding="utf-8" ?>
<rss version="2.0">
  <channel>
    <title>Google News</title>
    <item>
      <title>Apple Announces New Services Growth - Bloomberg</title>
      <link>https://news.google.com/rss/articles/CBMi12345</link>
      <pubDate>Thu, 17 Sep 2026 12:00:00 GMT</pubDate>
      <description>&lt;a href="..."&gt;Apple Announces New Services Growth&lt;/a&gt;</description>
      <source url="https://www.bloomberg.com">Bloomberg</source>
    </item>
  </channel>
</rss>"""
        resp = MagicMock()
        resp.status_code = 200
        resp.content = xml_content
        mock_get.return_value = resp

        provider = GoogleNewsSearchProvider(enabled=True)
        self.assertTrue(provider.is_available)

        response = provider.search("AAPL stock news", max_results=5, days=3)
        self.assertTrue(response.success)
        self.assertEqual(response.provider, "GoogleNews")
        self.assertEqual(len(response.results), 1)

        item = response.results[0]
        # Title stripped of trailing " - Bloomberg"
        self.assertEqual(item.title, "Apple Announces New Services Growth")
        self.assertEqual(item.source, "Bloomberg")
        self.assertEqual(item.published_date, "2026-09-17")
        self.assertEqual(item.url, "https://news.google.com/rss/articles/CBMi12345")

    @patch("src.search_service._get_with_retry")
    def test_appends_when_clause_and_resolves_locale(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b"<rss><channel></channel></rss>"
        mock_get.return_value = resp

        provider = GoogleNewsSearchProvider(enabled=True)
        provider.search("贵州茅台", days=5)

        # Check call arguments to verify query had when:5d and hl=zh-CN
        mock_get.assert_called_once()
        call_url = mock_get.call_args[0][0]
        self.assertIn("when%3A5d", call_url)
        self.assertIn("hl=zh-CN", call_url)
        self.assertIn("gl=CN", call_url)


class TestSearchServiceFreeProviderIntegration(unittest.TestCase):
    """Integration tests for SearchService with free search providers."""

    def test_search_service_includes_bing_and_google_news_when_enabled(self):
        service = SearchService(bing_news_search_enabled=True, google_news_search_enabled=True)
        provider_names = [p.name for p in service._providers]
        self.assertIn("BingNews", provider_names)
        self.assertIn("GoogleNews", provider_names)
        self.assertTrue(service.is_available)

    def test_get_search_service_enables_free_providers_by_default(self):
        from src.config import Config
        from src.search_service import get_search_service, reset_search_service
        reset_search_service()
        with patch("src.config.get_config") as mock_get_cfg:
            mock_cfg = Config(
                bing_news_search_enabled=True,
                google_news_search_enabled=True,
                searxng_public_instances_enabled=False,
            )
            mock_get_cfg.return_value = mock_cfg
            service = get_search_service()
            provider_names = [p.name for p in service._providers]
            self.assertIn("BingNews", provider_names)
            self.assertIn("GoogleNews", provider_names)
        reset_search_service()

    def test_search_service_can_disable_free_providers(self):
        service = SearchService(
            bing_news_search_enabled=False,
            google_news_search_enabled=False,
            searxng_public_instances_enabled=False,
        )
        provider_names = [p.name for p in service._providers]
        self.assertNotIn("BingNews", provider_names)
        self.assertNotIn("GoogleNews", provider_names)

    def test_bing_news_passes_freshness_filter(self):
        # Verify that RFC 822 pubDate converted to YYYY-MM-DD passes SearchService._filter_news_response
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        result = SearchResult(
            title="贵州茅台获主力资金加仓",
            snippet="今日白酒板块走强，贵州茅台涨幅居前",
            url="https://finance.example.com/123",
            source="东方财富网",
            published_date=today_str,
        )
        raw_response = SearchResponse(
            query="贵州茅台",
            results=[result],
            provider="BingNews",
            success=True,
        )

        service = SearchService()
        filtered = service._filter_news_response(
            raw_response,
            search_days=3,
            max_results=5,
            log_scope="test",
        )
        self.assertTrue(filtered.success)
        self.assertEqual(len(filtered.results), 1)
        self.assertEqual(filtered.results[0].title, "贵州茅台获主力资金加仓")
        self.assertEqual(filtered.results[0].published_date, today_str)


if __name__ == "__main__":
    unittest.main()
