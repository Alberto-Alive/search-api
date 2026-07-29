(() => {
  "use strict";

  const DEFAULT_PAGE_SIZE = 20;
  const PAGE_SIZES = new Set([10, 20, 50, 100]);
  const DATE_STATUS_LABELS = {
    iso: "ISO date",
    dmy: "Day / month / year",
    mdy: "Month / day / year",
    ambiguous: "Ambiguous source date",
    invalid: "Unparsed source date",
  };

  const elements = {};
  let state;
  let activeController = null;
  let requestVersion = 0;
  let isLoading = false;
  let latestPagination = {
    total_items: 0,
    total_pages: 0,
  };

  function initialize() {
    elements.form = document.querySelector("#search-form");
    elements.name = document.querySelector("#name-filter");
    elements.country = document.querySelector("#country-filter");
    elements.category = document.querySelector("#category-filter");
    elements.pageSize = document.querySelector("#page-size");
    elements.searchButton = document.querySelector("#search-button");
    elements.clearButton = document.querySelector("#clear-button");
    elements.resultCount = document.querySelector("#result-count");
    elements.loadingState = document.querySelector("#loading-state");
    elements.errorState = document.querySelector("#error-state");
    elements.errorMessage = document.querySelector("#error-message");
    elements.emptyState = document.querySelector("#empty-state");
    elements.emptyHeading = document.querySelector("#empty-heading");
    elements.emptyMessage = document.querySelector("#empty-message");
    elements.resultsGrid = document.querySelector("#results-grid");
    elements.pagination = document.querySelector("#pagination-controls");
    elements.previousPage = document.querySelector("#previous-page");
    elements.nextPage = document.querySelector("#next-page");
    elements.pageStatus = document.querySelector("#page-status");
    elements.resultsSection = document.querySelector("#results");

    state = readStateFromUrl();
    applyStateToControls();
    writeStateToUrl("replace");

    elements.form.addEventListener("submit", handleSubmit);
    elements.clearButton.addEventListener("click", handleClear);
    elements.pageSize.addEventListener("change", handlePageSizeChange);
    elements.previousPage.addEventListener("click", handlePreviousPage);
    elements.nextPage.addEventListener("click", handleNextPage);
    window.addEventListener("popstate", handleHistoryNavigation);

    loadResults();
  }

  function handleSubmit(event) {
    event.preventDefault();
    if (isLoading) {
      return;
    }

    state = {
      ...readFiltersFromControls(),
      page: 1,
    };
    applyStateToControls();
    writeStateToUrl("push");
    loadResults();
  }

  function handleClear() {
    state = {
      name: "",
      country: "",
      category: "",
      page: 1,
      pageSize: DEFAULT_PAGE_SIZE,
    };
    applyStateToControls();
    writeStateToUrl("push");
    loadResults();
    elements.name.focus();
  }

  function handlePageSizeChange() {
    state = {
      ...readFiltersFromControls(),
      page: 1,
    };
    writeStateToUrl("push");
    loadResults();
  }

  function handlePreviousPage() {
    if (isLoading || state.page <= 1) {
      return;
    }

    state.page = Math.min(
      state.page - 1,
      Math.max(latestPagination.total_pages, 1),
    );
    writeStateToUrl("push");
    loadResults();
    elements.resultsSection.scrollIntoView({ block: "start" });
  }

  function handleNextPage() {
    if (
      isLoading
      || latestPagination.total_pages === 0
      || state.page >= latestPagination.total_pages
    ) {
      return;
    }

    state.page += 1;
    writeStateToUrl("push");
    loadResults();
    elements.resultsSection.scrollIntoView({ block: "start" });
  }

  function handleHistoryNavigation() {
    state = readStateFromUrl();
    applyStateToControls();
    loadResults();
  }

  function readStateFromUrl() {
    const parameters = new URL(window.location.href).searchParams;
    const requestedPageSize = Number(parameters.get("page_size"));

    return {
      name: parameters.get("name") || "",
      country: parameters.get("country") || "",
      category: parameters.get("category") || "",
      page: parsePositiveSafeInteger(parameters.get("page"), 1),
      pageSize: PAGE_SIZES.has(requestedPageSize)
        ? requestedPageSize
        : DEFAULT_PAGE_SIZE,
    };
  }

  function readFiltersFromControls() {
    return {
      name: elements.name.value.trim(),
      country: elements.country.value.trim(),
      category: elements.category.value.trim(),
      pageSize: Number(elements.pageSize.value),
    };
  }

  function applyStateToControls() {
    elements.name.value = state.name;
    elements.country.value = state.country;
    elements.category.value = state.category;
    elements.pageSize.value = String(state.pageSize);
  }

  function writeStateToUrl(mode) {
    const url = new URL(window.location.href);
    url.search = "";

    for (const filterName of ["name", "country", "category"]) {
      const value = state[filterName].trim();
      if (value) {
        url.searchParams.set(filterName, value);
      }
    }

    url.searchParams.set("page", String(state.page));
    url.searchParams.set("page_size", String(state.pageSize));

    const relativeUrl = `${url.pathname}${url.search}${url.hash}`;
    if (mode === "push") {
      window.history.pushState({}, "", relativeUrl);
    } else {
      window.history.replaceState({}, "", relativeUrl);
    }
  }

  function buildApiUrl() {
    const url = new URL("/api/distributors", window.location.origin);

    for (const filterName of ["name", "country", "category"]) {
      const value = state[filterName].trim();
      if (value) {
        url.searchParams.set(filterName, value);
      }
    }

    url.searchParams.set("page", String(state.page));
    url.searchParams.set("page_size", String(state.pageSize));
    return url;
  }

  async function loadResults() {
    if (activeController) {
      activeController.abort();
    }

    const controller = new AbortController();
    const version = ++requestVersion;
    activeController = controller;

    hideMessageStates();
    setLoading(true);

    try {
      const response = await fetch(buildApiUrl(), {
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });
      const contentType = response.headers.get("content-type") || "";

      if (!contentType.toLowerCase().includes("application/json")) {
        await response.text();
        throw new Error("The server returned an unexpected response.");
      }

      let payload;
      try {
        payload = await response.json();
      } catch {
        throw new Error("The server returned unreadable data.");
      }

      if (!response.ok) {
        throw new Error(readApiError(payload, response.status));
      }
      if (!isDistributorResponse(payload)) {
        throw new Error("The server returned an unexpected data format.");
      }
      if (controller.signal.aborted || version !== requestVersion) {
        return;
      }

      renderResults(payload);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        return;
      }
      if (version === requestVersion) {
        showError(
          error instanceof Error
            ? error.message
            : "An unexpected error occurred.",
        );
      }
    } finally {
      if (version === requestVersion) {
        activeController = null;
        setLoading(false);
      }
    }
  }

  function setLoading(loading) {
    isLoading = loading;
    elements.loadingState.hidden = !loading;
    elements.searchButton.disabled = loading;
    elements.pageSize.disabled = loading;
    elements.form.setAttribute("aria-busy", String(loading));
    elements.resultsGrid.setAttribute("aria-busy", String(loading));
    updatePaginationControls();
  }

  function hideMessageStates() {
    elements.errorState.hidden = true;
    elements.emptyState.hidden = true;
  }

  function showError(message) {
    latestPagination = { total_items: 0, total_pages: 0 };
    elements.resultsGrid.replaceChildren();
    elements.resultCount.textContent = "Unable to load distributors.";
    elements.errorMessage.textContent = message;
    elements.errorState.hidden = false;
    elements.emptyState.hidden = true;
    elements.pagination.hidden = true;
  }

  function renderResults(payload) {
    const { items, pagination } = payload;
    latestPagination = pagination;
    elements.resultsGrid.replaceChildren();
    elements.errorState.hidden = true;

    for (const distributor of items) {
      elements.resultsGrid.append(createDistributorCard(distributor));
    }

    if (items.length === 0) {
      elements.emptyState.hidden = false;
      if (pagination.total_items === 0) {
        elements.emptyHeading.textContent = "No distributors found";
        elements.emptyMessage.textContent =
          "Try broadening your filters or clearing the search.";
        elements.resultCount.textContent = "0 distributors found.";
      } else {
        elements.emptyHeading.textContent = "No results on this page";
        elements.emptyMessage.textContent =
          "Use Previous to return to an available page.";
        elements.resultCount.textContent =
          `${formatNumber(pagination.total_items)} distributors found; `
          + `page ${state.page} is empty.`;
      }
    } else {
      elements.emptyState.hidden = true;
      const firstItem = (state.page - 1) * state.pageSize + 1;
      const lastItem = Math.min(
        firstItem + items.length - 1,
        pagination.total_items,
      );
      elements.resultCount.textContent =
        `Showing ${formatNumber(firstItem)}–${formatNumber(lastItem)} of `
        + `${formatNumber(pagination.total_items)} distributors.`;
    }

    elements.pagination.hidden = pagination.total_items === 0;
    elements.pageStatus.textContent =
      `Page ${formatNumber(state.page)} of `
      + `${formatNumber(pagination.total_pages)}.`;
    updatePaginationControls();
  }

  function updatePaginationControls() {
    const totalPages = latestPagination.total_pages;
    elements.previousPage.disabled = isLoading || state.page <= 1;
    elements.nextPage.disabled =
      isLoading || totalPages === 0 || state.page >= totalPages;
  }

  function createDistributorCard(distributor) {
    const card = document.createElement("article");
    card.className = "distributor-card";
    card.dataset.distributorId = String(distributor.id);

    const header = document.createElement("header");
    header.className = "card-header";
    appendTextElement(header, "h3", "card-title", distributor.name);
    appendTextElement(header, "p", "card-country", distributor.country);
    card.append(header);

    const aliases = genuineAliases(distributor.name, distributor.aliases);
    if (aliases.length > 0) {
      const aliasLine = document.createElement("p");
      aliasLine.className = "aliases";
      appendTextElement(aliasLine, "span", "aliases-label", "Also known as ");
      aliasLine.append(document.createTextNode(aliases.join(" · ")));
      card.append(aliasLine);
    }

    const categories = cleanStringList(distributor.categories);
    if (categories.length > 0) {
      const categoryList = document.createElement("ul");
      categoryList.className = "badge-list";
      categoryList.setAttribute("aria-label", "Categories");
      for (const category of categories) {
        appendTextElement(categoryList, "li", "badge", category);
      }
      card.append(categoryList);
    }

    if (isNonEmptyString(distributor.description)) {
      appendTextElement(
        card,
        "p",
        "card-description",
        distributor.description,
      );
    }

    const facts = document.createElement("div");
    facts.className = "card-facts";

    if (Number.isInteger(distributor.founded_year)) {
      facts.append(
        createTextFact("Founded", String(distributor.founded_year)),
      );
    }

    const locations = Array.isArray(distributor.locations)
      ? distributor.locations
      : [];
    const locationValues = locations
      .map((location) => {
        if (!location || typeof location !== "object") {
          return "";
        }
        return [location.city, location.country]
          .filter(isNonEmptyString)
          .join(", ");
      })
      .filter(Boolean);
    if (locationValues.length > 0) {
      facts.append(createListFact("Locations", locationValues, "detail-list"));
    }

    const contacts = Array.isArray(distributor.contacts)
      ? distributor.contacts
      : [];
    const contactFact = createContactFact(contacts);
    if (contactFact) {
      facts.append(contactFact);
    }

    const updatedFact = createUpdatedFact(distributor.last_updated);
    if (updatedFact) {
      facts.append(updatedFact);
    }

    if (facts.childElementCount > 0) {
      card.append(facts);
    }

    return card;
  }

  function createTextFact(title, value) {
    const fact = createFactContainer(title);
    appendTextElement(fact, "p", "fact-value", value);
    return fact;
  }

  function createListFact(title, values, className) {
    const fact = createFactContainer(title);
    const list = document.createElement("ul");
    list.className = className;
    for (const value of values) {
      appendTextElement(list, "li", "", value);
    }
    fact.append(list);
    return fact;
  }

  function createContactFact(contacts) {
    const entries = [];

    for (const contact of contacts) {
      if (!contact || typeof contact !== "object") {
        continue;
      }

      const values = [];
      if (isNonEmptyString(contact.email)) {
        values.push(["Email", contact.email]);
      }
      if (isNonEmptyString(contact.phone)) {
        values.push(["Phone", contact.phone]);
      }
      if (values.length > 0) {
        entries.push(values);
      }
    }

    if (entries.length === 0) {
      return null;
    }

    const fact = createFactContainer(
      entries.length > 1 ? "Contacts" : "Contact",
    );
    const list = document.createElement("ul");
    list.className = "contact-list";

    for (const entry of entries) {
      const listItem = document.createElement("li");
      entry.forEach(([label, value], index) => {
        if (index > 0) {
          listItem.append(document.createTextNode(" · "));
        }
        appendTextElement(
          listItem,
          "span",
          "contact-label",
          `${label}: `,
        );
        listItem.append(document.createTextNode(value));
      });
      list.append(listItem);
    }

    fact.append(list);
    return fact;
  }

  function createUpdatedFact(lastUpdated) {
    if (!lastUpdated || typeof lastUpdated !== "object") {
      return null;
    }

    const rawValue = isNonEmptyString(lastUpdated.raw)
      ? lastUpdated.raw
      : "";
    const parsedDate = isNonEmptyString(lastUpdated.date)
      ? lastUpdated.date
      : "";
    if (!rawValue && !parsedDate) {
      return null;
    }

    const fact = createFactContainer("Last updated");
    fact.classList.add("fact-wide");

    const primaryValue = parsedDate ? formatDate(parsedDate) : rawValue;
    const valueLine = document.createElement("p");
    valueLine.className = "fact-value";
    if (parsedDate) {
      const time = document.createElement("time");
      time.dateTime = parsedDate;
      time.textContent = primaryValue;
      valueLine.append(time);
    } else {
      valueLine.textContent = primaryValue;
    }
    fact.append(valueLine);

    const contextParts = [];
    if (parsedDate && rawValue && rawValue !== parsedDate) {
      contextParts.push(`Source value: ${rawValue}`);
    }
    if (isNonEmptyString(lastUpdated.parse_status)) {
      const statusLabel = DATE_STATUS_LABELS[lastUpdated.parse_status];
      if (statusLabel) {
        contextParts.push(statusLabel);
      }
    }
    if (contextParts.length > 0) {
      appendTextElement(
        fact,
        "p",
        "updated-context",
        contextParts.join(" · "),
      );
    }

    return fact;
  }

  function createFactContainer(title) {
    const fact = document.createElement("section");
    fact.className = "fact";
    appendTextElement(fact, "h4", "fact-title", title);
    return fact;
  }

  function appendTextElement(parent, tagName, className, text) {
    const element = document.createElement(tagName);
    if (className) {
      element.className = className;
    }
    element.textContent = text;
    parent.append(element);
    return element;
  }

  function readApiError(payload, status) {
    if (
      payload
      && typeof payload === "object"
      && payload.error
      && typeof payload.error === "object"
      && isNonEmptyString(payload.error.message)
    ) {
      const detailMessages = Array.isArray(payload.error.details)
        ? payload.error.details
          .map((detail) => (
            detail && isNonEmptyString(detail.message)
              ? detail.message
              : ""
          ))
          .filter(Boolean)
        : [];
      return detailMessages.length > 0
        ? `${payload.error.message}: ${detailMessages.join("; ")}`
        : payload.error.message;
    }

    return `The directory request failed with status ${status}.`;
  }

  function isDistributorResponse(payload) {
    return Boolean(
      payload
      && typeof payload === "object"
      && Array.isArray(payload.items)
      && payload.pagination
      && typeof payload.pagination === "object"
      && Number.isInteger(payload.pagination.page)
      && Number.isInteger(payload.pagination.page_size)
      && Number.isInteger(payload.pagination.total_items)
      && Number.isInteger(payload.pagination.total_pages),
    );
  }

  function parsePositiveSafeInteger(value, fallback) {
    const parsed = Number(value);
    return Number.isSafeInteger(parsed) && parsed >= 1 ? parsed : fallback;
  }

  function cleanStringList(values) {
    return Array.isArray(values) ? values.filter(isNonEmptyString) : [];
  }

  function genuineAliases(distributorName, aliases) {
    const distributorNameKey = aliasComparisonKey(distributorName);
    const seenAliasKeys = new Set();
    const genuineAlternatives = [];

    for (const alias of cleanStringList(aliases)) {
      const aliasKey = aliasComparisonKey(alias);
      if (
        !aliasKey
        || aliasKey === distributorNameKey
        || seenAliasKeys.has(aliasKey)
      ) {
        continue;
      }

      seenAliasKeys.add(aliasKey);
      genuineAlternatives.push(alias);
    }

    return genuineAlternatives;
  }

  function aliasComparisonKey(value) {
    return isNonEmptyString(value)
      ? value
        .normalize("NFKC")
        .replace(/\s+/gu, " ")
        .trim()
        .toLowerCase()
      : "";
  }

  function isNonEmptyString(value) {
    return typeof value === "string" && value.trim().length > 0;
  }

  function formatNumber(value) {
    return new Intl.NumberFormat().format(value);
  }

  function formatDate(value) {
    const parsedDate = new Date(`${value}T00:00:00Z`);
    if (Number.isNaN(parsedDate.getTime())) {
      return value;
    }
    return new Intl.DateTimeFormat(undefined, {
      day: "numeric",
      month: "short",
      year: "numeric",
      timeZone: "UTC",
    }).format(parsedDate);
  }

  initialize();
})();
