"""联网搜索模块 — 为 AI 答题提供实时搜索结果上下文"""
from abc import ABC, abstractmethod

import requests

from api.logger import logger


class WebSearch(ABC):
    """搜索抽象基类"""

    @abstractmethod
    def search(self, query: str, max_results: int = 3) -> list[dict]:
        """返回 [{'title': str, 'snippet': str, 'url': str}, ...]"""


class DuckDuckGoSearch(WebSearch):
    """DuckDuckGo HTML 搜索（免费，无需 API Key）"""

    def search(self, query: str, max_results: int = 3) -> list[dict]:
        try:
            resp = requests.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                timeout=10,
            )
            if resp.status_code != 200:
                logger.warning(f"DuckDuckGo 搜索失败: HTTP {resp.status_code}")
                return []

            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            for item in soup.select(".result")[:max_results]:
                title_el = item.select_one(".result__title a")
                snippet_el = item.select_one(".result__snippet")
                if title_el:
                    results.append({
                        "title": title_el.get_text(strip=True),
                        "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
                        "url": title_el.get("href", ""),
                    })
            return results
        except Exception as e:
            logger.warning(f"DuckDuckGo 搜索异常: {e}")
            return []


class CustomSearch(WebSearch):
    """自定义搜索 API（用户配置 endpoint + key）"""

    def __init__(self, endpoint: str, key: str = ""):
        self.endpoint = endpoint.rstrip("/")
        self.key = key

    def search(self, query: str, max_results: int = 3) -> list[dict]:
        try:
            headers = {"Content-Type": "application/json"}
            if self.key:
                headers["Authorization"] = f"Bearer {self.key}"

            resp = requests.post(
                self.endpoint,
                json={"query": query, "max_results": max_results},
                headers=headers,
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                # 兼容常见响应格式: {"results": [...]} 或直接数组
                items = data if isinstance(data, list) else data.get("results", [])
                return [
                    {
                        "title": r.get("title", ""),
                        "snippet": r.get("snippet", r.get("content", "")),
                        "url": r.get("url", ""),
                    }
                    for r in items[:max_results]
                ]
            logger.warning(f"自定义搜索失败: HTTP {resp.status_code}")
            return []
        except Exception as e:
            logger.warning(f"自定义搜索异常: {e}")
            return []


def create_search(config: dict) -> WebSearch | None:
    """工厂函数：根据配置创建搜索实例"""
    provider = config.get("search_provider", "duckduckgo")
    if provider == "duckduckgo":
        return DuckDuckGoSearch()
    elif provider == "custom":
        endpoint = config.get("search_endpoint", "")
        if not endpoint:
            logger.warning("自定义搜索未配置 endpoint，已禁用")
            return None
        return CustomSearch(endpoint=endpoint, key=config.get("search_key", ""))
    return None
