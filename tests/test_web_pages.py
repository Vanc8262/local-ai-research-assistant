import httpx

import assistant.web_pages
from assistant.web_pages import search_pages

assistant.web_pages.MIN_GAP_S = 0   # no pacing in tests





def test_results_carry_page_passages_and_fall_back_to_the_snippet():
    long_page = "<html><nav>menu</nav><p>" + "filler text. " * 300 + "QLoRA uses 4-bit NormalFloat (NF4) quantization. " \
                + "more filler. " * 300 + "</p></html>"

    def handler(request):
        if request.url.path == "/search":
            return httpx.Response(200, json={"results": [
                {"title": "QLoRA", "url": "https://a.example/qlora", "content": "QLoRA paper"},
                {"title": "Down", "url": "https://b.example/down", "content": "only a snippet"},
                {"title": "Third", "url": "https://c.example/3", "content": "beyond k"}]})
        if request.url.host == "a.example":
            return httpx.Response(200, text=long_page, headers={"content-type": "text/html"})
        return httpx.Response(503)

    first, second = search_pages("http://127.0.0.1:8888", "QLoRA NF4 quantization", k=2,
                                 transport=httpx.MockTransport(handler))
    assert first["url"] == "https://a.example/qlora" and first["text"].startswith("QLoRA paper")
    assert "NF4" in first["text"] and "menu" not in first["text"] and len(first["text"]) < 2500
    assert second["text"] == "only a snippet"


def test_tavily_answers_only_when_searxng_is_empty_and_a_key_was_passed():
    seen = []

    def handler(request):
        seen.append((request.url.host, request.headers.get("authorization", "")))
        if request.url.path == "/search" and request.url.host == "127.0.0.1":
            return httpx.Response(200, json={"results": [], "unresponsive_engines": [["brave", "Suspended"]]})
        if request.url.host == "api.tavily.com":
            return httpx.Response(200, json={"results": [{"title": "T", "url": "https://t.example/a", "content": "from tavily"}]})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    assert search_pages("http://127.0.0.1:8888", "q", transport=transport) == []
    assert all(host != "api.tavily.com" for host, _ in seen)        # no key, no cloud request
    (hit,) = search_pages("http://127.0.0.1:8888", "q", transport=transport, tavily_key="tvly-k")
    assert hit["engine"] == "tavily" and hit["text"] == "from tavily"
    assert ("api.tavily.com", "Bearer tvly-k") in seen
