import httpx

from assistant.web_pages import search_pages





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
