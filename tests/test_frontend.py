from html.parser import HTMLParser

from fastapi.testclient import TestClient


class ElementIndex(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: dict[str, tuple[str, dict[str, str | None]]] = {}
        self.stylesheets: list[str] = []
        self.scripts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        element_id = attributes.get("id")
        if element_id:
            self.elements[element_id] = (tag, attributes)
        if tag == "link" and attributes.get("rel") == "stylesheet":
            href = attributes.get("href")
            if href:
                self.stylesheets.append(href)
        if tag == "script":
            source = attributes.get("src")
            if source:
                self.scripts.append(source)


def test_root_serves_frontend_with_local_assets(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")

    document = ElementIndex()
    document.feed(response.text)
    assert document.stylesheets == ["/static/styles.css"]
    assert document.scripts == ["/static/app.js"]


def test_static_stylesheet_and_script_are_served(client: TestClient) -> None:
    stylesheet = client.get("/static/styles.css")
    script = client.get("/static/app.js")

    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert script.status_code == 200
    assert script.headers["content-type"].split(";")[0] in {
        "application/javascript",
        "text/javascript",
    }


def test_frontend_contains_required_controls_and_states(
    client: TestClient,
) -> None:
    response = client.get("/")
    document = ElementIndex()
    document.feed(response.text)

    expected_elements = {
        "search-form": "form",
        "name-filter": "input",
        "country-filter": "input",
        "category-filter": "input",
        "search-button": "button",
        "clear-button": "button",
        "page-size": "select",
        "results": "section",
        "result-count": "p",
        "results-grid": "div",
        "loading-state": "div",
        "error-state": "div",
        "empty-state": "div",
        "pagination-controls": "nav",
        "previous-page": "button",
        "next-page": "button",
    }
    assert {
        element_id: document.elements[element_id][0]
        for element_id in expected_elements
    } == expected_elements

    assert document.elements["result-count"][1]["aria-live"] == "polite"
    assert document.elements["loading-state"][1]["role"] == "status"
    assert document.elements["loading-state"][1]["aria-live"] == "polite"
    assert document.elements["error-state"][1]["role"] == "alert"
    assert all(
        f'value="{page_size}"' in response.text
        for page_size in ("10", "20", "50", "100")
    )


def test_frontend_script_uses_safe_stateful_browser_apis(
    client: TestClient,
) -> None:
    script = client.get("/static/app.js").text

    assert "AbortController" in script
    assert 'addEventListener("popstate"' in script
    assert "window.history.pushState" in script
    assert "window.history.replaceState" in script
    assert "document.createElement" in script
    assert ".textContent" in script
    assert "genuineAliases(distributor.name, distributor.aliases)" in script
    assert '.normalize("NFKC")' in script
    assert '.replace(/\\s+/gu, " ")' in script
    assert "innerHTML" not in script
